"""Response-masking middleware (spec §4–§6). Fail-closed: any error → 500, never the original."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.audit.api_access import client_ip
from app.auth.principal import resolve_principal
from app.masking.audit import write_unmask_audit
from app.masking.dictionary import get_dictionary
from app.masking.engine import Masker, mask_json
from app.masking.policy import decide
from app.settings import get_settings

logger = logging.getLogger("citec.masking")

IN_SCOPE = ("/v1/", "/api/")
SKIP_PREFIXES = ("/v1/health", "/v1/ops/", "/v1/auth/", "/api/docs", "/api/openapi")
_REF_KEYS = ("document_id", "external_id")


def in_scope(path: str) -> bool:
    return path.startswith(IN_SCOPE) and not path.startswith(SKIP_PREFIXES)


def _unmask_requested(request: Request) -> bool:
    q = request.query_params.get("unmask", "").lower()
    return q in ("1", "true") or request.headers.get("x-citec-unmask", "").strip() == "1"


def _doc_refs(obj: Any, out: list, depth: int = 0) -> None:
    if depth > 3 or len(out) >= 50:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _REF_KEYS and isinstance(v, (str, int)):
                out.append(str(v))
            else:
                _doc_refs(v, out, depth + 1)
    elif isinstance(obj, list):
        for x in obj[:100]:
            _doc_refs(x, out, depth + 1)


class MaskingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not in_scope(request.url.path):
            return await call_next(request)
        response = await call_next(request)
        if "application/json" not in response.headers.get("content-type", ""):
            return response
        raw = b"".join([chunk async for chunk in response.body_iterator])
        try:
            return await run_in_threadpool(self._process, request, response, raw)
        except Exception:  # noqa: BLE001 — fail closed
            logger.exception("masking failed for %s %s", request.method, request.url.path)
            return JSONResponse({"detail": "masking failed"}, status_code=500)

    def _process(self, request: Request, response: Response, raw: bytes) -> Response:
        if not raw:
            return self._rebuild(response, raw, {})
        principal = resolve_principal(
            authorization=request.headers.get("authorization"), api_key=request.headers.get("x-api-key")
        )
        d = decide(principal, get_settings().auth_mode, _unmask_requested(request))
        data = json.loads(raw)

        # When an original is granted we still run pii/net masking: it counts what would be revealed
        # (for the audit row) and is the fallback body if the audit write fails.
        groups = frozenset({"pii", "net"}) if d.unmask_granted else d.groups
        masker = Masker(groups, get_dictionary())
        masked = mask_json(data, masker)
        counts = {k: v for k, v in masker.counts.items() if v}
        refs: list = []
        _doc_refs(data, refs)
        ip = client_ip(request.headers, request.client.host if request.client else None)
        audit_kw = dict(principal=principal, level=d.level, method=request.method, path=request.url.path,
                        doc_refs=refs, counts=counts, client_ip=ip)

        body_obj, meta = masked, {}
        if d.unmask_granted:
            if counts:
                try:
                    write_unmask_audit(outcome="granted", **audit_kw)
                    body_obj, meta = data, {"unmask_granted": True, "revealed": counts}
                except Exception:  # noqa: BLE001 — no audit row → no original
                    logger.exception("unmask audit write failed; serving masked")
                    meta = {"audit_failed": True}
            else:
                body_obj = data
        elif d.unmask_denied:
            meta = {"unmask_denied": True}
            try:
                write_unmask_audit(outcome="denied", **{**audit_kw, "counts": {}})
            except Exception:  # noqa: BLE001
                logger.exception("denied-unmask audit write failed")

        if (counts and body_obj is masked) or meta:
            info = {"applied": body_obj is masked, "level": d.level, "counts": counts, **meta}
            if isinstance(body_obj, dict):
                body_obj = {**body_obj, "masking": info}
            return self._rebuild(response, json.dumps(body_obj, ensure_ascii=False).encode(), info)
        return self._rebuild(response, json.dumps(body_obj, ensure_ascii=False).encode(), {})

    @staticmethod
    def _rebuild(response: Response, body: bytes, info: dict) -> Response:
        out = Response(content=body, status_code=response.status_code)
        out.raw_headers = [(k, v) for k, v in response.raw_headers if k.lower() != b"content-length"]
        out.raw_headers.append((b"content-length", str(len(body)).encode()))
        counts = info.get("counts") or {}
        if info.get("applied") and counts:
            out.headers["X-Citec-Masking"] = ",".join(f"{k}={v}" for k, v in sorted(counts.items()))
        return out
