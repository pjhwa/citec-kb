"""Fabrix chat adapter (sync + stream) for RAG.

Wire format verified against the production endpoint (2026-10-29):

* ``POST {COMPANY_ENDPOINT_URL}/openapi/chat/v1/messages`` with headers
  ``x-fabrix-client`` / ``x-openapi-token`` (``Bearer …``) /
  ``x-generative-ai-user-email``.
* body ``{modelIds, contents[], llmConfig{max_new_tokens,temperature}, systemPrompt, isStream}``.
  ``contents`` alternates user / assistant turns (first is the user's).
* non-stream reply is camelCase JSON, text in ``content``
  (``status=SUCCESS``, ``finishReason``, ``truncated``).
* stream reply is SSE ``data: {snake_case json}``; answer text is only in
  events with ``event_status == "CHUNK"``. ``THINK`` events carry the model's
  reasoning in the same ``content`` field and must NOT reach the answer.
* the model reasons before answering and reasoning shares ``max_new_tokens``;
  a small limit yields ``content == ""`` with ``finishReason == "stop"``, so we
  add headroom on top of the caller's answer budget.
* errors: HTTP 4xx/5xx JSON ``{"error","message","detailMessage","errorCode"}``
  (FastAPI-style ``{"detail": [...]}`` for malformed bodies).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from typing import Any

import httpx

from app.settings import Settings

logger = logging.getLogger("citec.llm_fabrix")

# Reasoning tokens count against max_new_tokens; env-tunable without a deploy.
THINK_HEADROOM = int(os.environ.get("FABRIX_THINK_HEADROOM", "2048") or 2048)
_SUCCESS_STATUSES = {"SUCCESS", None}


class FabrixError(RuntimeError):
    pass


def messages_url(settings: Settings) -> str:
    return (settings.company_endpoint_url or "").rstrip("/") + "/openapi/chat/v1/messages"


def headers(settings: Settings) -> dict[str, str]:
    return {
        "x-fabrix-client": settings.company_client_key or "",
        "x-openapi-token": settings.company_pass_key or "",
        "x-generative-ai-user-email": settings.company_email or "",
        "content-type": "application/json",
    }


def build_body(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    stream: bool,
) -> dict[str, Any]:
    """Map OpenAI-style messages onto Fabrix ``systemPrompt`` + ``contents``."""
    system_parts: list[str] = []
    contents: list[str] = []
    last_role: str | None = None
    for m in messages:
        role, text = m.get("role"), str(m.get("content") or "")
        if role == "system":
            system_parts.append(text)
            continue
        role = "assistant" if role == "assistant" else "user"
        if role == last_role and contents:
            contents[-1] += "\n\n" + text  # contents must alternate
        else:
            contents.append(text)
        last_role = role
    if not contents or last_role != "user":
        raise FabrixError("Fabrix request needs a trailing user message")
    body: dict[str, Any] = {
        "modelIds": [settings.company_model_id],
        "contents": contents,
        "llmConfig": {
            "max_new_tokens": int(max_tokens) + THINK_HEADROOM,
            "temperature": temperature,
        },
        "isStream": stream,
    }
    if system_parts:
        body["systemPrompt"] = "\n\n".join(system_parts)
    return body


def error_text(status: int, raw: str) -> str:
    """Compact, human-readable reason from a Fabrix error body."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return f"Fabrix HTTP {status}: {raw[:300]}"
    if isinstance(data, dict):
        detail = data.get("detailMessage") or data.get("message") or data.get("error")
        if detail:
            code = data.get("errorCode")
            return f"Fabrix HTTP {status}{' ' + str(code) if code else ''}: {str(detail)[:300]}"
        if data.get("detail"):
            return f"Fabrix HTTP {status}: {json.dumps(data['detail'], ensure_ascii=False)[:300]}"
    return f"Fabrix HTTP {status}: {raw[:300]}"


def parse_complete(data: Any) -> str:
    if not isinstance(data, dict):
        raise FabrixError(f"Unexpected Fabrix response shape: {data!r}"[:300])
    status = data.get("status")
    if status not in _SUCCESS_STATUSES:
        raise FabrixError(f"Fabrix status={status} responseCode={data.get('responseCode')}")
    text = str(data.get("content") or "")
    if not text:
        fb = data.get("filterBlockReason") or {}
        logger.warning(
            "fabrix empty content finishReason=%s truncated=%s filter=%s",
            data.get("finishReason"), data.get("truncated"), fb.get("resultCode"),
        )
    return text


def iter_stream_text(lines: Iterator[str]) -> Iterator[str]:
    """Yield answer deltas (``CHUNK`` events only) from SSE lines."""
    for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            ev = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("status") not in _SUCCESS_STATUSES:
            raise FabrixError(f"Fabrix stream status={ev.get('status')} responseCode={ev.get('response_code')}")
        if ev.get("event_status") == "CHUNK":
            piece = ev.get("content")
            if piece:
                yield str(piece)


def complete(settings: Settings, messages, *, max_tokens: int, temperature: float, timeout: float) -> str:
    body = build_body(settings, messages, max_tokens=max_tokens, temperature=temperature, stream=False)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(messages_url(settings), headers=headers(settings), json=body)
            if resp.status_code >= 400:
                raise FabrixError(error_text(resp.status_code, resp.text))
            return parse_complete(resp.json())
    except httpx.HTTPError as exc:
        raise FabrixError(f"Fabrix request failed: {exc}") from exc


def stream(settings: Settings, messages, *, max_tokens: int, temperature: float, timeout: float) -> Iterator[str]:
    body = build_body(settings, messages, max_tokens=max_tokens, temperature=temperature, stream=True)
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", messages_url(settings), headers=headers(settings), json=body) as resp:
                if resp.status_code >= 400:
                    raise FabrixError(error_text(resp.status_code, resp.read().decode("utf-8", "replace")))
                yield from iter_stream_text(resp.iter_lines())
    except httpx.HTTPError as exc:
        raise FabrixError(f"Fabrix stream failed: {exc}") from exc
