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
