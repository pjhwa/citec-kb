"""kb_graph_explore pagination rendering — G07 회귀 테스트. 과거엔 API 페이지(최대
50건)를 20줄만 찍고 "offset+len(items)"를 다음 offset으로 안내해, 실제로는
21~50번째 항목에 영원히 접근할 수 없는 구멍이 있었다(2차 리뷰에서 지적)."""

import asyncio

import httpx

import server


def _run(monkeypatch, payload, **kwargs):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        server,
        "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(handler)
        ),
    )
    out = asyncio.run(server.kb_graph_explore("component", "NetApp", **kwargs))
    return out


def _doc(i: int) -> dict:
    return {"id": f"d{i:03d}", "title": f"문서{i}", "source_type": "tech_repo",
            "evidence_grade": "A", "relation": "HAS_COMPONENT", "hops": 1}


def _page_payload(total: int, offset: int) -> dict:
    page = [_doc(i) for i in range(offset, min(offset + 50, total))]
    return {
        "as_of": "2026-10-12", "anchor": {"type": "component", "resolved_id": "NetApp"},
        "found": True, "documents": page, "components": [], "failure_buckets": [],
        "excluded_hub_components": [],
        "documents_total": total, "documents_truncated": offset + len(page) < total,
        "components_total": 0, "components_truncated": False,
        "failure_buckets_total": 0, "failure_buckets_truncated": False,
    }


def test_renders_all_50_lines_not_just_20(monkeypatch):
    out = _run(monkeypatch, _page_payload(total=60, offset=0))
    shown_ids = {f"d{i:03d}" for i in range(50)}
    for doc_id in shown_ids:
        assert doc_id in out, f"{doc_id} missing from rendered output"
    assert "documents_offset=50" in out


def test_walking_offsets_reaches_every_document_exactly_once(monkeypatch):
    """total=60을 offset=0부터 응답이 가리키는 다음 offset을 따라가면 전부,
    중복 없이 도달해야 한다."""
    total = 60
    offset = 0
    seen_ids: set[str] = set()
    for _ in range(10):  # 안전장치 — 무한루프 방지
        payload = _page_payload(total=total, offset=offset)
        out = _run(monkeypatch, payload, documents_offset=offset)
        for i in range(offset, min(offset + 50, total)):
            doc_id = f"d{i:03d}"
            assert doc_id in out
            seen_ids.add(doc_id)
        if f"documents_offset={offset + 50}" in out:
            offset += 50
        else:
            break
    assert seen_ids == {f"d{i:03d}" for i in range(total)}


def test_boundary_exactly_50_has_no_next_page_hint(monkeypatch):
    out = _run(monkeypatch, _page_payload(total=50, offset=0))
    assert "documents_offset=" not in out


def test_boundary_51_has_next_page_hint_to_offset_50(monkeypatch):
    out = _run(monkeypatch, _page_payload(total=51, offset=0))
    assert "documents_offset=50" in out


def test_boundary_21_still_shows_all_21_lines(monkeypatch):
    out = _run(monkeypatch, _page_payload(total=21, offset=0))
    for i in range(21):
        assert f"d{i:03d}" in out


def test_zero_documents_renders_without_error(monkeypatch):
    payload = _page_payload(total=0, offset=0)
    out = _run(monkeypatch, payload)
    assert "연관 문서 (0건" in out


def _run_404(monkeypatch, anchor_value="nothingmatchxyz918273"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(handler)
        ),
    )
    return asyncio.run(server.kb_graph_explore("component", anchor_value))


def test_unknown_anchor_404_includes_status_line_not_bare_sentence(monkeypatch):
    # R07(2026-10-13 2차 핸드오프) §2.7 실제 재현 — 과거엔 이 분기만 status
    # 라인 없는 문장 하나를 돌려줘 isError=false와 함께 보면 성공처럼 보였다.
    out = _run_404(monkeypatch)
    assert out.startswith("status: not_found\n")


def test_invalid_anchor_type_returns_invalid_request_status():
    out = asyncio.run(server.kb_graph_explore("not_a_real_type", "x"))
    assert out.startswith("status: invalid_request\n")


def test_empty_anchor_value_returns_invalid_request_status():
    out = asyncio.run(server.kb_graph_explore("component", "   "))
    assert out.startswith("status: invalid_request\n")


def test_renders_extracted_inferred_tag_on_route(monkeypatch):
    # R06(2026-10-13 2차 핸드오프) §1 — EXTRACTED/INFERRED는 API에 있었지만
    # MCP 출력에서 사라져 있었다.
    payload = {
        "as_of": "2026-10-12", "anchor": {"type": "component", "resolved_id": "NetApp"},
        "found": True,
        "documents": [{
            "id": "tech_repo:1", "title": "본문 추론", "source_type": "tech_repo",
            "evidence_grade": "A", "hops": 1,
            "route": [{"relation": "HAS_COMPONENT", "direction": "in", "tag": "INFERRED"}],
        }],
        "components": [], "failure_buckets": [], "excluded_hub_components": [],
        "documents_total": 1, "documents_truncated": False,
        "components_total": 0, "components_truncated": False,
        "failure_buckets_total": 0, "failure_buckets_truncated": False,
    }
    out = _run(monkeypatch, payload)
    assert "HAS_COMPONENT[INFERRED]" in out


def test_renders_copies_archived_and_snapshot_tags(monkeypatch):
    payload = {
        "as_of": "2026-10-12", "anchor": {"type": "component", "resolved_id": "NetApp"},
        "found": True,
        "documents": [{
            "id": "tech_repo:1", "title": "같은 페이지", "source_type": "tech_repo",
            "evidence_grade": "A", "hops": 1, "route": [{"relation": "HAS_COMPONENT", "direction": "in"}],
            "copies": ["confluence_map:1"], "archived": True,
            "access": {"mcp_args": {"path": "tech_repo/1.md"}, "body_kind": "fulltext"},
        }],
        "components": [], "failure_buckets": [], "excluded_hub_components": [],
        "documents_total": 1, "documents_truncated": False,
        "components_total": 0, "components_truncated": False,
        "failure_buckets_total": 0, "failure_buckets_truncated": False,
    }
    out = _run(monkeypatch, payload)
    assert "copies=1" in out
    assert "archived" in out
    assert "원문: kb_get_document(path='tech_repo/1.md')" in out


def test_renders_snapshot_tag_for_confluence_map_body_kind(monkeypatch):
    payload = {
        "as_of": "2026-10-12", "anchor": {"type": "component", "resolved_id": "NetApp"},
        "found": True,
        "documents": [{
            "id": "confluence_map:1", "title": "요약본", "source_type": "confluence_map",
            "evidence_grade": "C", "hops": 1, "route": [{"relation": "HAS_COMPONENT", "direction": "in"}],
            "access": {"mcp_args": {"path": "confluence_map/1.md"}, "body_kind": "snapshot"},
        }],
        "components": [], "failure_buckets": [], "excluded_hub_components": [],
        "documents_total": 1, "documents_truncated": False,
        "components_total": 0, "components_truncated": False,
        "failure_buckets_total": 0, "failure_buckets_truncated": False,
    }
    out = _run(monkeypatch, payload)
    assert "snapshot" in out
    assert "copies=" not in out
    assert "archived" not in out.split("snapshot")[0].split("]")[0]


def test_isolated_failure_bucket_anchor_renders_own_evidence_reasons(monkeypatch):
    """F02(2026-10-13 최종 핸드오프) — anchor_type=failure_bucket이 isolated일
    때도 anchor 자신의 evidence_reasons가 렌더링돼야 한다."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "as_of": "2026-10-13", "status": "isolated", "found": True,
            "anchor": {
                "type": "failure_bucket", "resolved_id": "fb-1", "resolved_name": "외부 capture 버킷",
                "evidence_reasons": {"resolved": 0, "external_artifact": 1, "legacy_missing": 0,
                                      "target_not_ingested": 0, "unresolved_reference": 0},
            },
            "documents": [], "components": [], "failure_buckets": [],
            "excluded_hub_components": [],
            "documents_total": 0, "documents_truncated": False,
            "components_total": 0, "components_truncated": False,
            "failure_buckets_total": 0, "failure_buckets_truncated": False,
        })

    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(base_url="http://t", transport=httpx.MockTransport(handler)),
    )
    out = asyncio.run(server.kb_graph_explore("failure_bucket", "fb-1"))
    assert "status: isolated" in out
    assert "해석된 앵커: 외부 capture 버킷" in out
    assert "앵커 자신의 evidence_reasons=" in out
    assert "external_artifact" in out


def test_isolated_failure_bucket_anchor_shows_unknown_when_reasons_absent(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "as_of": "2026-10-13", "status": "isolated", "found": True,
            "anchor": {
                "type": "failure_bucket", "resolved_id": "fb-2", "resolved_name": "미재백필 버킷",
                "evidence_reasons": None,
            },
            "documents": [], "components": [], "failure_buckets": [],
            "excluded_hub_components": [],
            "documents_total": 0, "documents_truncated": False,
            "components_total": 0, "components_truncated": False,
            "failure_buckets_total": 0, "failure_buckets_truncated": False,
        })

    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(base_url="http://t", transport=httpx.MockTransport(handler)),
    )
    out = asyncio.run(server.kb_graph_explore("failure_bucket", "fb-2"))
    assert "앵커 자신의 evidence_reasons=unknown" in out


def test_renders_freshness_summary_line(monkeypatch):
    """F04(2026-10-13 최종 핸드오프) §7/8 — as_of(MAX(synced_at))와 별개로
    마지막 완료된 전체 배치 상태를 한 줄로 보여줘야 한다."""
    payload = _page_payload(total=1, offset=0)
    payload["freshness"] = {
        "latest_full_completed_run_id": "r1", "latest_full_completed_generation": "g1",
        "latest_full_completed_at": "2026-10-13T00:00:00+00:00",
        "latest_full_completed_status": "completed", "full_run_in_progress": False,
    }
    out = _run(monkeypatch, payload)
    assert "freshness: 마지막 완료된 전체 배치 2026-10-13T00:00:00+00:00" in out


def test_renders_freshness_in_progress_warning(monkeypatch):
    payload = _page_payload(total=1, offset=0)
    payload["freshness"] = {
        "latest_full_completed_run_id": None, "latest_full_completed_generation": None,
        "latest_full_completed_at": None, "latest_full_completed_status": None,
        "full_run_in_progress": True,
    }
    out = _run(monkeypatch, payload)
    assert "지금 새 전체 배치가 진행 중" in out


def test_renders_freshness_unknown_when_never_completed(monkeypatch):
    payload = _page_payload(total=1, offset=0)
    payload["freshness"] = {
        "latest_full_completed_run_id": None, "latest_full_completed_generation": None,
        "latest_full_completed_at": None, "latest_full_completed_status": None,
        "full_run_in_progress": False,
    }
    out = _run(monkeypatch, payload)
    assert "freshness: 완료된 전체 배치 기록 없음(unknown)" in out


def test_kb_graph_status_renders_read_state_and_liveness(monkeypatch):
    """F04(2026-10-13 최종 핸드오프) §8 — kb_graph_status가 REST
    GET /v1/graph/status와 같은 근거(run/revision/freshness)를 MCP에서도
    보여줘야 한다."""
    payload = {
        "as_of": "2026-10-13",
        "latest_run": {
            "run_id": "r1", "started_at": "2026-10-13T00:00:00+00:00", "completed_at": None,
            "status": "running", "scope": "full", "extractor_version": "v1", "generation": "g1",
            "lexicon_version": None, "documents_synced": 10, "documents_skipped": 0,
            "documents_failed": 0, "documents_state_write_failed": 0, "buckets_synced": 0,
            "buckets_failed": 0, "unresolved_references": 0, "hub_recompute_ok": None,
            "liveness": {"state": "likely_dead", "lock_holder_pid": 123, "lock_currently_held": False,
                         "heartbeat_at": None, "seconds_since_heartbeat": None},
        },
        "latest_full_completed_run": {
            "run_id": "r0", "started_at": "2026-10-12T00:00:00+00:00",
            "completed_at": "2026-10-12T01:00:00+00:00",
            "status": "completed", "scope": "full", "extractor_version": "v0", "generation": "g0",
            "lexicon_version": None, "documents_synced": 100, "documents_skipped": 0,
            "documents_failed": 0, "documents_state_write_failed": 0, "buckets_synced": 1,
            "buckets_failed": 0, "unresolved_references": 0, "hub_recompute_ok": True,
            "liveness": None,
        },
        "neo4j_generation": "g0", "generation_matches": False, "read_state": "mixed",
        "neo4j_store_identity": {"id": "store-abc", "name": "neo4j", "creation_date": "2026-01-01T00:00:00Z"},
        "api_build_sha": "unknown", "contract_version": "2026-10-13.1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(base_url="http://t", transport=httpx.MockTransport(handler)),
    )
    out = asyncio.run(server.kb_graph_status())
    assert "read_state: mixed" in out
    assert "liveness=likely_dead" in out
    assert "neo4j_store_identity(store id): store-abc" in out
    assert "contract_version: 2026-10-13.1" in out
    assert "latest_full_completed_run: run_id=r0" in out


def test_failure_bucket_section_label_and_empty_hint(monkeypatch):
    """A7 — '연관 과거 장애'는 FailureBucket 목록이라 오해를 불렀다. 라벨 변경 + 0건 안내."""
    out = _run(monkeypatch, _page_payload(total=1, offset=0))
    assert "연관 failure bucket (0건" in out
    assert "연관 과거 장애" not in out
    assert "과거 장애가 없다는 뜻이 아닙니다" in out and "kb_similar_incident" in out


def test_failure_bucket_section_with_items_has_no_empty_hint(monkeypatch):
    payload = _page_payload(total=1, offset=0)
    payload["failure_buckets"] = [{"id": "fb-1", "bucket_name": "RST idle", "hops": 1, "route": []}]
    payload["failure_buckets_total"] = 1
    out = _run(monkeypatch, payload)
    assert "연관 failure bucket (1건" in out and "RST idle" in out
    assert "과거 장애가 없다는 뜻이 아닙니다" not in out
