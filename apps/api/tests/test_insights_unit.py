from app.insights.service import TRANSITIONS, VALID_STATUS, _insight_draft
from app.db.models import Insight


def test_insight_status_machine():
    assert "draft" in VALID_STATUS
    assert "review" in VALID_STATUS
    assert "approved" in VALID_STATUS
    assert "rejected" in VALID_STATUS
    assert "review" in TRANSITIONS["draft"]
    assert "rejected" in TRANSITIONS["draft"]
    assert "approved" in TRANSITIONS["review"]
    assert "rejected" in TRANSITIONS["review"]
    assert "draft" in TRANSITIONS["review"]
    assert TRANSITIONS["approved"] == set()
    assert "draft" in TRANSITIONS["rejected"]


def test_no_escape_from_approved():
    assert not TRANSITIONS["approved"]


def test_insight_draft_hash_and_body_fallback():
    row = Insight(
        id="abcdef12-3456-7890-abcd-ef1234567890",
        title="UniqueTokenZebraHang",
        body_md="",
        status="approved",
        author="pilot",
        reviewer="senior",
    )
    draft = _insight_draft(row)
    assert draft.source_type == "insight"
    assert draft.external_id == "INSIGHT-abcdef12"
    assert draft.body_md == "UniqueTokenZebraHang"
    assert draft.content_hash
    assert draft.metadata["insight_id"] == row.id
    assert draft.evidence_grade == "draft"
