"""Pure-data tests for the confluence_map source seed used by the
20260922_0007 Alembic migration. No DB, no I/O — mirrors the old
MAP_SOURCE_DEFS shape tests that lived in test_confluence_map_sync.py
before that constant moved out of the runtime path (see
docs/superpowers/specs/2026-09-22-confluence-map-source-admin-design.md).
"""

from __future__ import annotations

from app.confluence.map_source_seed import SEED_MAP_SOURCE_DEFS


def test_seed_covers_all_twelve_approved_spaces():
    assert set(SEED_MAP_SOURCE_DEFS.keys()) == {
        "confluence_map_lookin",
        "confluence_map_techrepo",
        "confluence_map_serviceexcellenceteam",
        "confluence_map_icloudut",
        "confluence_map_devops001",
        "confluence_map_openstack101",
        "confluence_map_cldeng",
        "confluence_map_dftrts",
        "confluence_map_emcloud",
        "confluence_map_spc",
        "confluence_map_guid",
        "confluence_map_genaibusiness",
    }


def test_seed_each_have_own_space_key_and_roots_or_explicit_pages():
    space_keys = set()
    for source_id, sd in SEED_MAP_SOURCE_DEFS.items():
        assert sd["roots"] or sd.get("explicit_pages"), (
            f"{source_id} has neither roots nor explicit_pages"
        )
        space_keys.add(sd["space_key"])
    assert len(space_keys) == len(SEED_MAP_SOURCE_DEFS)


def test_seed_explicit_pages_match_known_page_ids():
    """SPC/GUID/genaibusiness's explicit_pages are the 6-page GitHub
    question gap-closure batch; DevOps001/Openstack101's are unrelated
    personal-workspace seeds. Both are asserted here for known page ids,
    not because they share a common scope/origin."""
    assert SEED_MAP_SOURCE_DEFS["confluence_map_spc"]["explicit_pages"].keys() == {
        "155680474", "383755011",
    }
    assert SEED_MAP_SOURCE_DEFS["confluence_map_guid"]["explicit_pages"].keys() == {
        "1488175558",
    }
    assert SEED_MAP_SOURCE_DEFS["confluence_map_genaibusiness"]["explicit_pages"].keys() == {
        "1268082455",
    }
    assert "1475349722" in SEED_MAP_SOURCE_DEFS["confluence_map_devops001"]["explicit_pages"]
    assert "2254661271" in SEED_MAP_SOURCE_DEFS["confluence_map_openstack101"]["explicit_pages"]
