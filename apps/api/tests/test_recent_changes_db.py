"""DB tests for app.documents.recent_changes — scratch DB only (never the live
citec_knowledge DB), same opt-in env var as test_confluence_sync_db.py."""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

TAG = "rc_test_"  # external_id prefix so cleanup never touches other rows


@pytest.fixture(autouse=True)
def _db():
    from app.db import session as db_session
    from app.db.models import Document
    from app.db.session import session_scope
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    with session_scope() as s:
        s.query(Document).filter(Document.external_id.like(TAG + "%")).delete(synchronize_session=False)
    yield
    with session_scope() as s:
        s.query(Document).filter(Document.external_id.like(TAG + "%")).delete(synchronize_session=False)
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _add(ext, *, source_type="confluence_docs", title="T", meta=None, created=None, status="active"):
    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as s:
        s.add(
            Document(
                id=uuid.uuid4().hex,
                source_type=source_type,
                external_id=TAG + ext,
                title=title,
                body_md="x",
                metadata_=meta or {},
                content_hash=uuid.uuid4().hex,
                status=status,
                source_uri=f"https://c.example.com/{ext}",
                created_at=created or datetime(2020, 1, 1, tzinfo=timezone.utc),
            )
        )


def _ids(res):
    return [i["page_id"].removeprefix(TAG) for i in res["items"]]


def _call(**kw):
    from app.documents.recent_changes import list_recent_changes

    kw.setdefault("date_from", date(2026, 10, 10))
    kw.setdefault("date_to", date(2026, 10, 12))
    return list_recent_changes(**kw)


def test_modified_range_is_kst_inclusive_and_sorted_desc():
    _add("in_a", meta={"source_modified_at": "2026-10-10T00:00:00.000+09:00", "공간명": "LOOKIN"})
    _add("in_b", meta={"source_modified_at": "2026-10-12T23:59:59.000+09:00", "공간명": "LOOKIN"})
    _add("before", meta={"source_modified_at": "2026-10-09T23:59:59.000+09:00"})
    _add("after", meta={"source_modified_at": "2026-10-13T00:00:00.000+09:00"})
    r = _call()
    assert _ids(r) == ["in_b", "in_a"]
    assert r["total"] == 2
    assert all(i["change"] == "modified" for i in r["items"])


def test_utc_offset_is_respected():
    # 2026-10-09T16:00:00Z == 2026-10-10 01:00 KST → inside
    _add("utc_in", meta={"source_modified_at": "2026-10-09T16:00:00.000+00:00"})
    # 2026-10-09T14:59:59Z == 2026-10-09 23:59:59 KST → outside
    _add("utc_out", meta={"source_modified_at": "2026-10-09T14:59:59.000+00:00"})
    assert _ids(_call()) == ["utc_in"]


def test_added_uses_created_at_and_all_is_union():
    _add("new", created=datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc))
    _add("edited", meta={"source_modified_at": "2026-10-11T10:00:00.000+09:00"})
    assert _ids(_call(change_type="added")) == ["new"]
    assert _ids(_call(change_type="modified")) == ["edited"]
    r = _call(change_type="all")
    assert set(_ids(r)) == {"new", "edited"}
    by = {i["page_id"].removeprefix(TAG): i["change"] for i in r["items"]}
    assert by == {"new": "added", "edited": "modified"}


def test_both_flag_when_new_and_edited_in_range():
    _add("both", meta={"source_modified_at": "2026-10-11T10:00:00.000+09:00"},
         created=datetime(2026, 10, 11, 3, 0, tzinfo=timezone.utc))
    r = _call(change_type="all")
    assert r["items"][0]["change"] == "both"


def test_missing_and_malformed_modified_at_excluded_and_counted():
    _add("none", meta={})
    _add("bad", meta={"source_modified_at": "not-a-date"})
    _add("bad_month", meta={"source_modified_at": "2026-13-01T00:00:00+09:00"})
    _add("ok", meta={"source_modified_at": "2026-10-11T00:00:00+09:00"})
    r = _call()
    assert _ids(r) == ["ok"]  # no exception from the bad rows
    assert r["missing_modified_at"] >= 3


def test_archived_documents_excluded():
    _add("arch", status="archived", meta={"source_modified_at": "2026-10-11T00:00:00+09:00"})
    assert _ids(_call()) == []


def test_other_source_types_never_returned():
    _add("tk", source_type="support_history", meta={"source_modified_at": "2026-10-11T00:00:00+09:00"})
    assert _ids(_call()) == []


def test_source_types_filter():
    m = {"source_modified_at": "2026-10-11T00:00:00+09:00"}
    _add("d", source_type="confluence_docs", meta=m)
    _add("t", source_type="tech_repo", meta=m)
    assert _ids(_call(source_types=["tech_repo"])) == ["t"]


def test_folders_excluded_by_default_included_on_request():
    m = {"source_modified_at": "2026-10-11T00:00:00+09:00", "space_key": "SK"}
    _add("folder", source_type="confluence_map", meta={**m, "유형": "폴더"})
    _add("doc", source_type="confluence_map", meta={**m, "유형": "문서"})
    assert _ids(_call()) == ["doc"]
    assert set(_ids(_call(include_folders=True))) == {"doc", "folder"}


def test_map_doc_without_type_key_is_not_silently_dropped():
    """회귀: `NOT (map AND 유형='폴더')`는 유형 키가 없으면 NULL이 되어 문서가 사라졌다."""
    _add("legacy", source_type="confluence_map", meta={"source_modified_at": "2026-10-11T00:00:00+09:00"})
    assert _ids(_call()) == ["legacy"]


def test_space_key_filter_map_vs_docs_and_case_insensitive():
    t = "2026-10-11T00:00:00+09:00"
    _add("map_a", source_type="confluence_map", meta={"source_modified_at": t, "space_key": "DevOps001", "공간명": "SCP인프라운영팀"})
    _add("docs_a", source_type="confluence_docs", meta={"source_modified_at": t, "공간명": "LOOKIN"})
    _add("tech_a", source_type="tech_repo", meta={"source_modified_at": t, "공간명": "TechRepo"})
    assert _ids(_call(space_key="devops001")) == ["map_a"]
    assert _ids(_call(space_key="lookin")) == ["docs_a"]
    assert _ids(_call(space_key="techrepo")) == ["tech_a"]
    # confluence_map's display name must NOT match as a key
    assert _ids(_call(space_key="SCP인프라운영팀")) == []


def test_title_contains_is_literal_and_case_insensitive():
    m = {"source_modified_at": "2026-10-11T00:00:00+09:00"}
    _add("a", title="Redis 100%_장애 가이드", meta=m)
    _add("b", title="Other", meta=m)
    assert _ids(_call(title_contains="redis")) == ["a"]
    assert _ids(_call(title_contains="100%_")) == ["a"]  # % and _ are literal
    assert _ids(_call(title_contains="%")) == ["a"]


def test_paging_and_total():
    for i in range(5):
        _add(f"p{i}", meta={"source_modified_at": f"2026-10-11T0{i}:00:00+09:00"})
    r1 = _call(limit=2, offset=0)
    r2 = _call(limit=2, offset=2)
    r3 = _call(limit=2, offset=4)
    assert r1["total"] == 5
    assert _ids(r1) == ["p4", "p3"] and _ids(r2) == ["p2", "p1"] and _ids(r3) == ["p0"]


def test_item_fields_per_source_type():
    t = "2026-10-11T00:00:00+09:00"
    _add("m", source_type="confluence_map", title="M",
         meta={"source_modified_at": t, "space_key": "SK", "경로": "A > B", "source_version": 7})
    _add("c", source_type="confluence_docs", title="C",
         meta={"source_modified_at": t, "공간명": "LOOKIN", "폴더분류": "오픈스택"})
    _add("r", source_type="tech_repo", title="R",
         meta={"source_modified_at": t, "공간명": "TechRepo", "디렉토리": "TechRepo > X"})
    items = {i["page_id"].removeprefix(TAG): i for i in _call()["items"]}
    assert (items["m"]["space_key"], items["m"]["path"], items["m"]["source_version"]) == ("SK", "A > B", 7)
    assert (items["c"]["space_key"], items["c"]["path"]) == ("LOOKIN", "오픈스택")
    assert (items["r"]["space_key"], items["r"]["path"]) == ("TechRepo", "TechRepo > X")
    assert items["m"]["url"] == "https://c.example.com/m"
    assert items["m"]["source_modified_at"] == t


def test_invalid_inputs_raise_value_error():
    from app.documents.recent_changes import list_recent_changes

    with pytest.raises(ValueError):
        list_recent_changes(date_from=date(2026, 10, 12), date_to=date(2026, 10, 1))
    with pytest.raises(ValueError):
        list_recent_changes(date_from=date(2026, 10, 1), date_to=date(2026, 10, 2), change_type="zzz")
    with pytest.raises(ValueError):
        list_recent_changes(date_from=date(2026, 10, 1), date_to=date(2026, 10, 2), source_types=["support_history"])


# ── API 레이어 (TestClient) ───────────────────────────────────────────────


def _client():
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def test_api_default_is_last_7_days_and_label():
    from app.query.time_range import KST

    now = datetime.now(KST).isoformat(timespec="seconds")
    _add("fresh", meta={"source_modified_at": now})
    _add("old", meta={"source_modified_at": "2020-01-01T00:00:00+09:00"})
    r = _client().get("/v1/documents/recent-changes")
    assert r.status_code == 200
    body = r.json()
    ids = [i["page_id"].removeprefix(TAG) for i in body["items"] if i["page_id"].startswith(TAG)]
    assert ids == ["fresh"]
    assert body["range"]["label"] == "최근 7일"


def test_api_relative_and_explicit_dates_and_source_types():
    _add("a", source_type="tech_repo", meta={"source_modified_at": "2026-10-11T00:00:00+09:00"})
    r = _client().get("/v1/documents/recent-changes",
                      params={"date_from": "2026-10-10", "date_to": "2026-10-12", "source_types": "tech_repo"})
    assert r.status_code == 200
    assert [i["page_id"] for i in r.json()["items"] if i["page_id"].startswith(TAG)] == [TAG + "a"]


def test_api_single_date_means_that_day():
    _add("a", meta={"source_modified_at": "2026-10-11T12:00:00+09:00"})
    _add("b", meta={"source_modified_at": "2026-10-12T12:00:00+09:00"})
    r = _client().get("/v1/documents/recent-changes", params={"date_from": "2026-10-11"})
    ids = [i["page_id"].removeprefix(TAG) for i in r.json()["items"] if i["page_id"].startswith(TAG)]
    assert ids == ["a"]


@pytest.mark.parametrize(
    "params",
    [
        {"relative": "의미불명의기간"},
        {"date_from": "2026-10-12", "date_to": "2026-10-01"},
        {"change_type": "zzz"},
        {"source_types": "support_history"},
        {"limit": 0},
        {"limit": 201},
    ],
)
def test_api_invalid_inputs_are_4xx(params):
    r = _client().get("/v1/documents/recent-changes", params=params)
    assert r.status_code in (400, 422)
