"""Plan/apply tests for app.confluence.map_space_setup. The pure planner needs
no DB; the apply tests use the opt-in scratch DB and their own throwaway
source rows (never the real seeded ones)."""

from __future__ import annotations

import os

import pytest

from app.confluence import map_space_setup as m


def _info(key, name="N", ancestors=0, title="Home"):
    return {"space_key": key, "space_name": name, "title": title, "ancestors": ancestors}


CUR = {
    "confluence_map_openstack101": {
        "config": {
            "space_key": "Openstack101",
            "space_name": "OPENSTACK PLATFORM",
            "roots": {"1148203906": "knowledge base", "2318695720": "9. 팀 ISSUE 관리"},
            "explicit_pages": {"2254661271": "Squid"},
            "checkpoint": {"1148203906": {"start": 5}},
        },
        "status": "active",
    },
    "confluence_map_genaibusiness": {
        "config": {"space_key": "genaibusiness", "roots": {}, "explicit_pages": {"1": "x"}},
        "status": "active",
    },
    "confluence_map_lookin": {"config": {"space_key": "LOOKIN", "roots": {"222532724": "h"}}, "status": "active"},
}
HOMES = [("Openstack101", "361297136"), ("STORAGE", "644458153")]


def _plan(info, cur=CUR, homes=HOMES):
    return m.build_plan(cur, info, homes, ["confluence_map_genaibusiness"])


def test_existing_space_roots_are_replaced_and_new_space_created_and_retired_removed():
    actions, errors = _plan(
        {"361297136": _info("Openstack101"), "644458153": _info("STORAGE", "스토리지 공간")}
    )
    assert errors == []
    by = {a["source_id"]: a for a in actions}
    o = by["confluence_map_openstack101"]
    assert o["op"] == "set_roots"
    assert o["old_roots"] == CUR["confluence_map_openstack101"]["config"]["roots"]
    assert o["roots"] == {"361297136": "전체 공간 (OPENSTACK PLATFORM Home)"}  # existing name kept
    assert o["explicit_pages"] == {"2254661271": "Squid"}
    c = by["confluence_map_storage"]
    assert c["op"] == "create" and c["space_name"] == "스토리지 공간"
    assert c["roots"] == {"644458153": "전체 공간 (스토리지 공간 Home)"}
    assert by["confluence_map_genaibusiness"]["op"] == "remove"
    # full-space sources that were not listed are never touched
    assert "confluence_map_lookin" not in by
    assert m.target_source_ids(actions) == ["confluence_map_openstack101", "confluence_map_storage"]


def test_wrong_space_or_unreadable_page_is_an_error_not_a_plan_entry():
    actions, errors = _plan({"361297136": _info("SOMETHINGELSE")})
    assert len(errors) == 2  # mismatch for Openstack101, unreadable for STORAGE
    assert all(a["op"] == "remove" for a in actions)  # nothing else planned


def test_page_with_ancestors_warns():
    actions, errors = _plan(
        {"361297136": _info("Openstack101", ancestors=2, title="Sub"), "644458153": _info("STORAGE")}
    )
    assert errors == []
    o = next(a for a in actions if a["source_id"] == "confluence_map_openstack101")
    assert o["warnings"] and "not the space's top page" in o["warnings"][0]


def test_rerun_after_apply_is_unchanged():
    cur = {
        "confluence_map_openstack101": {
            "config": {
                "space_key": "Openstack101",
                "space_name": "OPENSTACK PLATFORM",
                "roots": {"361297136": "전체 공간 (OPENSTACK PLATFORM Home)"},
            },
            "status": "active",
        }
    }
    actions, _ = m.build_plan(cur, {"361297136": _info("Openstack101")}, [("Openstack101", "361297136")], [])
    assert [a["op"] for a in actions] == ["unchanged"]
    assert m.target_source_ids(actions) == []


def test_existing_source_with_different_space_key_is_refused():
    cur = {"confluence_map_openstack101": {"config": {"space_key": "OTHER", "roots": {}}, "status": "active"}}
    _, errors = m.build_plan(cur, {"361297136": _info("Openstack101")}, [("Openstack101", "361297136")], [])
    assert errors and "refusing" in errors[0]


def test_real_target_list_matches_the_request():
    keys = {k for k, _ in m.SPACE_HOMES}
    assert keys == {
        "DevOps001", "Openstack101", "sysops", "CLDENG", "EMCloud", "DFTRTS", "GUID",
        "STORAGE", "SCPTechTree", "CATT", "SI",
    }
    assert dict(m.SPACE_HOMES)["Openstack101"] == "361297136"
    assert not keys & {"LOOKIN", "TechRepo", "ICLOUDUT", "ServiceExcellenceTeam", "SPC"}
    assert m.REMOVE_SOURCES == ["confluence_map_genaibusiness"]


_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")


@pytest.mark.skipif(not _TEST_DSN, reason="set CONFLUENCE_SYNC_TEST_DATABASE_URL")
def test_apply_writes_sources_seeds_cursor_and_archives_removed_space_docs():
    os.environ["DATABASE_URL"] = _TEST_DSN
    from app.db import session as db_session
    from app.settings import get_settings

    db_session.get_engine.cache_clear()
    db_session.get_session_factory.cache_clear()
    get_settings.cache_clear()
    from app.db.models import Document, Source
    from app.db.session import session_scope

    ids = ["confluence_map_setupupd", "confluence_map_setupnew", "confluence_map_setupgone"]
    try:
        with session_scope() as s:
            s.add(Source(id=ids[0], type="confluence_map", name="u", status="active",
                         config={"space_key": "SETUPUPD", "space_name": "Upd", "roots": {"9": "old"},
                                 "explicit_pages": {"5": "e"}, "checkpoint": {"9": {"start": 3}}}))
            s.add(Source(id=ids[2], type="confluence_map", name="g", status="active",
                         config={"space_key": "SETUPGONE", "roots": {}}))
            s.add(Document(id="test_setup:gone", source_type="confluence_map", external_id="setup_gone_1",
                           title="t", body_md="b", metadata_={"space_key": "SETUPGONE"},
                           content_hash="h", evidence_grade="C", status="active"))
        homes = [("SETUPUPD", "100"), ("SETUPNEW", "200")]
        info = {"100": _info("SETUPUPD"), "200": _info("SETUPNEW", "New Space")}
        cur = {k: v for k, v in m.load_current().items() if k in ids}
        actions, errors = m.build_plan(cur, info, homes, [ids[2]])
        assert errors == []
        m.apply_plan(actions)

        with session_scope() as s:
            upd = s.get(Source, ids[0])
            assert upd.config["roots"] == {"100": "전체 공간 (Upd Home)"}
            assert upd.config["explicit_pages"] == {"5": "e"} and "checkpoint" not in upd.config
            new = s.get(Source, ids[1])
            assert new.status == "active" and new.last_sync_at is not None
            assert new.config["space_name"] == "New Space"
            assert s.get(Source, ids[2]) is None
            assert s.get(Document, "test_setup:gone").status == "archived"

        # second run: nothing left to do
        cur = {k: v for k, v in m.load_current().items() if k in ids}
        actions, _ = m.build_plan(cur, info, homes, [ids[2]])
        assert m.target_source_ids(actions) == []
    finally:
        with session_scope() as s:
            for i in ids:
                src = s.get(Source, i)
                if src:
                    s.delete(src)
            s.query(Document).filter(Document.id == "test_setup:gone").delete()
