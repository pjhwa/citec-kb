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
        return await _check_fabrix(settings)

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


_FABRIX_PROBE_TTL_S = 60.0
_fabrix_probe_cache: tuple[float, dict[str, Any]] | None = None


async def _check_fabrix(settings: Settings) -> dict[str, Any]:
    """Real tiny non-stream + stream call; ``chat_supported`` reflects what happened."""
    import time

    from app import llm_fabrix

    global _fabrix_probe_cache
    now = time.monotonic()
    if _fabrix_probe_cache and now - _fabrix_probe_cache[0] < _FABRIX_PROBE_TTL_S:
        return dict(_fabrix_probe_cache[1])

    out: dict[str, Any] = {
        "ok": False,
        "backend": "fabrix",
        "model": settings.company_model_id,
        "chat_supported": False,
        "chat_stream_supported": False,
    }
    msgs = [{"role": "user", "content": "Reply with exactly: pong"}]
    url, hdrs = llm_fabrix.messages_url(settings), llm_fabrix.headers(settings)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            body = llm_fabrix.build_body(settings, msgs, max_tokens=16, temperature=0, stream=False)
            resp = await client.post(url, headers=hdrs, json=body)
            if resp.status_code >= 400:
                out["error"] = llm_fabrix.error_text(resp.status_code, resp.text)
            else:
                text = llm_fabrix.parse_complete(resp.json())
                out["chat_supported"] = bool(text.strip())
                out["sample"] = text[:80]
                if not text.strip():
                    out["error"] = "empty completion"
            if out["chat_supported"]:
                body = llm_fabrix.build_body(settings, msgs, max_tokens=16, temperature=0, stream=True)
                async with client.stream("POST", url, headers=hdrs, json=body) as sresp:
                    if sresp.status_code >= 400:
                        out["stream_error"] = llm_fabrix.error_text(
                            sresp.status_code, (await sresp.aread()).decode("utf-8", "replace")
                        )
                    else:
                        lines = [ln async for ln in sresp.aiter_lines()]
                        out["chat_stream_supported"] = bool(
                            "".join(llm_fabrix.iter_stream_text(iter(lines))).strip()
                        )
    except Exception as exc:  # noqa: BLE001 — surface probe errors
        out["error"] = str(exc)[:300]
    out["ok"] = bool(out["chat_supported"])
    _fabrix_probe_cache = (now, dict(out))
    return out
