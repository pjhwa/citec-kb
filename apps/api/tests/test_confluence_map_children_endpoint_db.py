"""DB-touching tests for GET /v1/confluence-map/children. Same opt-in-DB
convention as test_confluence_map_inventory_db.py.
"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    yield
    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()


def _seed(session, *, external_id, space_key, path, is_folder):
    from app.db.models import Document

    meta = {"space_key": space_key, "경로": path, "유형": "폴더" if is_folder else "문서"}
    payload = f"{path}\n\n{json.dumps(meta, sort_keys=True)}"
    session.add(
        Document(
            id=f"test_children:{external_id}",
            source_type="confluence_map",
            external_id=external_id,
            title=path.rsplit(" > ", 1)[-1],
            body_md=path,
            metadata_=meta,
            content_hash=hashlib.sha256(payload.encode()).hexdigest(),
            evidence_grade="C",
            status="active",
        )
    )


def test_children_returns_only_direct_children_of_space_root():
    from app.db.session import session_scope
    from app.routers.confluence_map import get_children

    with session_scope() as session:
        _seed(session, external_id="1", space_key="TESTSPC", path="GitHub", is_folder=True)
        _seed(session, external_id="2", space_key="TESTSPC", path="GitHub > Q&A", is_folder=False)
        _seed(session, external_id="3", space_key="TESTSPC", path="GitHub > Q&A > deep", is_folder=False)
        _seed(session, external_id="4", space_key="TESTSPC", path="Other Root", is_folder=True)

    result = get_children(space_key="TESTSPC", parent_path="")
    ids = {item["page_id"] for item in result["items"]}
    assert ids == {"1", "4"}


def test_children_returns_only_direct_children_of_given_path():
    from app.db.session import session_scope
    from app.routers.confluence_map import get_children

    with session_scope() as session:
        _seed(session, external_id="11", space_key="TESTSPC2", path="GitHub", is_folder=True)
        _seed(session, external_id="12", space_key="TESTSPC2", path="GitHub > Q&A", is_folder=False)
        _seed(session, external_id="13", space_key="TESTSPC2", path="GitHub > Q&A > deep", is_folder=False)

    result = get_children(space_key="TESTSPC2", parent_path="GitHub")
    ids = {item["page_id"] for item in result["items"]}
    assert ids == {"12"}
