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


def _access_lines(d: dict[str, Any], *, indent: str = "  ") -> str:
    path = d.get("path") or (d.get("access") or {}).get("path") or ""
    body = d.get("body_api") or d.get("body_api_url") or (d.get("access") or {}).get("body_api") or ""
    web = d.get("web_url") or d.get("web_path") or (d.get("access") or {}).get("web_url") or ""
    bits = []
    if path:
        bits.append(f"{indent}path: {path}")
        bits.append(f"{indent}mcp: kb_get_document(path={path!r})")
    if body:
        bits.append(f"{indent}body_api: {body}")
    if web:
        bits.append(f"{indent}web_url: {web}")
    return "\n".join(bits)


def _json_compact(data: Any, limit: int = 4000) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)[:limit]


# ── Tools (wiki-qa compatible names + citec-kb native) ────────────


@mcp.tool()
async def kb_search(
    query: str,
    section: str = "",
    area: str = "",
    category: str = "",
    limit: int = 10,
    environment: str = "",
    work_type: str = "",
    multi_query: bool = True,
    use_v1: bool = True,
) -> str:
    """CI-TEC 지식 하이브리드 검색 (FTS+vector).

    section/source_type: support_history|checkitems|tech_repo|tuning_ai|confluence_docs|…
    area: domain 필터 (os|dbms|network|cloud|storage|…)
    environment: csp|onprem|…  work_type: 기술지원|장애지원|…
    multi_query: 동의어·구문 확장 검색 (기본 true)
    use_v1: true면 POST /v1/search (필터 풍부), false면 GET /api/wiki/search
    """
    return await _search_impl(
        query,
        section=section,
        area=area,
        category=category,
        limit=limit,
        environment=environment,
        work_type=work_type,
        multi_query=multi_query,
        use_v1=use_v1,
    )


@mcp.tool()
async def wiki_search(
    query: str,
    section: str = "",
    area: str = "",
    category: str = "",
    limit: int = 10,
) -> str:
    """(wiki-qa 호환) kb_search 와 동일."""
    return await _search_impl(query, section=section, area=area, category=category, limit=limit)


async def _search_impl(
    query: str,
    section: str = "",
    area: str = "",
    category: str = "",
    limit: int = 10,
    environment: str = "",
    work_type: str = "",
    multi_query: bool = True,
    use_v1: bool = True,
) -> str:
    try:
        async with _client(timeout=60.0) as client:
            if use_v1:
                filters: dict[str, Any] = {"status": "active"}
                if section:
                    filters["source_type"] = section
                if area:
                    filters["domain"] = area
                if environment:
                    filters["environment"] = environment
                if work_type:
                    filters["work_type"] = work_type
                resp = await client.post(
                    "/v1/search",
                    json={
                        "q": query,
                        "top_k": min(max(limit, 1), 50),
                        "filters": filters,
                        "multi_query": multi_query,
                    },
                )
            else:
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

    results = data.get("results") or data.get("hits") or []
    if not results:
        return "검색 결과가 없습니다."

    lines = [
        f"검색 결과 {len(results)}건"
        + (f" total={data.get('total')}" if data.get("total") is not None else "")
        + (f" vector={data.get('vector_used')}" if "vector_used" in data else "")
        + (f" expanded={data.get('expanded_queries')}" if data.get("expanded_queries") else "")
        + ". 원문: kb_get_document(path=…)"
    ]
    for r in results:
        st = r.get("section") or r.get("source_type") or ""
        title = r.get("title", "(제목 없음)")
        score = r.get("score")
        score_s = f" score={score:.4f}" if isinstance(score, (int, float)) else ""
        eid = r.get("external_id") or ""
        lines.append(f"- [{st}] {title}{score_s}" + (f" ({eid})" if eid else ""))
        acc = _access_lines(r)
        if acc:
            lines.append(acc)
        snip = (r.get("snippet") or "")[:240]
        if snip:
            lines.append(f"  snippet: {snip}")
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
    """통합 의도 분류 질의 — 홈 UI와 동일 플래너 (권장 엔트리포인트).

    자동 분기: 기간 목록·집계·유사장애·체크리스트·용량·예방·하이브리드 검색.
    예:
      '지난 주 지원건', '2026년 SCP 유형 분류', '올해 월별 건수',
      '모니모 Redis 타임아웃 유사 장애', 'Linux OOM 체크리스트',
      '2026년 SCP v2 Multi-AZ 가용성 테스트가 있는가?'
    기간/집계를 파라미터로 직접 쓰려면 kb_list_tickets / kb_analytics 사용.
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

    return _format_query_response(data)


def _format_query_response(data: dict[str, Any]) -> str:
    intent = data.get("intent") or "unknown"
    note = data.get("note") or ""
    range_label = data.get("range_label") or ""
    result = data.get("result") or {}
    params = data.get("params") or {}
    lines = [
        f"intent={intent}"
        + (f" · range={range_label}" if range_label else "")
        + (f"\nnote: {note}" if note else ""),
    ]
    if params and intent != "hybrid_search":
        # useful plan hints
        for k in ("group_by", "date_from", "date_to", "relative", "component", "entity"):
            if params.get(k) is not None:
                lines.append(f"param.{k}={params.get(k)}")

    if intent == "analytics" or result.get("group_by"):
        lines.append(
            f"group_by={result.get('group_by')} total={result.get('total')} "
            f"method={result.get('method')}"
        )
        for b in (result.get("buckets") or [])[:20]:
            lines.append(f"- {b.get('key')}: {b.get('count')}건 (share={b.get('share')})")
            for s in (b.get("samples") or [])[:3]:
                if not isinstance(s, dict):
                    continue
                lines.append(
                    f"    · {s.get('external_id')} {str(s.get('title') or '')[:60]}"
                )
                acc = _access_lines(s, indent="      ")
                if acc:
                    lines.append(acc)
    elif intent in {"hybrid_search", "exhaustive", "prevention"} or (
        result.get("items") and intent not in {"time_scoped_list", "checklist"}
    ):
        items = result.get("items") or result.get("results") or []
        lines.append(f"hits={result.get('total', len(items))}")
        for it in items[:12]:
            if not isinstance(it, dict):
                continue
            lines.append(
                f"- {it.get('title') or it.get('external_id')} "
                f"[{it.get('source_type', '')}] score={it.get('score', '')}"
            )
            acc = _access_lines(it)
            if acc:
                lines.append(acc)
    elif intent == "similar_incident":
        lines.append(f"brief: {result.get('brief', '')}")
        for c in (result.get("cases") or [])[:8]:
            if not isinstance(c, dict):
                continue
            appl = (c.get("applicability") or {}).get("label") or ""
            lines.append(
                f"- {c.get('external_id')} {c.get('title')} 적용성={appl}"
            )
            acc = _access_lines(c)
            if acc:
                lines.append(acc)
    elif intent == "time_scoped_list":
        items = result.get("items") or []
        lines.append(f"total={result.get('total', len(items))}")
        for it in items[:20]:
            if not isinstance(it, dict):
                continue
            lines.append(
                f"- {it.get('external_id')} {it.get('title')} "
                f"({it.get('Created') or it.get('date') or it.get('created') or ''})"
            )
            acc = _access_lines(it)
            if acc:
                lines.append(acc)
    elif intent == "checklist":
        items = result.get("items") or result.get("checkitems") or []
        lines.append(f"checkitems={result.get('total', len(items))}")
        for it in items[:20]:
            if not isinstance(it, dict):
                continue
            lines.append(
                f"- {it.get('code')} {it.get('title') or it.get('subject') or ''}"
            )
            acc = _access_lines(it)
            if acc:
                lines.append(acc)
            else:
                code = it.get("code") or ""
                if code:
                    lines.append(f"  mcp: kb_get_checkitem(code={code!r})")
    elif intent == "capacity":
        lines.append(_json_compact(result, 3500))
    elif intent == "entity_aggregate":
        lines.append(_json_compact(result, 3500))
    else:
        lines.append(_json_compact(result, 3500))

    return "\n".join(lines)


# ── Structured search helpers (period / analytics / SI / checklist) ──


@mcp.tool()
async def kb_list_tickets(
    relative: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "Created",
    source_type: str = "support_history",
    limit: int = 30,
    offset: int = 0,
    order: str = "desc",
) -> str:
    """기간·날짜로 지원 티켓 목록 조회 (기간 조회 전용).

    relative 예: '지난 주', '이번 달', '올해', '최근 7일', '작년'
    또는 date_from/date_to: '2026-01-01' / '2026-06-30' (ISO date)
    date_field: Created|Resolved|Updated
    """
    params: dict[str, Any] = {
        "source_type": source_type or "support_history",
        "date_field": date_field or "Created",
        "limit": min(max(limit, 1), 200),
        "offset": max(offset, 0),
        "order": order or "desc",
    }
    if relative.strip():
        params["relative"] = relative.strip()
    if date_from.strip():
        params["date_from"] = date_from.strip()
    if date_to.strip():
        params["date_to"] = date_to.strip()
    try:
        async with _client() as client:
            resp = await client.get("/v1/tickets", params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    items = data.get("items") or data.get("tickets") or data.get("results") or []
    lines = [
        f"tickets total={data.get('total', len(items))} "
        f"range={data.get('range_label') or (date_from + '~' + date_to) or relative or 'all'}"
    ]
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        lines.append(
            f"- {it.get('external_id')} {it.get('title') or ''} "
            f"[{it.get('component') or ''}/{it.get('status') or ''}] "
            f"{it.get('Created') or it.get('created') or ''}"
        )
        acc = _access_lines(it)
        if acc:
            lines.append(acc)
        else:
            eid = it.get("external_id") or ""
            if eid:
                lines.append(f"  mcp: kb_ticket(external_id={eid!r})")
    if not items:
        lines.append("(결과 없음)")
    return "\n".join(lines)


@mcp.tool()
async def kb_analytics(
    group_by: str = "year",
    relative: str = "",
    date_from: str = "",
    date_to: str = "",
    date_field: str = "Created",
    source_type: str = "support_history",
    component: str = "",
    entity: str = "",
    top_k: int = 50,
) -> str:
    """티켓 집계/기간 통계 (LLM 없이 DB 집계).

    group_by: year|month|component|issue_type|status|assignee|total
    relative: '지난 주'|'이번 달'|'올해'|'작년' 등
    entity: 제목 ILIKE 필터 (예: SCP, 모니모)
    component: Jira Component (예: 장애지원)
    """
    params: dict[str, Any] = {
        "group_by": group_by or "year",
        "source_type": source_type or "support_history",
        "date_field": date_field or "Created",
        "top_k": min(max(top_k, 1), 200),
    }
    if relative.strip():
        params["relative"] = relative.strip()
    if date_from.strip():
        params["date_from"] = date_from.strip()
    if date_to.strip():
        params["date_to"] = date_to.strip()
    if component.strip():
        params["component"] = component.strip()
    if entity.strip():
        params["entity"] = entity.strip()
    try:
        async with _client() as client:
            resp = await client.get("/v1/analytics/tickets", params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    lines = [
        f"analytics group_by={data.get('group_by')} total={data.get('total')} "
        f"method={data.get('method')} range={data.get('range_label') or ''}"
    ]
    for b in (data.get("buckets") or [])[: top_k]:
        lines.append(f"- {b.get('key')}: {b.get('count')}건 (share={b.get('share')})")
        for s in (b.get("samples") or [])[:2]:
            if isinstance(s, dict):
                lines.append(f"    · {s.get('external_id')} {str(s.get('title') or '')[:50]}")
                acc = _access_lines(s, indent="      ")
                if acc:
                    lines.append(acc)
    return "\n".join(lines)


@mcp.tool()
async def kb_entity_share(
    entity: str,
    relative: str = "",
    date_from: str = "",
    date_to: str = "",
    source_type: str = "support_history",
) -> str:
    """특정 키워드(엔티티) 점유율/건수. 예: entity='SCP', relative='올해'."""
    if not (entity or "").strip():
        return "오류: entity 가 비어 있습니다."
    params: dict[str, Any] = {
        "entity": entity.strip(),
        "source_type": source_type or "support_history",
    }
    if relative.strip():
        params["relative"] = relative.strip()
    if date_from.strip():
        params["date_from"] = date_from.strip()
    if date_to.strip():
        params["date_to"] = date_to.strip()
    try:
        async with _client() as client:
            resp = await client.get("/v1/analytics/entity_share", params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return _json_compact(data, 3000)


@mcp.tool()
async def kb_title_tokens(
    component: str = "",
    source_type: str = "support_history",
    top_k: int = 20,
) -> str:
    """제목 토큰 빈도 (이슈 키워드 랭킹). component 예: 장애지원."""
    params: dict[str, Any] = {
        "source_type": source_type or "support_history",
        "top_k": min(max(top_k, 1), 100),
    }
    if component.strip():
        params["component"] = component.strip()
    try:
        async with _client() as client:
            resp = await client.get("/v1/analytics/title_tokens", params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    tokens = data.get("tokens") or data.get("items") or []
    lines = [f"title_tokens top_k={top_k} component={component or '(all)'}"]
    if isinstance(tokens, list):
        for t in tokens[:top_k]:
            if isinstance(t, dict):
                lines.append(f"- {t.get('token') or t.get('key')}: {t.get('count')}")
            else:
                lines.append(f"- {t}")
    else:
        lines.append(_json_compact(data, 2500))
    return "\n".join(lines)


@mcp.tool()
async def kb_similar_incident(
    symptom: str,
    environment: str = "",
    product: str = "",
    service: str = "",
    top_k: int = 3,
) -> str:
    """유사 장애(SI) 검색 — 증상 문장 기반 과거 사례 + 적용성.
    예: symptom='Redis timeout after deploy', product='모니모'
    """
    if not (symptom or "").strip():
        return "오류: symptom 이 비어 있습니다."
    body: dict[str, Any] = {
        "symptom": symptom.strip(),
        "top_k": min(max(top_k, 1), 10),
    }
    if environment.strip():
        body["environment"] = environment.strip()
    if product.strip():
        body["product"] = product.strip()
    if service.strip():
        body["service"] = service.strip()
    try:
        async with _client(timeout=90.0) as client:
            resp = await client.post("/v1/similar-incident", json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    lines = [f"SI brief: {data.get('brief') or ''}"]
    for c in (data.get("cases") or [])[: top_k]:
        if not isinstance(c, dict):
            continue
        appl = c.get("applicability") or {}
        lines.append(
            f"- {c.get('external_id')} {c.get('title')}\n"
            f"  적용성={appl.get('label')} score={c.get('score')}"
        )
        acc = _access_lines(c)
        if acc:
            lines.append(acc)
    if len(lines) == 1:
        lines.append("(유사 사례 없음)")
    return "\n".join(lines)


@mcp.tool()
async def kb_list_checkitems(
    q: str = "",
    area: str = "",
    category_1: str = "",
    limit: int = 30,
    offset: int = 0,
) -> str:
    """PISA 체크리스트 항목 검색/목록.
    q: 키워드(OOM, 파일시스템…), area: Linux|Oracle|Windows…
    """
    params: dict[str, Any] = {
        "limit": min(max(limit, 1), 200),
        "offset": max(offset, 0),
    }
    if q.strip():
        params["q"] = q.strip()
    if area.strip():
        params["area"] = area.strip()
    if category_1.strip():
        params["category_1"] = category_1.strip()
    try:
        async with _client() as client:
            resp = await client.get("/v1/checkitems", params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    items = data.get("items") or data.get("results") or []
    lines = [f"checkitems total={data.get('total', len(items))} q={q!r} area={area!r}"]
    for it in items[:limit]:
        if not isinstance(it, dict):
            continue
        code = it.get("code") or ""
        lines.append(f"- {code} {it.get('title') or it.get('subject') or ''}")
        acc = _access_lines(it)
        if acc:
            lines.append(acc)
        elif code:
            lines.append(f"  mcp: kb_get_checkitem(code={code!r})")
    if not items:
        lines.append("(결과 없음)")
    return "\n".join(lines)


@mcp.tool()
async def kb_capacity_estimate(
    period_days: int = 7,
    basis: str = "1안",
    include_pricing: bool = True,
) -> str:
    """용량/공수 추정 (규칙 기반, LLM 없음). period_days 지원 일수, basis 예: 1안."""
    try:
        async with _client() as client:
            resp = await client.post(
                "/v1/capacity/estimate",
                json={
                    "period_days": max(1, min(period_days, 365)),
                    "basis": basis or "1안",
                    "include_pricing": include_pricing,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return _json_compact(data, 4000)


@mcp.tool()
async def kb_tools_help() -> str:
    """MCP 도구 안내 — 어떤 검색/조회를 쓸지 선택."""
    return """citec-kb MCP 도구 가이드

[자연어 통합 — 먼저 시도]
  kb_query(q)     기간·집계·SI·체크리스트·용량·검색 자동 분기
                  예: "지난 주 지원건", "올해 SCP 유형", "유사 장애 Redis OOM"

[문서 검색]
  kb_search / wiki_search   하이브리드 검색 (필터: section, area, environment, work_type)
  kb_get_document / wiki_get_document   원문

[기간 조회 · 목록]
  kb_list_tickets(relative=|date_from=|date_to=)  티켓 기간 목록
                  예: relative="지난 주", date_from="2026-01-01", date_to="2026-03-31"

[집계 · 분석]
  kb_analytics(group_by=, relative=, entity=, component=)
                  group_by=year|month|component|issue_type|status|assignee|total
  kb_entity_share(entity=, relative=)
  kb_title_tokens(component=)

[유사 장애 · 체크리스트 · 용량]
  kb_similar_incident(symptom=, product=, environment=)
  kb_list_checkitems(q=, area=) / kb_get_checkitem(code=)
  kb_capacity_estimate(period_days=, basis=)

[티켓 · Insight · 상태]
  kb_ticket(external_id=)
  kb_list_insights / kb_get_insight
  kb_ask / wiki_ask   RAG 답변
  kb_health / kb_stats
"""


@mcp.tool()
async def kb_get_checkitem(code: str) -> str:
    """PISA 체크리스트 항목 원문(구조화 필드)을 조회한다. code 예: PISAOLNX_01.04.05
    또는 kb_search/list 결과의 path·code 사용."""
    code = (code or "").strip()
    if not code:
        return "오류: code 가 비어 있습니다."
    # allow path form checkitem/CODE.md
    if "/" in code:
        code = code.rstrip("/").split("/")[-1]
    if code.endswith(".md"):
        code = code[:-3]
    try:
        async with _client() as client:
            resp = await client.get(f"/v1/checkitems/{code}")
            if resp.status_code == 404:
                return f"오류: 체크항목을 찾을 수 없습니다: {code}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    lines = [
        f"# {data.get('title') or data.get('code')}",
        f"web_url: {data.get('web_url') or ''}",
        f"body_api: {data.get('body_api') or ''}",
        "",
    ]
    for sec in data.get("sections") or []:
        if not sec.get("value"):
            continue
        lines.append(f"## {sec.get('label') or sec.get('key')}")
        lines.append(str(sec.get("value")))
        lines.append("")
    return "\n".join(lines).strip()


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
