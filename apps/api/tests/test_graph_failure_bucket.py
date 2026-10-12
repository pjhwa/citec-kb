from app.graph.extract import Edge
from app.graph.failure_bucket import extract_evidence, extract_similar_to


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


def test_extract_similar_to_links_above_threshold():
    bucket = {
        "id": "fb1", "bucket_name": "b1", "symptom": "",
        "discriminating_signals": ["RST 직전 idle 60초 이상"],
    }
    others = [
        {"id": "fb2", "bucket_name": "b2", "confidence": 0.9,
         "discriminating_signals": ["RST 직전 idle 60초 이상"], "counter_signals": []},
    ]
    edges = extract_similar_to(bucket, other_buckets=others)
    assert len(edges) == 1
    assert edges[0].rel_type == "SIMILAR_TO"
    assert edges[0].target_label == "FailureBucket"
    assert edges[0].target_value == "fb2"
    assert edges[0].tag == "INFERRED"


def test_extract_similar_to_excludes_self():
    bucket = {"id": "fb1", "bucket_name": "b1", "symptom": "", "discriminating_signals": ["x"]}
    edges = extract_similar_to(bucket, other_buckets=[bucket])
    assert edges == []


def test_extract_similar_to_below_threshold_returns_empty():
    bucket = {"id": "fb1", "bucket_name": "b1", "symptom": "", "discriminating_signals": ["x"]}
    others = [
        {"id": "fb2", "bucket_name": "b2", "confidence": 0.1,
         "discriminating_signals": ["완전히 무관"], "counter_signals": []},
    ]
    assert extract_similar_to(bucket, other_buckets=others) == []


def test_decode_confluence_tiny_link_known_pairs():
    from app.graph.failure_bucket import decode_confluence_tiny_link

    assert decode_confluence_tiny_link("9uAFJ") == 604365046
    assert decode_confluence_tiny_link("PVSFlw") == 2542097469


def test_decode_confluence_tiny_link_rejects_url_safe_chars():
    from app.graph.failure_bucket import decode_confluence_tiny_link

    assert decode_confluence_tiny_link("abc-def") is None
    assert decode_confluence_tiny_link("abc_def") is None
    assert decode_confluence_tiny_link("") is None


def test_extract_mentioned_references_resolves_tiny_link_in_recommended_action():
    from app.graph.failure_bucket import extract_mentioned_references

    bucket = {
        "id": "fb1", "root_cause": "...",
        "recommended_action": (
            "정본 선택과 트랜잭션 정합성을 확인해야 한다. "
            "분석 원문: https://devops.sdsdev.co.kr/confluence/x/PVSFlw"
        ),
    }
    index = _candidates(("2542097469", "confluence_map:2542097469", "C"))
    edges = extract_mentioned_references(bucket, page_identity_index=index)
    assert edges == [
        Edge(rel_type="REFERENCES", target_label="Document", target_key="id",
             target_value="confluence_map:2542097469", tag="EXTRACTED")
    ]


def test_extract_mentioned_references_target_not_ingested_is_silently_skipped():
    from app.graph.failure_bucket import extract_mentioned_references

    bucket = {
        "id": "fb1", "root_cause": "",
        "recommended_action": "분석 원문: https://devops.sdsdev.co.kr/confluence/x/PVSFlw",
    }
    assert extract_mentioned_references(bucket, page_identity_index={}) == []


def test_extract_mentioned_references_ignores_non_confluence_host():
    from app.graph.failure_bucket import extract_mentioned_references

    bucket = {"id": "fb1", "root_cause": "see https://example.com/x/PVSFlw", "recommended_action": ""}
    index = _candidates(("2542097469", "doc-a", "A"))
    assert extract_mentioned_references(bucket, page_identity_index=index) == []


def test_extract_mentioned_references_resolves_pageid_query_param():
    from app.graph.failure_bucket import extract_mentioned_references

    bucket = {
        "id": "fb1", "root_cause": "",
        "recommended_action": "https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=2542097469",
    }
    index = _candidates(("2542097469", "confluence_map:2542097469", "C"))
    edges = extract_mentioned_references(bucket, page_identity_index=index)
    assert [e.target_value for e in edges] == ["confluence_map:2542097469"]


def test_extract_mentioned_references_empty_text_returns_empty():
    from app.graph.failure_bucket import extract_mentioned_references

    assert extract_mentioned_references({"id": "fb1"}, page_identity_index={}) == []


def test_extract_mentioned_references_rejects_url_safe_code_end_to_end():
    # 회귀: 과거 캡처 정규식이 [A-Za-z0-9]+만 잡아 "9uA-FJ"가 "9uA"로 잘려
    # 들어가면 거부 로직을 안 거치고 엉뚱한 page id로 디코딩될 뻔했다.
    from app.graph.failure_bucket import extract_mentioned_references

    bucket = {"id": "fb1", "root_cause": "",
              "recommended_action": "참고: https://devops.sdsdev.co.kr/confluence/x/9uA-FJ"}
    index = _candidates(("604365046", "confluence_map:604365046", "C"))
    assert extract_mentioned_references(bucket, page_identity_index=index) == []


def test_extract_mentioned_references_resolves_with_trailing_punctuation():
    from app.graph.failure_bucket import extract_mentioned_references

    bucket = {"id": "fb1", "root_cause": "",
              "recommended_action": "분석 원문(https://devops.sdsdev.co.kr/confluence/x/PVSFlw)."}
    index = _candidates(("2542097469", "confluence_map:2542097469", "C"))
    edges = extract_mentioned_references(bucket, page_identity_index=index)
    assert [e.target_value for e in edges] == ["confluence_map:2542097469"]


def test_extract_mentioned_references_resolves_with_trailing_korean_particle():
    from app.graph.failure_bucket import extract_mentioned_references

    bucket = {"id": "fb1", "root_cause": "",
              "recommended_action": "https://devops.sdsdev.co.kr/confluence/x/PVSFlw에서 확인"}
    index = _candidates(("2542097469", "confluence_map:2542097469", "C"))
    edges = extract_mentioned_references(bucket, page_identity_index=index)
    assert [e.target_value for e in edges] == ["confluence_map:2542097469"]


def test_classify_evidence_matches_real_8_bucket_distribution():
    """dev 운영 failure_bucket 8건의 실제 evidence_ref(2026-10-12 조회)로 분류
    분포를 확인한다 — §3.2 "장애 패턴 8개" 필수 사례."""
    from app.graph.failure_bucket import classify_evidence

    external_id_index = _candidates(
        ("2465855011", "confluence_map:2465855011", "C"),
    )
    page_identity_index = _candidates(
        ("2542097469", "confluence_map:2542097469", "C"),
    )

    buckets = {
        "6a1c8e1e": {
            "id": "6a1c8e1e", "root_cause": "", "recommended_action": "",
            "evidence_ref": (
                "confluence:LOOKIN/2465855011#관측9,관측10-4 (CLOUD.pcap/CLOUD_1.pcap/"
                "CLOUD_2.pcap/CLOUD_3.pcap frame 단위 사례, tcp.stream 예시 10.160.9.68:41074)"
            ),
        },
        "6ea28d59": {
            "id": "6ea28d59", "root_cause": "",
            "recommended_action": (
                "동료·qdevice·SBD의 실제 통신 경로와 공통 장애 구간을 확인한다. "
                "분석 원문: https://devops.sdsdev.co.kr/confluence/x/PVSFlw"
            ),
            "evidence_ref": "log:ps-dev-tndb001/var/log/pacemaker/pacemaker.log#L95-L102",
        },
        "3a91d1c3": {
            "id": "3a91d1c3", "root_cause": "", "recommended_action": "",
            "evidence_ref": (
                "capture:CLOUD.pcap#frame=9985 (stream247, 버킷A 예시), "
                "capture:CLOUD.pcap#frame=52056 (stream457, 버킷B 예시)"
            ),
        },
        "89d78df7": {
            "id": "89d78df7", "root_cause": "", "recommended_action": "",
            "evidence_ref": (
                "capture:CLOUD_2.pcap#frame=2698 (stale ACK ack=1718827394), "
                "frame=2850 (SNAT RST seq=1718827394 일치); capture:CLOUD.pcap#frame=2237/2238 "
                "동일 패턴; capture:CLOUD_1.pcap#frame=997/998 동일 패턴"
            ),
        },
        "bb8df507": {"id": "bb8df507", "root_cause": "", "recommended_action": "",
                      "evidence_ref": "legacy:pre-migration"},
        "c9d72a38": {"id": "c9d72a38", "root_cause": "", "recommended_action": "",
                      "evidence_ref": "legacy:pre-migration"},
        "59f417e2": {"id": "59f417e2", "root_cause": "", "recommended_action": "",
                      "evidence_ref": "legacy:pre-migration"},
        "efd42d6e": {"id": "efd42d6e", "root_cause": "", "recommended_action": "",
                      "evidence_ref": "legacy:pre-migration"},
    }

    results = {
        key: classify_evidence(b, external_id_index=external_id_index, page_identity_index=page_identity_index)
        for key, b in buckets.items()
    }

    assert results["6a1c8e1e"]["resolved"] == 1
    assert results["6a1c8e1e"]["external_artifact"] == 0

    assert results["6ea28d59"]["external_artifact"] == 1  # log:
    assert results["6ea28d59"]["resolved"] == 1  # PVSFlw tiny link

    for key in ("3a91d1c3", "89d78df7"):
        assert results[key]["external_artifact"] > 0
        assert results[key]["resolved"] == 0

    for key in ("bb8df507", "c9d72a38", "59f417e2", "efd42d6e"):
        assert results[key]["legacy_missing"] == 1
        assert sum(results[key].values()) == 1  # 다른 사유 없음

    # 어떤 사유로 분류돼도 Document 노드를 만들지 않는다(참고 집계일 뿐).
    from app.graph.failure_bucket import _EVIDENCE_REASONS
    assert set(_EVIDENCE_REASONS) == {
        "resolved", "external_artifact", "legacy_missing",
        "target_not_ingested", "unresolved_reference",
    }
