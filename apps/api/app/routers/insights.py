"""RETIRED (2026-11-05): 1st-generation insights.

This router only answers with a retirement notice for one release so callers get guidance instead
of an opaque 404. Replacement: confirmed decisions (`/v1/decisions`, MCP `kb_list_decisions` /
`kb_get_decision`). Remove this file in the release after the one that introduces it.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

RETIRED_MESSAGE = "폐기됨: Insight는 사람 확정 결정(Confirmed Decision)으로 대체됐습니다. kb_list_decisions / kb_get_decision (REST: /v1/decisions)을 사용하세요."

router = APIRouter(prefix="/v1", tags=["insights-retired"])


def retired_response() -> JSONResponse:
    return JSONResponse(
        status_code=410,
        content={"detail": RETIRED_MESSAGE, "replacement": "/v1/decisions", "retired": True},
    )


@router.api_route("/insights", methods=["GET", "POST"], include_in_schema=False)
@router.api_route("/insights/{rest:path}", methods=["GET", "POST", "PATCH", "PUT", "DELETE"], include_in_schema=False)
def insights_retired(rest: str = "") -> JSONResponse:
    return retired_response()
