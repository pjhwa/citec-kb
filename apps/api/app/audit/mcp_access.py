"""2026-10-14 — MCP 경유 HTTP 요청 기록.

운영자가 "AI가 MCP로 실제로 뭘 조회하는지"를 보고 싶어했는데, 기존
QueryLog(app.audit.log)는 audit=True로 명시한 호출만 기록하고, 그마저도
웹/MCP 구분이 없다. 가장 흔한 MCP 조회인 kb_search(`/v1/search`,
`/api/wiki/search`)는 audit 기본값이 False라 지금껏 전혀 안 남았다.

mcp-server/server.py는 이제 모든 요청에 `X-Citec-Client: mcp-server` 헤더를
보내고, 호출한 kb_* 도구 이름을 `X-Citec-Tool`에 싣는다. 이 모듈은 그 헤더가
있는 요청만 골라 mcp_access_log에 남긴다 — 어느 엔드포인트를 쓰든(라우터별로
일일이 log_query_answer를 심을 필요 없이) 자동으로 잡힌다."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.db.models import McpAccessLog
from app.db.session import session_scope

logger = logging.getLogger("citec.audit.mcp")

_QUERY_KEYS = (
    "q", "query", "anchor_value", "external_id", "bucket_id", "path", "slug", "code", "symptom_text",
)
_MAX_SUMMARY_LEN = 500


def _summarize(request: Request, raw_body: bytes) -> str | None:
    """쿼리스트링 또는 JSON 바디에서 사람이 알아볼 흔한 키 몇 개만 뽑는다 —
    전체 바디를 그대로 저장하지 않는다(업로드 같은 큰 바디, 토큰 등 민감값
    유입 방지)."""
    parts: list[str] = []
    for k, v in request.query_params.items():
        if k in _QUERY_KEYS or not parts:
            parts.append(f"{k}={v}")
    if raw_body:
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        if isinstance(body, dict):
            for key in _QUERY_KEYS:
                if key in body and body[key]:
                    parts.append(f"{key}={body[key]}")
    if not parts:
        return None
    summary = " ".join(parts)
    return summary[:_MAX_SUMMARY_LEN]


class McpAccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        is_mcp = request.headers.get("x-citec-client") == "mcp-server"
        if not is_mcp:
            return await call_next(request)

        raw_body = await request.body()
        t0 = time.monotonic()
        response = await call_next(request)
        latency_ms = int((time.monotonic() - t0) * 1000)

        try:
            summary = _summarize(request, raw_body)
            with session_scope() as session:
                session.add(McpAccessLog(
                    tool=request.headers.get("x-citec-tool"),
                    method=request.method,
                    path=request.url.path,
                    query_summary=summary,
                    status_code=response.status_code,
                    latency_ms=latency_ms,
                ))
        except Exception:  # noqa: BLE001 — 기록 실패가 실제 요청을 막으면 안 된다
            logger.exception("mcp access log insert failed")

        return response


def mcp_query_stats(session) -> dict[str, Any]:
    """admin 대시보드 "MCP 조회" 카드용 — 최근 1h/24h 건수(도구별)와
    최근 20건 raw 목록."""
    now = datetime.now(timezone.utc)
    since_1h = now - timedelta(hours=1)
    since_24h = now - timedelta(hours=24)

    count_1h = session.scalar(
        select(func.count()).select_from(McpAccessLog).where(McpAccessLog.created_at >= since_1h)
    ) or 0
    by_tool_24h_rows = session.execute(
        select(McpAccessLog.tool, func.count())
        .where(McpAccessLog.created_at >= since_24h)
        .group_by(McpAccessLog.tool)
        .order_by(func.count().desc())
    ).all()

    recent_rows = session.execute(
        select(McpAccessLog).order_by(McpAccessLog.created_at.desc()).limit(20)
    ).scalars().all()

    return {
        "count_1h": int(count_1h),
        "count_24h": sum(c for _, c in by_tool_24h_rows),
        "by_tool_24h": {(t or "unknown"): int(c) for t, c in by_tool_24h_rows},
        "recent": [
            {
                "id": r.id,
                "tool": r.tool,
                "method": r.method,
                "path": r.path,
                "query_summary": r.query_summary,
                "status_code": r.status_code,
                "latency_ms": r.latency_ms,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in recent_rows
        ],
    }
