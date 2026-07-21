"""Unit tests for wiki-qa external compat helpers (no live LLM)."""

from app.routers.external_compat import _map_section, _SECTION_MAP, _VERDICT_RATING


def test_section_map_checkitems():
    assert _map_section("checkitems") == "checkitem"
    assert _map_section("support_history") == "support_history"
    assert _map_section("incident_reports") == "support_history"
    assert _map_section("synthesis") == "insight"
    assert _map_section("general") is None
    assert _map_section("") is None


def test_section_passthrough():
    assert _map_section("tech_repo") == "tech_repo"
    assert _map_section("tuning_ai") == "tuning_ai"


def test_verdict_rating():
    assert _VERDICT_RATING["helpful"] == 1
    assert _VERDICT_RATING["not_helpful"] == -1
    assert _VERDICT_RATING["resolved"] == 1


def test_section_map_keys_cover_mcp_templates():
    for t in (
        "checkitems",
        "support_history",
        "incident_reports",
        "vendor_docs",
        "tech_repo",
        "tuning_ai",
        "synthesis",
    ):
        assert t in _SECTION_MAP
