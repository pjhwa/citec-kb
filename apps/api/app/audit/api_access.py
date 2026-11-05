"""2026-11-05 — non-MCP API usage log (who calls the API: browser UI, internal systems, external systems).

mcp_access_log already covers AI CLIs (mcp-server sends `X-Citec-Client: mcp-server`). This module
records everything else under /v1, classified by caller:

  web       a browser (Sec-Fetch-Site / Mozilla UA + Referer): the web UI pages used by people
  internal  a system on this host/container network (worker, cron scripts, curl localhost) —
            client IP inside API_INTERNAL_CIDRS (default loopback + the docker/private 172.16/12 range)
  external  any other system (another server calling the API)

The IP comes from X-Real-IP / X-Forwarded-For (set by nginx) and is therefore advisory: a client that
reaches the api port directly can send those headers itself. Name = `X-Citec-Client` if the caller
sets one (recommended for external systems), else the authenticated token name, else the User-Agent
product. Skipped on purpose: MCP (own table), the admin console's own polling (Referer /admin.html),
health/ops/auth-status polling, and OPTIONS preflights.

Rows are queued and inserted in batches by a daemon thread so a request never waits on the DB; the
queue is bounded (overflow is dropped and counted). Rows older than API_ACCESS_LOG_RETENTION_DAYS
(default 30) are pruned hourly. API_ACCESS_LOG=off disables the whole thing.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import queue
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import delete, func, select, text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.db.models import ApiAccessLog
from app.db.session import session_scope

logger = logging.getLogger("citec.audit.api")

DEFAULT_INTERNAL_CIDRS = "127.0.0.0/8,::1/128,169.254.0.0/16,172.16.0.0/12"
SKIP_PREFIXES = ("/v1/health", "/v1/ops/", "/v1/graph/status", "/v1/auth/")
_ID_SEG = re.compile(r"^(?:[0-9a-fA-F]{8}-[0-9a-fA-F-]{27}|[0-9a-fA-F]{16,}|\d+)$")
_FLUSH_EVERY_S = 2.0
_BATCH = 200
_QUEUE_MAX = 10000
_PRUNE_EVERY_S = 3600

_q: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=_QUEUE_MAX)
_dropped = 0
_writer_lock = threading.Lock()
_writer: threading.Thread | None = None


def enabled() -> bool:
    return os.environ.get("API_ACCESS_LOG", "on").strip().lower() not in ("off", "0", "false", "no")


def internal_networks() -> list:
    out = []
    for part in (os.environ.get("API_INTERNAL_CIDRS") or DEFAULT_INTERNAL_CIDRS).split(","):
        part = part.strip()
        if part:
            try:
                out.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                logger.warning("ignoring bad API_INTERNAL_CIDRS entry %r", part)
    return out


def client_ip(headers, peer: str | None) -> str | None:
    xr = (headers.get("x-real-ip") or "").strip()
    if xr:
        return xr[:64]
    xf = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return (xf or peer or None) and (xf or peer)[:64]


def is_internal(ip: str | None, nets=None) -> bool:
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in (nets if nets is not None else internal_networks()))


def classify(headers, ip: str | None, principal_name: str | None, nets=None) -> tuple[str, str | None]:
    """(kind, name) for one request. `headers` must support case-insensitive .get()."""
    ua = (headers.get("user-agent") or "").strip()
    browser = bool(headers.get("sec-fetch-site")) or ("Mozilla" in ua and bool(headers.get("referer")))
    kind = "web" if browser else ("internal" if is_internal(ip, nets) else "external")
    name = (headers.get("x-citec-client") or "").strip()[:64] or principal_name
    if not name and ua and kind != "web":
        name = re.split(r"[\s/]", ua, 1)[0][:64] or None
    return kind, name


def normalize_path(path: str) -> str:
    segs = ["{id}" if _ID_SEG.match(s) else s for s in path.split("/")]
    return "/".join(segs)[:256]


def should_log(path: str, method: str, headers) -> bool:
    if not enabled() or method == "OPTIONS" or not path.startswith("/v1/"):
        return False
    if path.startswith(SKIP_PREFIXES):
        return False
    if (headers.get("x-citec-client") or "") == "mcp-server":
        return False  # mcp_access_log
    return "/admin.html" not in (headers.get("referer") or "")


# ---------------------------------------------------------------- batched writer

def enqueue(row: dict[str, Any]) -> None:
    global _dropped
    _ensure_writer()
    try:
        _q.put_nowait(row)
    except queue.Full:
        _dropped += 1
        if _dropped % 1000 == 1:
            logger.warning("api access log queue full; dropped=%d", _dropped)


def flush() -> int:
    """Insert everything queued right now (also used by tests). Returns rows written."""
    rows: list[dict[str, Any]] = []
    while len(rows) < _BATCH * 10:
        try:
            rows.append(_q.get_nowait())
        except queue.Empty:
            break
    if not rows:
        return 0
    try:
        with session_scope() as session:
            session.add_all(ApiAccessLog(**r) for r in rows)
    except Exception:  # noqa: BLE001 — logging must never hurt the API
        logger.exception("api access log insert failed (%d rows lost)", len(rows))
        return 0
    return len(rows)


def prune() -> int:
    days = int(os.environ.get("API_ACCESS_LOG_RETENTION_DAYS") or 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    with session_scope() as session:
        return session.execute(delete(ApiAccessLog).where(ApiAccessLog.created_at < cutoff)).rowcount or 0


def _loop() -> None:
    last_prune = 0.0
    while True:
        time.sleep(_FLUSH_EVERY_S)
        try:
            while flush() >= _BATCH:
                pass
            if time.monotonic() - last_prune > _PRUNE_EVERY_S:
                last_prune = time.monotonic()
                prune()
        except Exception:  # noqa: BLE001
            logger.exception("api access log writer loop error")


def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    with _writer_lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_loop, name="api-access-log", daemon=True)
            _writer.start()


class ApiAccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not should_log(request.url.path, request.method, request.headers):
            return await call_next(request)
        t0 = time.monotonic()
        response = await call_next(request)
        try:
            principal = getattr(request.state, "principal", None)
            pname = principal.name if principal is not None and getattr(principal, "auth_via", "anonymous") != "anonymous" else None
            ip = client_ip(request.headers, request.client.host if request.client else None)
            kind, name = classify(request.headers, ip, pname)
            route = request.scope.get("route")
            # No matching route (404 probes, wrong URLs): keep only the first 3 segments so scanners
            # cannot fill the endpoint table with unique junk paths.
            path = getattr(route, "path", None) or ("(미등록) " + "/".join(normalize_path(request.url.path).split("/")[:4]))
            enqueue({"client_kind": kind, "client_name": name, "client_ip": ip, "method": request.method[:8], "path": path[:256],
                     "status_code": response.status_code, "latency_ms": int((time.monotonic() - t0) * 1000)})
        except Exception:  # noqa: BLE001
            logger.exception("api access log enqueue failed")
        return response


# ---------------------------------------------------------------- admin stats

WINDOWS = {"1h": (timedelta(hours=1), "minute"), "24h": (timedelta(hours=24), "hour"), "7d": (timedelta(days=7), "day")}


def api_usage_stats(session, window: str = "24h") -> dict[str, Any]:
    delta, unit = WINDOWS.get(window, WINDOWS["24h"])
    since = datetime.now(timezone.utc) - delta
    A = ApiAccessLog
    in_win = A.created_at >= since

    tot = session.execute(
        select(
            func.count(), func.count().filter(A.status_code >= 500), func.count().filter((A.status_code >= 400) & (A.status_code < 500)),
            func.avg(A.latency_ms), func.percentile_cont(0.95).within_group(A.latency_ms),
        ).where(in_win)
    ).one()
    by_kind = {k: int(c) for k, c in session.execute(select(A.client_kind, func.count()).where(in_win).group_by(A.client_kind)).all()}

    bucket = func.date_trunc(unit, A.created_at).label("t")
    series: dict[str, dict[str, int]] = {}
    for t, kind, c in session.execute(select(bucket, A.client_kind, func.count()).where(in_win).group_by(bucket, A.client_kind).order_by(bucket)).all():
        series.setdefault(t.isoformat(), {})[kind] = int(c)

    clients = session.execute(
        select(A.client_kind, A.client_name, A.client_ip, func.count().label("n"), func.count().filter(A.status_code >= 400).label("err"),
               func.avg(A.latency_ms), func.max(A.created_at))
        .where(in_win).group_by(A.client_kind, A.client_name, A.client_ip).order_by(text("n desc")).limit(30)
    ).all()
    endpoints = session.execute(
        select(A.method, A.path, func.count().label("n"), func.count().filter(A.status_code >= 400).label("err"), func.avg(A.latency_ms),
               func.percentile_cont(0.95).within_group(A.latency_ms))
        .where(in_win).group_by(A.method, A.path).order_by(text("n desc")).limit(15)
    ).all()
    errors = session.execute(select(A).where(in_win, A.status_code >= 400).order_by(A.created_at.desc()).limit(15)).scalars().all()

    return {
        "window": window if window in WINDOWS else "24h", "unit": unit, "since": since.isoformat(),
        "total": int(tot[0]), "errors_5xx": int(tot[1]), "errors_4xx": int(tot[2]),
        "avg_latency_ms": round(float(tot[3]), 1) if tot[3] is not None else None,
        "p95_latency_ms": round(float(tot[4]), 1) if tot[4] is not None else None,
        "by_kind": by_kind,
        "series": [{"t": t, **v} for t, v in series.items()],
        "clients": [{"kind": k, "name": n, "ip": ip, "count": int(c), "errors": int(e), "avg_latency_ms": round(float(a), 1) if a is not None else None,
                     "last_seen": ls.isoformat() if ls else None} for k, n, ip, c, e, a, ls in clients],
        "endpoints": [{"method": m, "path": p, "count": int(c), "errors": int(e), "avg_latency_ms": round(float(a), 1) if a is not None else None,
                       "p95_latency_ms": round(float(p95), 1) if p95 is not None else None} for m, p, c, e, a, p95 in endpoints],
        "recent_errors": [{"at": r.created_at.isoformat(), "kind": r.client_kind, "name": r.client_name, "ip": r.client_ip, "method": r.method,
                           "path": r.path, "status": r.status_code, "latency_ms": r.latency_ms} for r in errors],
        "internal_cidrs": [str(n) for n in internal_networks()],
        "logging_enabled": enabled(),
        "dropped": _dropped,
    }
