from app.doc_access import attach_document_access, document_access, document_path


def test_document_path():
    assert document_path(external_id="CITECTS-1", source_type="support_history") == (
        "support_history/CITECTS-1.md"
    )


def test_document_access_fields():
    acc = document_access(
        external_id="CITECTS-1",
        source_type="support_history",
        title="t",
        absolute=True,
    )
    assert acc["path"] == "support_history/CITECTS-1.md"
    assert "CITECTS-1" in acc["body_api"]
    assert acc["mcp_tool"] == "kb_get_document"
    assert acc["mcp_args"]["path"].endswith("CITECTS-1.md")
    assert "web_url" in acc
    assert "body_api_url" in acc


def test_attach_document_access():
    item = {"external_id": "X", "source_type": "tech_repo", "title": "hi"}
    out = attach_document_access(item)
    assert out["path"] == "tech_repo/X.md"
    assert out["body_api"]
    assert out["access"]["mcp_tool"] == "kb_get_document"
