"""Unit tests for app.tickets.query helpers (no DB)."""

from app.tickets.query import resolve_date_field


def test_resolve_date_field_support_history_passthrough():
    assert resolve_date_field("support_history", "Created") == "Created"
    assert resolve_date_field("support_history", "Resolved") == "Resolved"
    assert resolve_date_field("support_history", "Updated") == "Updated"


def test_resolve_date_field_support_history_default_on_invalid():
    assert resolve_date_field("support_history", None) == "Created"
    assert resolve_date_field("support_history", "발생일시(한국)") == "Created"


def test_resolve_date_field_incident_reports_swim_key():
    """Phase 2 fix: SWIM has no Created/Resolved/Updated — its date key is
    발생일시(한국). A caller passing the Jira default "Created" through must not
    silently filter out every incident_reports row.
    """
    assert resolve_date_field("incident_reports", None) == "발생일시(한국)"
    assert resolve_date_field("incident_reports", "Created") == "발생일시(한국)"
    assert resolve_date_field("incident_reports", "발생일시(한국)") == "발생일시(한국)"


def test_resolve_date_field_unknown_source_type_falls_back_to_jira_default():
    assert resolve_date_field("tech_repo", "Created") == "Created"
    assert resolve_date_field("tech_repo", None) == "Created"
