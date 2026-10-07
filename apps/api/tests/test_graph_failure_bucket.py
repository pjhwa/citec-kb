from app.graph.extract import Edge
from app.graph.failure_bucket import extract_evidence


def _candidates(*rows):
    """pipeline.py가 넘기는 형태: external_id -> [{"document_id","evidence_grade"}, ...]"""
    out: dict[str, list[dict]] = {}
    for external_id, document_id, grade in rows:
        out.setdefault(external_id, []).append(
            {"document_id": document_id, "evidence_grade": grade}
        )
    return out


def test_extract_evidence_resolves_confluence_prefix():
    bucket = {"id": "fb1", "evidence_ref": "confluence:LOOKIN/2465855011#관측9"}
    index = _candidates(("2465855011", "doc-a", "A"))
    edges = extract_evidence(bucket, external_id_index=index)
    assert edges == [
        Edge(rel_type="HAS_EVIDENCE", target_label="Document", target_key="id",
             target_value="doc-a", tag="EXTRACTED")
    ]


def test_extract_evidence_prefers_higher_evidence_grade_on_ambiguity():
    bucket = {"id": "fb1", "evidence_ref": "confluence:LOOKIN/2465855011"}
    index = _candidates(
        ("2465855011", "doc-c-grade", "C"),
        ("2465855011", "doc-a-grade", "A"),
    )
    edges = extract_evidence(bucket, external_id_index=index)
    assert [e.target_value for e in edges] == ["doc-a-grade"]


def test_extract_evidence_resolves_citects_prefix():
    bucket = {"id": "fb1", "evidence_ref": "CITECTS-2481 참고"}
    index = _candidates(("citects-2481", "doc-b", "A"))
    edges = extract_evidence(bucket, external_id_index=index)
    assert [e.target_value for e in edges] == ["doc-b"]


def test_extract_evidence_resolves_bare_confluence_id_without_space_key():
    bucket = {"id": "fb1", "evidence_ref": "confluence:2412784426"}
    index = _candidates(("2412784426", "doc-bare", "A"))
    edges = extract_evidence(bucket, external_id_index=index)
    assert [e.target_value for e in edges] == ["doc-bare"]


def test_extract_evidence_resolves_multiple_confluence_refs_in_one_evidence_ref():
    bucket = {
        "id": "fb1",
        "evidence_ref": "confluence:LOOKIN/1111111111 (관측1), confluence:LOOKIN/2222222222 (관측2)",
    }
    index = _candidates(("1111111111", "doc-x", "A"), ("2222222222", "doc-y", "A"))
    edges = extract_evidence(bucket, external_id_index=index)
    assert sorted(e.target_value for e in edges) == ["doc-x", "doc-y"]


def test_extract_evidence_skips_non_document_prefixes():
    bucket = {"id": "fb1", "evidence_ref": "capture:CLOUD.pcap#frame=9985"}
    assert extract_evidence(bucket, external_id_index={}) == []


def test_extract_evidence_skips_legacy_placeholder():
    bucket = {"id": "fb1", "evidence_ref": "legacy:pre-migration"}
    assert extract_evidence(bucket, external_id_index={}) == []
