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
