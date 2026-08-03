"""Confluence draw.io diagram API — list/get/put diagrams on a page."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.confluence.client import ConfluenceConfigError
from app.confluence.service import (
    DiagramNotFoundError,
    get_diagram_xml,
    list_diagrams,
    put_diagram_xml,
)

router = APIRouter(prefix="/v1/confluence", tags=["confluence"])


def _map_http_error(e: httpx.HTTPStatusError) -> HTTPException:
    status = e.response.status_code
    if status == 404:
        return HTTPException(status_code=404, detail="confluence resource not found")
    if status in (401, 403):
        return HTTPException(status_code=502, detail="confluence auth failed — check CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD")
    return HTTPException(status_code=502, detail=f"confluence error: {status}")


@router.get("/pages/{page_id}/diagrams")
async def get_page_diagrams(page_id: str) -> dict[str, Any]:
    try:
        items = await list_diagrams(page_id)
    except ConfluenceConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except httpx.HTTPStatusError as e:
        raise _map_http_error(e) from None
    return {"items": items, "total": len(items)}


@router.get("/pages/{page_id}/diagrams/{diagram_name}")
async def get_page_diagram(page_id: str, diagram_name: str) -> Response:
    try:
        xml_content = await get_diagram_xml(page_id, diagram_name)
    except ConfluenceConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except DiagramNotFoundError:
        raise HTTPException(status_code=404, detail=f"diagram not found: {diagram_name}") from None
    except httpx.HTTPStatusError as e:
        raise _map_http_error(e) from None
    return Response(content=xml_content, media_type="text/xml")


@router.put("/pages/{page_id}/diagrams/{diagram_name}")
async def put_page_diagram(page_id: str, diagram_name: str, request: Request) -> dict[str, Any]:
    body = await request.body()
    try:
        result = await put_diagram_xml(page_id, diagram_name, body.decode("utf-8"))
    except ConfluenceConfigError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except httpx.HTTPStatusError as e:
        raise _map_http_error(e) from None
    return result
