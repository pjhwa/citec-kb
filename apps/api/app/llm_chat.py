"""OpenAI-compatible chat completion (sync + stream) for RAG."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from typing import Any, Optional

import httpx

from app.settings import Settings, get_settings

logger = logging.getLogger("citec.llm_chat")


class LLMChatError(RuntimeError):
    pass


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://citec-knowledge.local",
        "X-Title": "CI-TEC Knowledge Platform",
    }


def _build_body(
    settings: Settings,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    stream: bool,
) -> dict[str, Any]:
    cfg = settings.load_model_config()
    body: dict[str, Any] = {
        "model": settings.openrouter_model_id,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature if temperature is not None else float(cfg.get("temperature") or 0.3),
        "stream": stream,
    }
    if cfg.get("disable_thinking"):
        body["reasoning"] = {"enabled": False}
    return body


def chat_complete(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1200,
    temperature: float = 0.3,
    settings: Settings | None = None,
    timeout: float = 120.0,
) -> str:
    """Non-streaming completion; returns assistant text."""
    settings = settings or get_settings()
    if settings.llm_backend == "none":
        raise LLMChatError("LLM not configured (OPENROUTER_API_KEY)")
    if settings.llm_backend == "fabrix":
        raise LLMChatError("Fabrix live chat not wired yet; use OpenRouter in dev")

    url = settings.openrouter_base_url.rstrip("/") + "/chat/completions"
    body = _build_body(
        settings, messages, max_tokens=max_tokens, temperature=temperature, stream=False
    )
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, headers=_headers(settings), json=body)
        if resp.status_code >= 400:
            raise LLMChatError(f"LLM HTTP {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
    try:
        return str(data["choices"][0]["message"]["content"] or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMChatError(f"Unexpected LLM response shape: {data!r}"[:300]) from exc


def chat_complete_stream(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 1200,
    temperature: float = 0.3,
    settings: Settings | None = None,
    timeout: float = 180.0,
) -> Iterator[str]:
    """Yield text deltas from SSE stream."""
    settings = settings or get_settings()
    if settings.llm_backend != "openrouter":
        raise LLMChatError(f"stream requires openrouter, got {settings.llm_backend}")

    url = settings.openrouter_base_url.rstrip("/") + "/chat/completions"
    body = _build_body(
        settings, messages, max_tokens=max_tokens, temperature=temperature, stream=True
    )
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", url, headers=_headers(settings), json=body) as resp:
            if resp.status_code >= 400:
                raise LLMChatError(f"LLM HTTP {resp.status_code}: {resp.read().decode()[:400]}")
            for line in resp.iter_lines():
                if not line:
                    continue
                if line.startswith("data: "):
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        data = json.loads(payload)
                        delta = data["choices"][0].get("delta") or {}
                        piece = delta.get("content") or ""
                        if piece:
                            yield piece
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
