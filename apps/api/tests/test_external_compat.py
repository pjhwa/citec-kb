"""Unit tests for wiki-qa external compat helpers (no live LLM)."""

import pytest
from fastapi import HTTPException

from app.ingest.adapters import parse_incident_report_file
from app.routers.external_compat import (
    _map_section,
    _resolve_upload_source_type,
    _safe_upload_filename,
    _SECTION_MAP,
    _UPLOAD_PARSERS,
    _UPLOAD_RAW_SUBDIR,
    _validate_upload_extension,
    _VERDICT_RATING,
)


def test_section_map_checkitems():
    assert _map_section("checkitems") == "checkitem"
    assert _map_section("support_history") == "support_history"
    assert _map_section("incident_reports") == "incident_reports"
    assert _map_section("synthesis") == "insight"
    assert _map_section("general") is None
    assert _map_section("") is None


def test_section_passthrough():
    assert _map_section("tech_repo") == "tech_repo"
    assert _map_section("tuning_ai") == "tuning_ai"


def test_section_map_incident_reports_not_aliased_to_support_history():
    """Regression: _SECTION_MAP used to map incident_reports -> support_history
    (stale, predates the Phase 1 SWIM source_type). That silently made
    GET /api/wiki/search?section=incident_reports and
    POST /api/query {"template": "incident_reports"} (wiki_ask/kb_ask compat)
    search support_history instead of SWIM.
    """
    assert _map_section("incident_reports") == "incident_reports"


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


def test_resolve_upload_source_type_aliases():
    assert _resolve_upload_source_type("support_history") == "support_history"
    assert _resolve_upload_source_type("support") == "support_history"
    assert _resolve_upload_source_type("incident_reports") == "incident_reports"
    assert _resolve_upload_source_type("incident") == "incident_reports"
    assert _resolve_upload_source_type("tech_repo") == "tech_repo"
    assert _resolve_upload_source_type("confluence_docs") == "tech_repo"
    assert _resolve_upload_source_type("confluence") == "tech_repo"
    assert _resolve_upload_source_type("techrepo") == "tech_repo"
    assert _resolve_upload_source_type("tech-repo") == "tech_repo"
    assert _resolve_upload_source_type("tuning_ai") == "tuning_ai"
    assert _resolve_upload_source_type("sql_tuning") == "tuning_ai"
    assert _resolve_upload_source_type("sql") == "tuning_ai"
    assert _resolve_upload_source_type("issue_analysis") == "tuning_ai"
    assert _resolve_upload_source_type("dbms_tuning") == "tuning_ai"
    assert _resolve_upload_source_type("dbms-tuning") == "tuning_ai"
    assert _resolve_upload_source_type("tuning-ai") == "tuning_ai"


def test_resolve_upload_source_type_default_is_support_history():
    assert _resolve_upload_source_type("") == "support_history"


def test_incident_reports_upload_wired_to_native_swim_parser():
    """Regression: incident_reports used to alias to support_history and get
    parsed by parse_support_history_file (Jira "# Title" shape) — silently
    mis-filing SWIM uploads under source_type=support_history with a garbage
    title. Confirmed on the running stack before this fix (POST /api/upload
    with source_type=incident_reports landed as
    documents.source_type='support_history', title=filename stem).
    """
    internal = _resolve_upload_source_type("incident_reports")
    assert internal == "incident_reports"
    assert _UPLOAD_PARSERS[internal] is parse_incident_report_file
    assert _UPLOAD_RAW_SUBDIR[internal] == "incident_reports"


def test_resolve_upload_source_type_unknown_is_400():
    with pytest.raises(HTTPException) as exc:
        _resolve_upload_source_type("totally-bogus-type")
    assert exc.value.status_code == 400


def test_resolve_upload_source_type_known_unimplemented_is_501():
    with pytest.raises(HTTPException) as exc:
        _resolve_upload_source_type("vendor_docs")
    assert exc.value.status_code == 501

    with pytest.raises(HTTPException) as exc:
        _resolve_upload_source_type("checkitems")
    assert exc.value.status_code == 501


def test_safe_upload_filename_strips_path_traversal():
    assert _safe_upload_filename("../../etc/passwd") == "passwd"
    assert _safe_upload_filename("a/b/c.md") == "c.md"
    assert _safe_upload_filename("CITECTS-1234.md") == "CITECTS-1234.md"


def test_safe_upload_filename_rejects_empty():
    with pytest.raises(HTTPException) as exc:
        _safe_upload_filename("")
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        _safe_upload_filename(None)
    assert exc.value.status_code == 400


def test_validate_upload_extension_accepts_md_txt():
    _validate_upload_extension("foo.md")
    _validate_upload_extension("foo.txt")


def test_validate_upload_extension_xls_is_501():
    with pytest.raises(HTTPException) as exc:
        _validate_upload_extension("checkitems.xlsx")
    assert exc.value.status_code == 501


def test_validate_upload_extension_unknown_is_400():
    with pytest.raises(HTTPException) as exc:
        _validate_upload_extension("foo.pdf")
    assert exc.value.status_code == 400
