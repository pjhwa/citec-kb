"""A2 — entity_share: matched/total are counted on the same scope (scratch DB only)."""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres+pgvector DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

TAG = "es_test_"


@pytest.fixture(autouse=True)
def _db():
    from app.db import session as db_session
    from app.db.models import Document, DocumentEntity, Entity
    from app.db.session import session_scope
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()

    def _wipe():
        with session_scope() as s:
            ids = [r[0] for r in s.query(Document.id).filter(Document.external_id.like(TAG + "%"))]
            if ids:
                s.query(DocumentEntity).filter(DocumentEntity.document_id.in_(ids)).delete(synchronize_session=False)
            s.query(Document).filter(Document.external_id.like(TAG + "%")).delete(synchronize_session=False)
            s.query(Entity).filter(Entity.id.like("es_test:%")).delete(synchronize_session=False)

    _wipe()
    yield
    _wipe()
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _add(ext, *, title, created, source_type="support_history"):
    from app.db.models import Document
    from app.db.session import session_scope

    doc_id = uuid.uuid4().hex
    with session_scope() as s:
        s.add(
            Document(
                id=doc_id, source_type=source_type, external_id=TAG + ext, title=title,
                body_md="본문", metadata_={"Created": created}, content_hash=uuid.uuid4().hex,
                status="active", source_uri=f"https://x/{ext}",
            )
        )
    return doc_id


def test_matched_never_exceeds_total_and_equals_aggregate():
    from app.analytics.aggregate import aggregate_tickets, entity_share

    _add("1", title="SCP 장애", created="2026-03-01")
    _add("2", title="SCP 지연", created="2026-04-01")
    _add("3", title="Redis 장애", created="2026-05-01")
    _add("4", title="SCP 작년 건", created="2025-06-01")
    kw = dict(date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    r = entity_share(entity="scp", **kw)
    assert (r["matched"], r["total"]) == (2, 3)
    assert r["share"] == round(2 / 3, 4)
    a = aggregate_tickets(group_by="total", entity="scp", **kw)
    assert r["matched"] == a["total"]  # kb_analytics(entity=) 와 같은 정의


def test_entity_links_outside_range_do_not_inflate_matched():
    """예전 버그: document_entities 링크 수는 기간 필터가 없어 matched > total 이 됐다."""
    from app.analytics.aggregate import entity_share
    from app.db.models import DocumentEntity, Entity
    from app.db.session import session_scope

    old1 = _add("o1", title="SCP 옛날 1", created="2020-01-01")
    old2 = _add("o2", title="SCP 옛날 2", created="2020-02-01")
    _add("n1", title="SCP 올해", created="2026-02-01")
    with session_scope() as s:
        s.add(Entity(id="es_test:scp", canonical_name="SCPX", aliases=["scp"]))
        s.flush()
        for d in (old1, old2):
            s.add(DocumentEntity(document_id=d, entity_id="es_test:scp"))
    r = entity_share(entity="scp", date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    assert r["matched"] <= r["total"]
    assert (r["matched"], r["total"]) == (1, 1)
    assert r["method"] == "title_entity_scan"


def test_no_range_and_no_match():
    from app.analytics.aggregate import entity_share

    _add("1", title="Redis", created="2026-01-01")
    r = entity_share(entity="없는엔티티")
    assert (r["matched"], r["total"], r["share"]) == (0, 1, 0.0)
    assert entity_share(entity="  ")["matched"] == 0
