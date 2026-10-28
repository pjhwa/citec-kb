"""kb_search text output and request body: total_candidates, folded copies,
and the new filter parameters reaching /v1/search."""

import asyncio
import json

import httpx

import server


def _run(monkeypatch, payload, **kwargs):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        server,
        "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(handler)
        ),
    )
    out = asyncio.run(server.kb_search("q", **kwargs))
    return out, seen["body"]


_PAYLOAD = {
    "total": 1,
    "returned_count": 1,
    "total_candidates": 27,
    "results": [
        {
            "title": "Ceph RBD 성능",
            "source_type": "confluence_map",
            "external_id": "1",
            "score": 0.5,
            "duplicate_count": 2,
        }
    ],
}


def test_output_shows_total_candidates_next_to_total_and_folded_copies(monkeypatch):
    out, _ = _run(monkeypatch, _PAYLOAD)
    assert "total=1 total_candidates=27" in out
    assert "(사본 2건 접힘)" in out


def test_new_filters_are_forwarded_only_when_set(monkeypatch):
    _, body = _run(monkeypatch, _PAYLOAD)
    assert set(body["filters"]) == {"status"}

    _, body = _run(
        monkeypatch,
        _PAYLOAD,
        exclude_subtree_ids=["2525893483"],
        include_irrelevant_maps=False,
        diversify_copies=False,
    )
    f = body["filters"]
    assert f["exclude_subtree_ids"] == ["2525893483"]
    assert f["include_irrelevant_maps"] is False
    assert f["diversify_copies"] is False


def test_similar_incident_forwards_source_types_and_shows_the_source(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "brief": "b",
                "cases": [
                    {"external_id": "26090761356", "title": "이라크 정전", "source_type": "incident_reports",
                     "applicability": {"label": "가능"}, "score": 1}
                ],
            },
        )

    monkeypatch.setattr(
        server,
        "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(handler)
        ),
    )
    out = asyncio.run(server.kb_similar_incident("정전", source_types=["incident_reports"]))
    assert seen["body"]["source_types"] == ["incident_reports"]
    assert "[incident_reports] 26090761356" in out

    asyncio.run(server.kb_similar_incident("정전"))
    assert "source_types" not in seen["body"]


# ── A3: limit 검증 ──
def test_search_limit_zero_is_rejected_not_silently_one():
    import asyncio

    import server

    out = asyncio.run(server.kb_search("redis", limit=0))
    assert out.startswith("오류: limit은 1~50")


def test_search_limit_over_50_is_clamped_and_reported(monkeypatch):
    import asyncio

    import httpx

    import server

    seen = {}

    def handler(request):
        import json

        seen["top_k"] = json.loads(request.content)["top_k"]
        return httpx.Response(200, json={"results": [{"title": "t", "source_type": "support_history", "external_id": "CITECTS-1", "score": 1.0}]})

    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(base_url="http://t", transport=httpx.MockTransport(handler)),
    )
    out = asyncio.run(server.kb_search("redis", limit=500))
    assert seen["top_k"] == 50
    assert "limit 500→50으로 보정" in out.splitlines()[0]
    ok = asyncio.run(server.kb_search("redis", limit=5))
    assert "보정" not in ok.splitlines()[0]


def test_search_warnings_are_printed(monkeypatch):
    import asyncio

    import httpx

    import server

    payload = {"results": [], "warnings": ["area/domain 'zzz'에 해당하는 문서가 없습니다"]}
    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))),
    )
    out = asyncio.run(server.kb_search("x", area="zzz"))
    assert "[경고]" in out and "zzz" in out
    payload["results"] = [{"title": "t", "source_type": "checkitem", "external_id": "A", "score": 1.0}]
    out2 = asyncio.run(server.kb_search("x", area="zzz"))
    assert "[경고]" in out2 and "- [checkitem] t" in out2


def test_search_header_shows_trust_and_missing_identifier(monkeypatch):
    import asyncio

    import httpx

    import server

    payload = {
        "results": [{"title": "무관", "source_type": "support_history", "external_id": "CITECTS-1", "score": 0.02}],
        "trust_retrieval": "weak", "exact_tokens": ["CITECTS-99999"], "missing_identifiers": ["CITECTS-99999"],
    }
    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))),
    )
    out = asyncio.run(server.kb_search("CITECTS-99999 내용"))
    head = out.splitlines()[0]
    assert "trust=weak" in head and "exact_tokens=['CITECTS-99999']" in head
    assert "[경고] 질의의 식별자 CITECTS-99999 는 코퍼스에서 찾지 못했습니다" in out
    assert "- [support_history] 무관" in out


def test_query_hybrid_branch_shows_trust_and_missing_identifier():
    import server

    out = server._format_query_response({
        "intent": "hybrid_search", "params": {},
        "result": {"total": 1, "trust_retrieval": "weak", "missing_identifiers": ["CITECTS-8888"],
                   "items": [{"title": "x", "external_id": "CITECTS-1", "source_type": "support_history", "score": 0.01}]},
    })
    assert "trust=weak" in out and "CITECTS-8888" in out


def test_clean_search_header_unchanged_without_trust_fields(monkeypatch):
    import asyncio

    import httpx

    import server

    payload = {"results": [{"title": "t", "source_type": "tech_repo", "external_id": "1", "score": 1.0}]}
    monkeypatch.setattr(
        server, "_client",
        lambda timeout=30.0: httpx.AsyncClient(
            base_url="http://t", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))),
    )
    out = asyncio.run(server.kb_search("x"))
    assert "trust=" not in out and "[경고]" not in out
