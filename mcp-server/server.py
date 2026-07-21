#!/usr/bin/env python3
"""citec-kb MCP server — thin proxy over REST for Claude / Cursor / other MCP clients.

Calls the citec-kb API (wiki-qa compatible /api/* plus native /v1/* helpers).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

CITEC_KB_BASE_URL = os.environ.get(
    "CITEC_KB_BASE_URL",
    os.environ.get("WIKI_QA_BASE_URL", "http://api:8000"),
)
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8100"))
MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "streamable-http").strip().lower()
# Optional bearer for AUTH_MODE=apikey|oidc later
CITEC_KB_TOKEN = os.environ.get("CITEC_KB_TOKEN", "").strip()

mcp = FastMCP(
    "citec-kb-mcp",
    host=MCP_HOST,
    port=MCP_PORT,
)


def _headers() -> dict[str, str]:
    h: dict[str, str] = {"Accept": "application/json"}
    if CITEC_KB_TOKEN:
        h["Authorization"] = f"Bearer {CITEC_KB_TOKEN}"
    return h


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=CITEC_KB_BASE_URL.rstrip("/"),
        timeout=timeout,
        headers=_headers(),
    )


def _err(e: Exception) -> str:
    return f"오류: citec-kb API에 연결할 수 없습니다 ({e})"


# ── Tools (wiki-qa compatible names + citec-kb aliases) ────────────


@mcp.tool()
async def kb_search(
    query: str,
    section: str = "",
    area: str = "",
    category: str = "",
    limit: int = 10,
) -> str:
    """CI-TEC 지식베이스에서 문서를 검색한다 (하이브리드 FTS+vector).
    section(선택): support_history|checkitems|tech_repo|tuning_ai|vendor_docs|confluence_docs|synthesis
    area(선택): domain 필터 (os|dbms|network|…)
    """
    return await _search_impl(query, section, area, category, limit)


@mcp.tool()
async def wiki_search(
    query: str,
    section: str = "",
    area: str = "",
    category: str = "",
    limit: int = 10,
) -> str:
    """(wiki-qa 호환) kb_search 와 동일."""
    return await _search_impl(query, section, area, category, limit)


async def _search_impl(
    query: str, section: str, area: str, category: str, limit: int
) -> str:
    try:
        async with _client() as client:
            resp = await client.get(
                "/api/wiki/search",
                params={
                    "q": query,
                    "section": section,
                    "area": area,
                    "category": category,
                    "limit": limit,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    results = data.get("results") or []
    if not results:
        return "검색 결과가 없습니다."

    lines = [
        f"검색 결과 {len(results)}건 (backend={data.get('backend', 'citec-kb')}). "
        f"원문 전체는 kb_get_document(path=…) 로 조회하세요."
    ]
    for r in results:
        path = r.get("path") or ""
        body_api = r.get("body_api") or r.get("body_api_url") or ""
        web_url = r.get("web_url") or r.get("web_path") or ""
        lines.append(
            f"- [{r.get('section') or r.get('source_type') or ''}] "
            f"{r.get('title', '(제목 없음)')}\n"
            f"  path: {path}\n"
            f"  body_api: {body_api}\n"
            f"  web_url: {web_url}\n"
            f"  mcp: kb_get_document(path={path!r})\n"
            f"  snippet: {r.get('snippet', '')}"
        )
    return "\n".join(lines)


@mcp.tool()
async def kb_get_document(path: str) -> str:
    """문서 원문(markdown)을 조회한다. path는 kb_search 결과의 path 값을 그대로 사용한다.
    예: support_history/CITECTS-2502.md 또는 CITECTS-2502
    """
    return await _get_document_impl(path)


@mcp.tool()
async def wiki_get_document(path: str) -> str:
    """(wiki-qa 호환) kb_get_document 와 동일."""
    return await _get_document_impl(path)


async def _get_document_impl(path: str) -> str:
    try:
        async with _client() as client:
            resp = await client.get("/api/wiki/file", params={"path": path})
            if resp.status_code == 404:
                return f"오류: 파일을 찾을 수 없습니다: {path}"
            if resp.status_code == 403:
                return f"오류: 접근할 수 없는 경로입니다: {path}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    title = data.get("title") or ""
    content = data.get("content") or ""
    acc = data.get("access") or {}
    header_bits = []
    if data.get("external_id"):
        header_bits.append(f"external_id={data.get('external_id')}")
    if data.get("source_type"):
        header_bits.append(f"source_type={data.get('source_type')}")
    if acc.get("web_url") or data.get("web_url"):
        header_bits.append(f"web_url={acc.get('web_url') or data.get('web_url')}")
    if acc.get("body_api") or data.get("body_api"):
        header_bits.append(f"body_api={acc.get('body_api') or data.get('body_api')}")
    meta_line = (" | ".join(header_bits) + "\n\n") if header_bits else ""
    if title:
        return f"# {title}\n{meta_line}{content}"
    return meta_line + content


@mcp.tool()
async def kb_list_insights(limit: int = 20) -> str:
    """Insight(승인 플로우) / 합성 지식 목록을 조회한다."""
    return await _list_synthesis_impl(limit)


@mcp.tool()
async def wiki_list_synthesis(limit: int = 20) -> str:
    """(wiki-qa 호환) kb_list_insights 와 동일 — synthesis≈insights."""
    return await _list_synthesis_impl(limit)


async def _list_synthesis_impl(limit: int) -> str:
    try:
        async with _client() as client:
            resp = await client.get("/api/synthesis", params={"limit": limit})
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    items = data.get("items") or []
    if not items:
        return "저장된 Insight/합성 답변이 없습니다."

    lines = [f"Insight {len(items)}건 (전체 {data.get('total', 0)}건):"]
    for it in items:
        lines.append(
            f"- {it.get('slug', '')} [{it.get('quality') or it.get('status', '')}] "
            f"{it.get('query') or it.get('title', '')} "
            f"(수정: {it.get('updated_at', '')})"
        )
    return "\n".join(lines)


@mcp.tool()
async def kb_get_insight(slug: str) -> str:
    """Insight 본문을 조회한다. slug는 kb_list_insights 결과의 id/slug."""
    return await _get_synthesis_impl(slug)


@mcp.tool()
async def wiki_get_synthesis(slug: str) -> str:
    """(wiki-qa 호환) kb_get_insight 와 동일."""
    return await _get_synthesis_impl(slug)


async def _get_synthesis_impl(slug: str) -> str:
    try:
        async with _client() as client:
            resp = await client.get(f"/api/synthesis/{slug}")
            if resp.status_code == 404:
                return f"오류: Insight/합성 답변을 찾을 수 없습니다: {slug}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    sources = data.get("sources") or []
    src = ", ".join(str(s) for s in sources) if sources else "없음"
    return (
        f"질문/제목: {data.get('query') or data.get('title', '')}\n"
        f"상태: {data.get('quality') or data.get('status', '')}\n\n"
        f"{data.get('answer', '')}\n\n출처: {src}"
    )


@mcp.tool()
async def kb_ask(query: str, template: str = "general", mode: str = "fast") -> str:
    """CI-TEC 지식 근거로 자연어 질문에 답변한다 (RAG + Trust).
    template: general|checkitems|support_history|tech_repo|tuning_ai|synthesis
    mode: fast|deep
    """
    return await _ask_impl(query, template, mode)


@mcp.tool()
async def wiki_ask(query: str, template: str = "general") -> str:
    """(wiki-qa 호환) kb_ask 와 동일 (mode=fast)."""
    return await _ask_impl(query, template, "fast")


async def _ask_impl(query: str, template: str, mode: str) -> str:
    answer_parts: list[str] = []
    sources: list[str] = []
    cite_lines: list[str] = []
    try:
        async with _client(timeout=180.0) as client:
            async with client.stream(
                "POST",
                "/api/query",
                json={
                    "query": query,
                    "template": template or "general",
                    "mode": mode if mode in {"fast", "deep"} else "fast",
                    "stream": True,
                },
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[len("data: ") :])
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "token":
                        answer_parts.append(event.get("text") or "")
                    elif etype == "sources":
                        sources = list(event.get("files") or [])
                    elif etype == "error":
                        return f"오류: {event.get('text') or event.get('error') or '알 수 없는 오류'}"
                    elif etype == "done":
                        result = event.get("result") or {}
                        if not answer_parts and result.get("answer"):
                            answer_parts.append(str(result.get("answer")))
                        for c in result.get("citations") or []:
                            if not isinstance(c, dict):
                                continue
                            eid = c.get("external_id") or ""
                            st = c.get("source_type") or ""
                            path = c.get("path") or (
                                f"{st}/{eid}.md" if eid and st else eid
                            )
                            web = c.get("web_url") or c.get("web_path") or ""
                            body = c.get("body_api") or c.get("body_api_url") or ""
                            if path:
                                sources.append(path)
                            cite_lines.append(
                                f"- {c.get('id') or ''} {c.get('title') or eid}\n"
                                f"  path: {path}\n"
                                f"  body_api: {body}\n"
                                f"  web_url: {web}\n"
                                f"  mcp: kb_get_document(path={path!r})"
                            )
    except httpx.HTTPError as e:
        return _err(e)

    answer = "".join(answer_parts).strip()
    if not answer:
        return "답변을 생성하지 못했습니다."
    if cite_lines:
        answer += "\n\n**출처 (원문 접근)**\n" + "\n".join(cite_lines)
    elif sources:
        answer += "\n\n**출처 path**: " + ", ".join(sources)
        answer += "\n원문: kb_get_document(path=…)"
    return answer


@mcp.tool()
async def kb_query(q: str, top_k: int = 10) -> str:
    """통합 의도 분류 질의 (검색/집계/유사장애/티켓목록 등). 홈 UI와 동일 플래너.
    예: '2026년 지원 유형', '지난 주 지원건', '모니모 Redis 타임아웃'
    """
    try:
        async with _client(timeout=120.0) as client:
            resp = await client.post(
                "/v1/query",
                json={"q": q, "include_search": True, "top_k": top_k},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    intent = data.get("intent") or "unknown"
    note = data.get("note") or ""
    range_label = data.get("range_label") or ""
    result = data.get("result") or {}
    lines = [
        f"intent={intent}"
        + (f" · {range_label}" if range_label else "")
        + (f"\nnote: {note}" if note else ""),
    ]

    if intent == "analytics" or result.get("group_by"):
        lines.append(
            f"group_by={result.get('group_by')} total={result.get('total')} "
            f"method={result.get('method')}"
        )
        for b in (result.get("buckets") or [])[:15]:
            lines.append(f"- {b.get('key')}: {b.get('count')}건 (share={b.get('share')})")
            for s in (b.get("samples") or [])[:3]:
                if not isinstance(s, dict):
                    continue
                path = s.get("path") or ""
                lines.append(
                    f"    · {s.get('external_id')} {s.get('title', '')[:60]}\n"
                    f"      path={path} body_api={s.get('body_api') or s.get('body_api_url') or ''}\n"
                    f"      web_url={s.get('web_url') or s.get('web_path') or ''}\n"
                    f"      mcp: kb_get_document(path={path!r})"
                )
    elif intent in {"hybrid_search", "exhaustive"} or result.get("items"):
        items = result.get("items") or result.get("results") or []
        lines.append(
            f"hits={result.get('total', len(items))} "
            f"(원문: kb_get_document(path=…) 또는 body_api/web_url)"
        )
        for it in items[:10]:
            if isinstance(it, dict):
                path = it.get("path") or ""
                lines.append(
                    f"- {it.get('title') or it.get('external_id')} "
                    f"[{it.get('source_type', '')}] score={it.get('score', '')}\n"
                    f"  path={path} body_api={it.get('body_api') or it.get('body_api_url') or ''}\n"
                    f"  web_url={it.get('web_url') or it.get('web_path') or ''}\n"
                    f"  mcp: kb_get_document(path={path!r})"
                )
    elif intent == "similar_incident":
        lines.append(f"brief: {result.get('brief', '')}")
        for c in (result.get("cases") or [])[:5]:
            if isinstance(c, dict):
                lines.append(
                    f"- {c.get('external_id')} {c.get('title')} "
                    f"적용성={ (c.get('applicability') or {}).get('label') }"
                )
    elif intent == "time_scoped_list":
        items = result.get("items") or []
        lines.append(f"total={result.get('total', len(items))}")
        for it in items[:15]:
            if isinstance(it, dict):
                lines.append(
                    f"- {it.get('external_id')} {it.get('title')} "
                    f"({it.get('Created') or it.get('date') or ''})"
                )
    else:
        # compact JSON fallback
        blob = json.dumps(result, ensure_ascii=False)[:3000]
        lines.append(blob)

    return "\n".join(lines)


@mcp.tool()
async def kb_ticket(external_id: str, source_type: str = "support_history") -> str:
    """지원 티켓 전체 본문을 조회한다. 예: CITECTS-2502"""
    eid = (external_id or "").strip()
    if not eid:
        return "오류: external_id 가 비어 있습니다."
    try:
        async with _client() as client:
            resp = await client.get(
                f"/v1/tickets/{eid}",
                params={"source_type": source_type or "support_history"},
            )
            if resp.status_code == 404:
                return f"오류: 티켓을 찾을 수 없습니다: {eid}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    return (
        f"# {data.get('title') or eid}\n"
        f"id={data.get('external_id')} · component={data.get('component')} · "
        f"status={data.get('status')} · assignee={data.get('assignee')}\n\n"
        f"{data.get('body_md') or '(본문 없음)'}"
    )


@mcp.tool()
async def kb_health() -> str:
    """citec-kb API 헬스 상태."""
    try:
        async with _client(timeout=10.0) as client:
            light = await client.get("/api/health")
            light.raise_for_status()
            full = await client.get("/v1/health")
            full_data: dict[str, Any] = {}
            if full.status_code == 200:
                full_data = full.json()
            light_data = light.json()
    except httpx.HTTPError as e:
        return _err(e)

    return (
        f"ok={light_data.get('ok')} version={light_data.get('version')} "
        f"service={light_data.get('service')}\n"
        f"full_status={full_data.get('status')} env={full_data.get('env')}\n"
        f"checks={json.dumps(full_data.get('checks') or {}, ensure_ascii=False)[:800]}"
    )


@mcp.tool()
async def kb_stats() -> str:
    """코퍼스 통계 (소스 타입별 문서 수)."""
    try:
        async with _client() as client:
            resp = await client.get("/api/wiki-stats")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    lines = [
        f"total_documents={data.get('total')} insights={data.get('insights')}",
        "by_source_type:",
    ]
    for k, v in sorted((data.get("by_source_type") or {}).items(), key=lambda x: -int(x[1] or 0)):
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


if __name__ == "__main__":
    # streamable-http: Claude Desktop remote / Docker (default)
    # stdio: local Claude Desktop / Claude Code subprocess
    if MCP_TRANSPORT in {"stdio", "std"}:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")
