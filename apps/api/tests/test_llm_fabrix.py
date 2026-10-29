"""Fabrix adapter, pinned to responses captured from the production endpoint (2026-10-29)."""
import asyncio
import json

import httpx
import pytest

from app import llm, llm_chat, llm_fabrix as F
from app.settings import Settings

S = Settings(
    COMPANY_ENDPOINT_URL="https://fx.example/sds/prod/api-chat/",
    COMPANY_CLIENT_KEY="ck", COMPANY_PASS_KEY="Bearer pk", COMPANY_EMAIL="u@example.com",
    COMPANY_MODEL_ID="019f23a1-46aa-7fa5-a6ab-391127fea7e6",
)

NONSTREAM_OK = {"userId": "u", "modelType": "vllm_glm5.2", "content": "pong", "reasoningContent": None,
                "truncated": False, "finishReason": "stop", "status": "SUCCESS", "responseCode": "R20000",
                "eventStatus": "CHUNK", "eventData": ""}
NONSTREAM_EMPTY = dict(NONSTREAM_OK, content="")


def _sse(event_status, content=None, **extra):
    d = {"user_id": "u", "content": content, "status": "SUCCESS", "event_status": event_status,
         "finish_reason": None, "truncated": None, **extra}
    return "data: " + json.dumps(d, ensure_ascii=False)


STREAM = [
    _sse("CHUNK", ""), _sse("REQUEST_ANALYSIS"), _sse("FINAL_ANSWER"),
    _sse("THINK", "The"), _sse("THINK", ' user wants me to reply with exactly "pong".'),
    _sse("CHUNK", "po"), "", _sse("CHUNK", "ng"), _sse("CHUNK", "", finish_reason="stop", truncated=False),
]


def test_build_body_maps_system_and_alternating_turns():
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"}, {"role": "user", "content": "retry"}]
    b = F.build_body(S, msgs, max_tokens=1200, temperature=0.25, stream=False)
    assert b["systemPrompt"] == "SYS"
    assert b["contents"] == ["q", "a", "retry"]
    assert b["modelIds"] == [S.company_model_id]
    assert b["isStream"] is False
    assert b["llmConfig"]["max_new_tokens"] == 1200 + F.THINK_HEADROOM  # reasoning shares the budget
    assert b["llmConfig"]["temperature"] == 0.25


def test_build_body_merges_consecutive_same_role_and_requires_trailing_user():
    b = F.build_body(S, [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],
                     max_tokens=10, temperature=0, stream=True)
    assert b["contents"] == ["a\n\nb"] and "systemPrompt" not in b
    with pytest.raises(F.FabrixError):
        F.build_body(S, [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}],
                     max_tokens=10, temperature=0, stream=False)


def test_headers_and_url():
    h = F.headers(S)
    assert h["x-fabrix-client"] == "ck" and h["x-openapi-token"] == "Bearer pk"
    assert h["x-generative-ai-user-email"] == "u@example.com"
    assert F.messages_url(S) == "https://fx.example/sds/prod/api-chat/openapi/chat/v1/messages"


def test_parse_complete():
    assert F.parse_complete(NONSTREAM_OK) == "pong"
    assert F.parse_complete(NONSTREAM_EMPTY) == ""
    with pytest.raises(F.FabrixError):
        F.parse_complete(dict(NONSTREAM_OK, status="FAIL"))
    with pytest.raises(F.FabrixError):
        F.parse_complete([1])


def test_stream_keeps_only_chunk_content_not_think():
    out = list(F.iter_stream_text(iter(STREAM)))
    assert "".join(out) == "pong"
    assert not any("user wants" in x for x in out)


def test_error_text_shapes():
    nf = json.dumps({"error": "Not Found", "message": "It does not exist",
                     "detailMessage": "The LLM Model [0] does not exist", "errorCode": "COMMON_ERROR_4"})
    assert F.error_text(404, nf) == "Fabrix HTTP 404 COMMON_ERROR_4: The LLM Model [0] does not exist"
    bad = json.dumps({"detail": [{"type": "json_invalid", "msg": "JSON decode error"}]})
    assert "json_invalid" in F.error_text(422, bad)
    assert F.error_text(502, "<html>bad gateway</html>").startswith("Fabrix HTTP 502:")


def _patch_client(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(F.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_complete_and_stream_through_chat_layer(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["h"], seen["b"] = req.headers, json.loads(req.content)
        if seen["b"]["isStream"]:
            return httpx.Response(200, content=("\n\n".join(STREAM) + "\n\n").encode(),
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=NONSTREAM_OK)

    _patch_client(monkeypatch, handler)
    monkeypatch.setattr(Settings, "llm_backend", property(lambda self: "fabrix"))
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "q"}]
    assert llm_chat.chat_complete(msgs, settings=S) == "pong"
    assert seen["h"]["x-fabrix-client"] == "ck"
    assert "".join(llm_chat.chat_complete_stream(msgs, settings=S)) == "pong"
    assert seen["b"]["isStream"] is True


def test_http_error_becomes_llmchaterror(monkeypatch):
    body = {"error": "Not Found", "detailMessage": "model missing", "errorCode": "COMMON_ERROR_4"}
    _patch_client(monkeypatch, lambda req: httpx.Response(404, json=body))
    monkeypatch.setattr(Settings, "llm_backend", property(lambda self: "fabrix"))
    with pytest.raises(llm_chat.LLMChatError, match="COMMON_ERROR_4"):
        llm_chat.chat_complete([{"role": "user", "content": "q"}], settings=S)
    with pytest.raises(llm_chat.LLMChatError, match="model missing"):
        list(llm_chat.chat_complete_stream([{"role": "user", "content": "q"}], settings=S))


def test_check_llm_probes_for_real(monkeypatch):
    def handler(req: httpx.Request):
        if json.loads(req.content)["isStream"]:
            return httpx.Response(200, content=("\n\n".join(STREAM) + "\n\n").encode())
        return httpx.Response(200, json=NONSTREAM_OK)

    real = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(llm, "_fabrix_probe_cache", None)
    monkeypatch.setattr(Settings, "llm_backend", property(lambda self: "fabrix"))
    r = asyncio.run(llm.check_llm(S))
    assert r["ok"] and r["chat_supported"] and r["chat_stream_supported"] and r["sample"] == "pong"

    monkeypatch.setattr(llm, "_fabrix_probe_cache", None)
    monkeypatch.setattr(llm.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "no"})), **kw))
    r = asyncio.run(llm.check_llm(S))
    assert not r["ok"] and not r["chat_supported"] and "401" in r["error"]
