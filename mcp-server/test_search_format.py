"""kb_search request body: new filter parameters reach /v1/search."""

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
    "results": [
        {"title": "Ceph RBD 성능", "source_type": "confluence_map", "external_id": "1", "score": 0.5}
    ],
}


def test_subtree_filter_is_forwarded_only_when_set(monkeypatch):
    _, body = _run(monkeypatch, _PAYLOAD)
    assert set(body["filters"]) == {"status"}

    _, body = _run(monkeypatch, _PAYLOAD, exclude_subtree_ids=["2525893483"])
    assert body["filters"]["exclude_subtree_ids"] == ["2525893483"]
