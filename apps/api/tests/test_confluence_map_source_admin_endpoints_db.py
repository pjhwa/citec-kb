"""DB-touching tests for the confluence_map source admin endpoints
(POST /v1/confluence-map/sources, PATCH .../sources/{id}) and the
space_name/status fields added to GET /v1/confluence-map/status. Same
opt-in scratch-DB convention as test_confluence_map_inventory_db.py.
"""

from __future__ import annotations

import os

import pytest

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _TEST_DSN,
    reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL to a scratch Postgres DB to run these",
)

if _TEST_DSN:
    os.environ["DATABASE_URL"] = _TEST_DSN

_TEST_SOURCE_IDS = [
    "confluence_map_newspc",
    "confluence_map_expspc",
    "confluence_map_togspc",
]


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


@pytest.fixture(autouse=True)
def _cleanup_test_sources():
    yield
    from app.db.models import Source
    from app.db.session import session_scope

    with session_scope() as session:
        for source_id in _TEST_SOURCE_IDS:
            src = session.get(Source, source_id)
            if src is not None:
                session.delete(src)


def _admin():
    from app.auth.principal import Principal

    return Principal(sub="test-admin", name="test-admin", roles=frozenset({"admin"}))


def test_create_source_root_and_duplicate_conflicts():
    from fastapi import HTTPException
    from app.routers.confluence_map import CreateSourceBody, create_source, get_status

    body = CreateSourceBody(
        space_key="NEWSPC",
        space_name="새 팀 공간",
        page_id="900001",
        label="전체 공간 (New Space Home)",
        is_explicit_page=False,
    )
    result = create_source(body, principal=_admin())
    assert result["source_id"] == "confluence_map_newspc"

    status = get_status(principal=_admin())
    src = status["sources"]["confluence_map_newspc"]
    assert src["space_key"] == "NEWSPC"
    assert src["status"] == "active"

    with pytest.raises(HTTPException) as exc:
        create_source(body, principal=_admin())
    assert exc.value.status_code == 409


def test_create_source_explicit_page():
    from app.routers.confluence_map import CreateSourceBody, create_source
    from app.db.session import session_scope
    from app.db.models import Source

    body = CreateSourceBody(
        space_key="EXPSPC",
        space_name="explicit page space",
        page_id="900002",
        label="단일 페이지",
        is_explicit_page=True,
    )
    create_source(body, principal=_admin())

    with session_scope() as session:
        src = session.get(Source, "confluence_map_expspc")
        assert src.config["roots"] == {}
        assert src.config["explicit_pages"] == {"900002": "단일 페이지"}


def test_toggle_source_status_roundtrip():
    from fastapi import HTTPException
    from app.routers.confluence_map import (
        CreateSourceBody,
        UpdateSourceStatusBody,
        create_source,
        update_source_status,
    )

    create_source(
        CreateSourceBody(
            space_key="TOGSPC",
            space_name="toggle space",
            page_id="900003",
            label="root",
            is_explicit_page=False,
        ),
        principal=_admin(),
    )

    result = update_source_status(
        "confluence_map_togspc", UpdateSourceStatusBody(status="disabled"), principal=_admin()
    )
    assert result["status"] == "disabled"

    from app.confluence.map_sync import get_source_defs

    assert "confluence_map_togspc" not in get_source_defs(active_only=True)
    assert "confluence_map_togspc" in get_source_defs(active_only=False)

    result = update_source_status(
        "confluence_map_togspc", UpdateSourceStatusBody(status="active"), principal=_admin()
    )
    assert result["status"] == "active"

    with pytest.raises(HTTPException) as exc:
        update_source_status(
            "confluence_map_does_not_exist", UpdateSourceStatusBody(status="active"), principal=_admin()
        )
    assert exc.value.status_code == 404


def test_add_root_merges_and_is_idempotent():
    from fastapi import HTTPException

    from app.db.models import Source
    from app.db.session import session_scope
    from app.routers.confluence_map import (
        AddRootBody,
        CreateSourceBody,
        add_source_root,
        create_source,
    )

    create_source(
        CreateSourceBody(
            space_key="TOGSPC", space_name="x", page_id="1", label="첫 root"
        ),
        principal=_admin(),
    )
    sid = "confluence_map_togspc"
    with session_scope() as session:
        src = session.get(Source, sid)
        src.config = {**src.config, "checkpoint": {"1": {"start": 5}}}

    out = add_source_root(sid, AddRootBody(page_id="2", label="둘째"), principal=_admin())
    assert out["added"] is True and out["roots"] == {"1": "첫 root", "2": "둘째"}

    again = add_source_root(sid, AddRootBody(page_id="2", label="둘째"), principal=_admin())
    assert again["added"] is False and again["roots"] == {"1": "첫 root", "2": "둘째"}

    with session_scope() as session:
        cfg = session.get(Source, sid).config
        assert cfg["roots"] == {"1": "첫 root", "2": "둘째"}
        assert cfg["checkpoint"] == {"1": {"start": 5}}  # runtime state kept
        assert cfg["space_key"] == "TOGSPC"

    # create_source's own 409 for an existing space is unchanged
    with pytest.raises(HTTPException) as exc:
        create_source(
            CreateSourceBody(space_key="TOGSPC", space_name="x", page_id="3", label="l"),
            principal=_admin(),
        )
    assert exc.value.status_code == 409

    with pytest.raises(HTTPException) as nf:
        add_source_root("confluence_map_nope", AddRootBody(page_id="9", label="l"), principal=_admin())
    assert nf.value.status_code == 404
