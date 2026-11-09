"""MaskingMiddleware on SSE (text/event-stream): buffered, token events coalesced, one Masker per stream."""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from test_masking_middleware import _fresh_denied_cache, harness  # noqa: F401 (autouse fixture)

# Shaped like POST /api/query (external_compat.api_query.event_gen).
API_QUERY_EVENTS = [
    {"type": "status", "text": "[일반] 지식 검색 중… (citec-kb)"},
    {"type": "sources", "files": ["jira/T-1.md"]},
    {"type": "status", "text": "답변 생성 중…"},
    {"type": "token", "text": "call 010-12"},
    {"type": "token", "text": "34-5678 at 10.0.0.1"},
    {"type": "token", "text": " ok"},
    {"type": "error", "text": "llm timeout at 10.0.0.9", "error": "llm timeout at 10.0.0.9"},
    {"type": "done", "result": {
        "answer": "call 010-1234-5678 at 10.0.0.1 ok", "generation_status": "ok",
        "citations": [{"id": "C1", "external_id": "T-1", "source_type": "jira", "title": "t 10.0.0.9",
                       "path": "jira/T-1.md"}]}},
]
RAW_API_QUERY = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in API_QUERY_EVENTS).encode()

# Hand-written stream: CRLF framing, event:/id: lines, a non-JSON multi-line payload, no space after "data:".
RAW_MIXED = (
    b"event: note\r\nid: 7\r\ndata: plain 010-1234-5678\r\ndata: second 10.0.0.1\r\n\r\n"
    b'data:{"type":"meta","citations":[{"snippet":"ip 10.0.0.1"}]}\r\n\r\n'
)
LEAKS = ("010-1234-5678", "010-12", "5678", "10.0.0.1", "10.0.0.9")


def _stream(raw: bytes):
    def gen():
        # Small chunks so event boundaries fall inside chunks, like a real generator.
        for i in range(0, len(raw), 7):
            yield raw[i:i + 7]
    return StreamingResponse(gen(), media_type="text/event-stream")


@pytest.fixture
def sse(monkeypatch):
    app, audit, state = harness(monkeypatch)

    @app.post("/api/query")
    def api_query():
        return _stream(RAW_API_QUERY)

    @app.get("/v1/mixed")
    def mixed():
        return _stream(RAW_MIXED)

    return TestClient(app), audit, state


def _events(text: str) -> list:
    """Same line loop as mcp-server/server.py _ask_impl (replicated: server.py imports the mcp package)."""
    out = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[len("data: "):])
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def test_sse_masked_with_coalesced_tokens(sse):
    c, audit, _ = sse
    r = c.post("/api/query", headers={"X-API-Key": "viewer"}, json={})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    for v in LEAKS:
        assert v not in r.text
    assert r.headers["content-length"] == str(len(r.content))
    assert "x-citec-masking" in r.headers
    assert r.text.endswith("\n\n")
    evs = _events(r.text)
    types = [e["type"] for e in evs]
    assert types == ["status", "sources", "status", "token", "error", "done"]
    tok = evs[3]["text"]
    assert tok == "call [PHONE-1] at [IP-1] ok"
    # same value → same number across events (one Masker per response)
    assert evs[5]["result"]["answer"] == "call [PHONE-1] at [IP-1] ok"
    assert evs[4]["text"] == "llm timeout at [IP-2]" and evs[4]["error"] == "llm timeout at [IP-2]"
    assert evs[5]["result"]["citations"][0]["title"] == "t [IP-2]"
    assert evs[0]["text"] == "[일반] 지식 검색 중… (citec-kb)"   # non-ASCII kept as-is (ensure_ascii=False)
    assert not audit


def test_sse_preserves_event_and_id_lines_and_masks_plain_payload(sse):
    c, _, _ = sse
    r = c.get("/v1/mixed", headers={"X-API-Key": "author"})
    for v in LEAKS:
        assert v not in r.text
    blocks = r.text.split("\n\n")
    assert blocks[-1] == ""
    first = blocks[0].split("\n")
    assert first == ["event: note", "id: 7", "data: plain [PHONE-1]", "data: second [IP-1]"]
    assert json.loads(blocks[1][len("data: "):]) == {"type": "meta", "citations": [{"snippet": "ip [IP-1]"}]}


def test_sse_senior_unmask_returns_identical_bytes_and_audits_once(sse):
    c, audit, _ = sse
    r = c.post("/api/query", headers={"X-API-Key": "senior", "X-Citec-Unmask": "1"}, json={})
    assert r.content == RAW_API_QUERY
    assert [a["outcome"] for a in audit] == ["granted"]
    assert audit[0]["doc_refs"] == ["T-1"] and audit[0]["counts"]["pii"] >= 1
    assert r.headers.get("x-citec-unmask") == "granted"


def test_sse_audit_failure_serves_masked_stream(sse):
    c, audit, state = sse
    state["audit_fail"] = True
    r = c.post("/api/query", headers={"X-API-Key": "admin", "X-Citec-Unmask": "1"}, json={})
    assert r.status_code == 200
    for v in LEAKS:
        assert v not in r.text
    assert r.headers.get("x-citec-unmask") == "audit_failed"
    assert not audit


def test_sse_engine_error_fails_closed(sse, monkeypatch):
    import app.masking.middleware as MW

    c, _, _ = sse
    monkeypatch.setattr(MW, "mask_json", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = c.post("/api/query", headers={"X-API-Key": "viewer"}, json={})
    assert r.status_code == 500
    for v in LEAKS:
        assert v not in r.text


def test_mcp_client_folds_masked_stream(sse):
    """The masked stream still parses with the MCP client's line loop and folds via ask_fold."""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "mcp-server"))
    from ask_fold import fold_ask_events

    c, _, _ = sse
    r = c.post("/api/query", headers={"X-API-Key": "viewer"}, json={})
    out = fold_ask_events(_events(r.text))
    assert "call [PHONE-1] at [IP-1] ok" in out and "jira/T-1.md" in out
    for v in LEAKS:
        assert v not in out


def test_sse_denied_header_and_single_row(sse):
    c, audit, _ = sse
    for _ in range(3):
        r = c.post("/api/query?unmask=1", headers={"X-API-Key": "viewer"}, json={})
        assert r.headers["x-citec-unmask"] == "denied"
        for v in LEAKS:
            assert v not in r.text
    assert [a["outcome"] for a in audit] == ["denied"]
