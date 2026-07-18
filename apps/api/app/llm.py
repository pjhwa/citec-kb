"""Minimal OpenAI-compatible LLM client (OpenRouter for dev)."""

from __future__ import annotations

from typing import Any

import httpx

from app.settings import Settings, get_settings


class LLMError(RuntimeError):
    pass


async def check_llm(settings: Settings | None = None) -> dict[str, Any]:
    """Lightweight connectivity check (does not burn many tokens)."""
    settings = settings or get_settings()
    backend = settings.llm_backend
    if backend == "none":
        return {
            "ok": False,
            "backend": backend,
            "error": "No LLM credentials (set OPENROUTER_API_KEY for dev)",
        }
    if backend == "fabrix":
        return {
            "ok": True,
            "backend": backend,
            "model": settings.company_model_id,
            "note": "Fabrix configured; live probe deferred to PR-09",
        }

    # OpenRouter: list models is heavier; do a tiny chat completion
    model = settings.openrouter_model_id
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://citec-knowledge.local",
        "X-Title": "CI-TEC Knowledge Platform",
    }
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Reply with exactly: pong"},
        ],
        "max_tokens": 16,
        "temperature": 0,
    }
    cfg = settings.load_model_config()
    if cfg.get("disable_thinking"):
        body["reasoning"] = {"enabled": False}

    url = settings.openrouter_base_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "backend": "openrouter",
                    "model": model,
                    "status_code": resp.status_code,
                    "error": resp.text[:500],
                }
            data = resp.json()
            content = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return {
                "ok": True,
                "backend": "openrouter",
                "model": model,
                "profile": settings.model_profile_key,
                "max_context_tokens": settings.max_context_tokens,
                "sample": (content or "")[:80],
            }
    except Exception as exc:  # noqa: BLE001 — surface probe errors
        return {
            "ok": False,
            "backend": "openrouter",
            "model": model,
            "error": str(exc),
        }
