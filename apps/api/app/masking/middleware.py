"""Response-masking middleware (spec §4–§6). Fail-closed: any error → 500, never the original."""

from __future__ import annotations

import codecs
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.auth.principal import resolve_principal
from app.masking.audit import write_unmask_audit
from app.masking.dictionary import get_dictionary
from app.masking.engine import Masker, mask_json
from app.masking.policy import decide
from app.settings import get_settings

logger = logging.getLogger("citec.masking")

IN_SCOPE = ("/v1/", "/api/")
# Skipped subtrees: the path itself or anything below it ("/v1/health" skips "/v1/health/llm" but not
# "/v1/healthcheck-x"). An entry ending in "/" only matches below it.
SKIP_PREFIXES = ("/v1/health", "/v1/ops/", "/v1/auth", "/api/docs", "/api/openapi")
_REF_KEYS = ("document_id", "external_id")
# Bodies we cannot mask but that cannot carry text PII either. Everything else unknown fails closed.
_PASSTHROUGH_TYPES = ("image/", "font/")
_XML_TYPES = ("application/xml",)

JSON, SSE, TEXT, PASS, REJECT = "json", "sse", "text", "pass", "reject"


def _skipped(path: str) -> bool:
    for x in SKIP_PREFIXES:
        if x.endswith("/"):
            if path.startswith(x):
                return True
        elif path == x or path.startswith(x + "/"):
            return True
    return False


def in_scope(path: str) -> bool:
    return path.startswith(IN_SCOPE) and not _skipped(path)


def classify(content_type: str) -> str:
    """Media type → how the body is masked. Parameters (charset…) and case are ignored."""
    mt = (content_type or "").split(";", 1)[0].strip().lower()
    if mt == "application/json" or mt.endswith("+json"):
        return JSON
    if mt == "text/event-stream":
        return SSE
    if mt.startswith("text/") or mt in _XML_TYPES or mt.endswith("+xml"):
        return TEXT
    if mt.startswith(_PASSTHROUGH_TYPES):
        return PASS
    return REJECT


# Denied-unmask audit rows: only when something would have been revealed, at most one per (sub, path)
# per _DENIED_TTL seconds per process. Bounded so a flood of distinct paths cannot grow memory.
_DENIED_TTL = 60.0
_DENIED_CAP = 2000
_DENIED_SEEN: "OrderedDict[tuple, float]" = OrderedDict()
_DENIED_LOCK = threading.Lock()


def _now() -> float:
    return time.monotonic()


def reset_denied_cache() -> None:
    with _DENIED_LOCK:
        _DENIED_SEEN.clear()


def _claim_denied(sub: str, path: str, now: float) -> bool:
    """True if a denied row for (sub, path) may be written now (and records it)."""
    key = (sub, path)
    with _DENIED_LOCK:
        while _DENIED_SEEN:                       # oldest first: drop expired entries
            ts = next(iter(_DENIED_SEEN.values()))
            if now - ts < _DENIED_TTL:
                break
            _DENIED_SEEN.popitem(last=False)
        if key in _DENIED_SEEN:
            return False
        _DENIED_SEEN[key] = now
        while len(_DENIED_SEEN) > _DENIED_CAP:
            _DENIED_SEEN.popitem(last=False)
        return True


def _release_denied(sub: str, path: str) -> None:
    with _DENIED_LOCK:
        _DENIED_SEEN.pop((sub, path), None)


def audit_client_ip(request: Request) -> str:
    """TCP peer always; a proxy header (spoofable — port 8573 is published) only as an extra hint."""
    peer = request.client.host if request.client and request.client.host else "unknown"
    hint = (request.headers.get("x-real-ip") or "").split(",")[0].strip() \
        or (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return (f"{peer} xri={hint}" if hint else peer)[:64]


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


_SSE_KEEP = ("event:", "id:", "retry:")


def _mask_sse(raw: bytes, masker: Masker, refs: list) -> bytes:
    """Mask a whole buffered SSE stream with one Masker.

    Events are split on a blank line (CRLF/CR normalised to LF); each event's ``data:`` lines are joined
    with LF. All ``token`` events (``{"type":"token","text":…}``) are coalesced into one at the first
    token's position so a value split across fragments is still caught; every other JSON payload goes
    through ``mask_json``, non-JSON payloads through ``mask_text``. ``event:``/``id:``/``retry:`` lines
    are kept as-is; other lines (comments, unknown fields) are masked as text. Output: one
    ``data: <json>`` line per JSON event, LF framing.
    """
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        head: list = []
        data: list = []
        slot = None
        for ln in block.split("\n"):
            if ln.startswith("data:"):
                v = ln[5:]
                data.append(v[1:] if v.startswith(" ") else v)
                if slot is None:
                    slot = len(head)
                    head.append(None)
            else:
                head.append(ln)
        payload = "\n".join(data) if slot is not None else None
        obj, is_json = None, False
        if payload is not None:
            try:
                obj, is_json = json.loads(payload), True
            except ValueError:
                pass
        events.append({"head": head, "slot": slot, "payload": payload, "json": is_json, "obj": obj})

    tokens = [e for e in events if e["json"] and isinstance(e["obj"], dict)
              and e["obj"].get("type") == "token" and isinstance(e["obj"].get("text"), str)]
    if tokens:
        tokens[0]["obj"] = {**tokens[0]["obj"], "text": "".join(t["obj"]["text"] for t in tokens)}
        drop = {id(t) for t in tokens[1:]}
        events = [e for e in events if id(e) not in drop]

    out: list = []
    for e in events:
        if e["json"]:
            _doc_refs(e["obj"], refs)
            data_lines = [json.dumps(mask_json(e["obj"], masker), ensure_ascii=False)]
        elif e["payload"] is not None:
            data_lines = masker.mask_text(e["payload"]).split("\n")
        else:
            data_lines = []
        lines: list = []
        for i, ln in enumerate(e["head"]):
            if i == e["slot"]:
                lines.extend("data: " + d for d in data_lines)
            elif ln.startswith(_SSE_KEEP):
                lines.append(ln)
            else:
                lines.append(masker.mask_text(ln))
        out.append("\n".join(lines) + "\n\n")
    return "".join(out).encode("utf-8")


def _charset(content_type: str) -> str:
    for part in (content_type or "").split(";")[1:]:
        k, _, v = part.partition("=")
        if k.strip().lower() == "charset" and v.strip():
            try:
                return codecs.lookup(v.strip().strip('"')).name
            except LookupError:
                break
    return "utf-8"


class MaskingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not in_scope(request.url.path):
            return await call_next(request)
        response = await call_next(request)
        if request.method == "HEAD" or response.status_code in (204, 304):
            return response
        ctype = response.headers.get("content-type", "")
        kind = classify(ctype)
        if kind == PASS:
            return response
        raw = b"".join([chunk async for chunk in response.body_iterator])
        if not raw:  # redirects, empty 200s: nothing to mask
            return self._rebuild(response, raw, {})
        encoding = response.headers.get("content-encoding", "").strip().lower()
        if kind == REJECT or encoding not in ("", "identity"):
            logger.warning("unmaskable response blocked: %s %s content-type=%r content-encoding=%r",
                           request.method, request.url.path, ctype, encoding)
            return JSONResponse({"detail": "unmaskable content type"}, status_code=500)
        try:
            return await run_in_threadpool(self._process, request, response, raw, kind)
        except Exception:  # noqa: BLE001 — fail closed
            logger.exception("masking failed for %s %s", request.method, request.url.path)
            return JSONResponse({"detail": "masking failed"}, status_code=500)

    def _process(self, request: Request, response: Response, raw: bytes, kind: str = JSON) -> Response:
        """One decision path for every body kind: decide → mask → audit → choose body."""
        principal = resolve_principal(
            authorization=request.headers.get("authorization"), api_key=request.headers.get("x-api-key")
        )
        d = decide(principal, get_settings().auth_mode, _unmask_requested(request))

        # When an original is granted we still run pii/net masking: it counts what would be revealed
        # (for the audit row) and is the fallback body if the audit write fails.
        groups = frozenset({"pii", "net"}) if d.unmask_granted else d.groups
        masker = Masker(groups, get_dictionary())
        refs: list = []
        data: Any = None
        if kind == JSON:
            data = json.loads(raw)
            masked: Any = mask_json(data, masker)
            _doc_refs(data, refs)
        elif kind == SSE:
            masked = _mask_sse(raw, masker, refs)
        else:
            cs = _charset(response.headers.get("content-type", ""))
            masked = masker.mask_text(raw.decode(cs, errors="replace")).encode(cs, errors="replace")
        counts = {k: v for k, v in masker.counts.items() if v}

        ip = audit_client_ip(request)
        audit_kw = dict(principal=principal, level=d.level, method=request.method, path=request.url.path,
                        doc_refs=refs, counts=counts, client_ip=ip)
        original, meta = False, {}
        if d.unmask_granted:
            if counts:
                try:
                    write_unmask_audit(outcome="granted", **audit_kw)
                    original, meta = True, {"unmask_granted": True, "revealed": counts}
                except Exception:  # noqa: BLE001 — no audit row → no original
                    logger.exception("unmask audit write failed; serving masked")
                    meta = {"audit_failed": True}
            else:
                original = True
        elif d.unmask_denied:
            meta = {"unmask_denied": True}
            would_reveal = counts.get("pii", 0) + counts.get("net", 0) > 0
            if would_reveal and _claim_denied(principal.sub, request.url.path, _now()):
                try:
                    write_unmask_audit(outcome="denied", **{**audit_kw, "counts": {}})
                except Exception:  # noqa: BLE001
                    _release_denied(principal.sub, request.url.path)
                    logger.exception("denied-unmask audit write failed")

        info = {"applied": not original, "level": d.level, "counts": counts, **meta}
        if kind != JSON:
            return self._rebuild(response, raw if original else masked, info)
        body_obj = data if original else masked
        if (counts and not original) or meta:
            if isinstance(body_obj, dict):
                body_obj = {**body_obj, "masking": info}
        else:
            info = {}
        return self._rebuild(response, json.dumps(body_obj, ensure_ascii=False).encode(), info)

    @staticmethod
    def _rebuild(response: Response, body: bytes, info: dict) -> Response:
        out = Response(content=body, status_code=response.status_code)
        out.raw_headers = [(k, v) for k, v in response.raw_headers if k.lower() != b"content-length"]
        out.raw_headers.append((b"content-length", str(len(body)).encode()))
        counts = info.get("counts") or {}
        if info.get("applied") and counts:
            out.headers["X-Citec-Masking"] = ",".join(f"{k}={v}" for k, v in sorted(counts.items()))
        for flag, value in (("unmask_granted", "granted"), ("unmask_denied", "denied"),
                            ("audit_failed", "audit_failed")):
            if info.get(flag):
                out.headers["X-Citec-Unmask"] = value
        return out
