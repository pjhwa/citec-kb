#!/usr/bin/env python3
"""citec-kb MCP server — thin proxy over REST for Claude / Cursor / other MCP clients.

Calls the citec-kb API (wiki-qa compatible /api/* plus native /v1/* helpers).
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from ask_fold import KbAskError, fold_ask_events

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


def _headers(tool: Optional[str] = None) -> dict[str, str]:
    # 2026-10-14 — 운영자 admin 대시보드가 "AI가 MCP로 실제 뭘 조회하는지"를
    # 보여주려면, API 쪽이 이 요청이 MCP에서 왔다는 것과 어느 도구인지를 알아야
    # 한다. X-Citec-Client는 app.middleware.mcp_access가 가로채는 기준 헤더다.
    h: dict[str, str] = {"Accept": "application/json", "X-Citec-Client": "mcp-server"}
    if tool:
        h["X-Citec-Tool"] = tool
    if CITEC_KB_TOKEN:
        h["Authorization"] = f"Bearer {CITEC_KB_TOKEN}"
    return h


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    # 호출한 kb_* 도구 함수 이름을 콜스택에서 그대로 읽는다 — 이 모듈의 거의
    # 모든 도구 함수가 자기 본문에서 직접 _client(...)를 호출하는 한 단계
    # 패턴이라 안전하다. 다만 kb_search/wiki_search처럼 공용 `_search_impl`
    # 헬퍼를 거치는 경우가 있다(실측으로 발견 — 재배포 직후 mcp_access_log에
    # tool="_search_impl"로 찍힘, 어느 도구가 호출했는지 알 수 없게 됨).
    # `_`로 시작하는 private 헬퍼 프레임은 건너뛰고, 그 바로 바깥의 공개
    # 도구 함수 이름을 찾는다 — 33곳의 _client() 호출 어디도 수정할 필요가
    # 없다.
    caller = "unknown"
    for frame_info in inspect.stack()[1:]:
        name = frame_info.function
        if not name.startswith("_"):
            caller = name
            break
    return httpx.AsyncClient(
        base_url=CITEC_KB_BASE_URL.rstrip("/"),
        timeout=timeout,
        headers=_headers(tool=caller),
    )


def _api_error_detail(resp: httpx.Response) -> str:
    """citec-kb API의 오류 응답에서 detail 메시지를 추출한다.
    resp.raise_for_status()만 쓰면 이 detail이 사라지고 일반 HTTP 문구만 남는다."""
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("detail"):
            return str(data["detail"])
    except Exception:
        pass
    return (resp.text or f"HTTP {resp.status_code}")[:300]


def _classify_http_error(e: Exception) -> dict[str, Any]:
    """F01(2026-10-13 최종 핸드오프) — httpx 예외를 기계가 판별 가능한 범주로
    분류한다. 과거엔 모든 httpx.HTTPError(요청 검증 4xx 포함)를 "연결할 수
    없습니다"로 뭉개서, offset=-1 같은 순수 입력 오류를 연결 장애로 잘못
    안내했다(실제 재현: comparison/raw/invalid_offset.json, 2026-10-13 최종
    핸드오프). retryable은 호출자가 재시도해볼 가치가 있는지의 대략적 신호다
    — 내부 endpoint URL/stack/자격증명은 절대 포함하지 않는다(httpx의 기본
    HTTPStatusError 문자열에는 요청 URL이 들어있어 그대로 쓰면 그게 샌다)."""
    if isinstance(e, httpx.TimeoutException):
        return {
            "status": "backend_error", "error_code": "timeout", "retryable": True,
            "http_status": None, "detail": "요청 시간이 초과됐습니다", "field_errors": None,
        }
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        detail = _api_error_detail(e.response)
        field_errors = None
        try:
            body = e.response.json()
            if isinstance(body, dict) and isinstance(body.get("detail"), list):
                field_errors = body["detail"]
        except Exception:
            pass
        if code in (400, 422):
            return {
                "status": "invalid_request", "error_code": "validation_error", "retryable": False,
                "http_status": code, "detail": detail, "field_errors": field_errors,
            }
        if code == 404:
            return {
                "status": "not_found", "error_code": "not_found", "retryable": False,
                "http_status": code, "detail": detail, "field_errors": None,
            }
        if code in (401, 403):
            return {
                "status": "invalid_request", "error_code": "unauthorized", "retryable": False,
                "http_status": code, "detail": detail, "field_errors": None,
            }
        if 500 <= code < 600:
            return {
                "status": "backend_error", "error_code": "server_error", "retryable": True,
                "http_status": code, "detail": detail, "field_errors": None,
            }
        return {
            "status": "backend_error", "error_code": "http_error", "retryable": False,
            "http_status": code, "detail": detail, "field_errors": None,
        }
    # httpx.ConnectError/NetworkError 등 — 실제로 API에 연결 자체가 안 된 경우만 여기로 온다.
    return {
        "status": "backend_error", "error_code": "connection_error", "retryable": True,
        "http_status": None, "detail": "citec-kb API에 연결할 수 없습니다", "field_errors": None,
    }


def _err(e: Exception) -> str:
    """기존 ~30개 도구가 공유하는 범용 오류 문자열 — F01: 4xx 요청 오류를
    더 이상 "연결 장애"로 뭉뚱그리지 않는다. 구조화된 isError/structuredContent
    전체 계약은 kb_graph_explore에만 적용한다(나머지 도구까지 전부 바꾸는 건
    이번 범위 밖) — 이 함수는 그 외 도구들에 최소 침습으로 상태 구분만
    더한다."""
    info = _classify_http_error(e)
    if info["status"] == "invalid_request":
        return f"status: invalid_request\n오류: 요청 값이 올바르지 않습니다 (HTTP {info['http_status']}): {info['detail']}"
    if info["status"] == "not_found":
        return f"status: not_found\n오류: 대상을 찾을 수 없습니다: {info['detail']}"
    return f"status: backend_error\n오류: {info['detail']} ({info['error_code']})"


def _access_lines(d: dict[str, Any], *, indent: str = "  ") -> str:
    """Render access/freshness hints for a search/citation row.

    P0-A (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md §4
    item 3/9): this used to unconditionally print
    "mcp: kb_get_document(path=...)" for every hit, including confluence_map
    rows — which makes kb_get_document sound like it returns the map's full
    current body when it only re-returns the same ~400-char stored snapshot
    (evidence_eligible=false). It now labels that call as a snapshot fetch
    and, when the API supplied verify_via (map hits with a pageId), also
    surfaces the confluence-mcp call that actually reads the live page.
    """
    acc = d.get("access") or {}
    path = d.get("path") or acc.get("path") or ""
    body = d.get("body_api") or d.get("body_api_url") or acc.get("body_api") or ""
    web = d.get("web_url") or d.get("web_path") or acc.get("web_url") or ""
    body_kind = d.get("body_kind") or acc.get("body_kind")
    verify_via = d.get("verify_via") or acc.get("verify_via")
    evidence_eligible = d.get("evidence_eligible")
    map_synced_at = d.get("map_synced_at")
    bits = []
    if path:
        bits.append(f"{indent}path: {path}")
        label = "kb_get_document snapshot 조회" if body_kind == "snapshot" else "kb_get_document"
        bits.append(f"{indent}mcp: {label}(path={path!r})")
    if body:
        bits.append(f"{indent}body_api: {body}")
    if web:
        bits.append(f"{indent}web_url: {web}")
    if evidence_eligible is False:
        bits.append(f"{indent}⚠ pointer만 있음 — 원문 미확인 (evidence_eligible=false)")
    if map_synced_at:
        bits.append(f"{indent}map_synced_at: {map_synced_at} (원문 실시간 확인 아님)")
    if verify_via and isinstance(verify_via, dict):
        vt = verify_via.get("tool")
        va = verify_via.get("args") or {}
        bits.append(f"{indent}원문 확인: {vt}({', '.join(f'{k}={v!r}' for k, v in va.items())})")
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
    exclude_page_ids: Optional[list[str]] = None,
    exclude_source_types: Optional[list[str]] = None,
    exclude_subtree_ids: Optional[list[str]] = None,
    include_irrelevant_maps: Optional[bool] = None,
    diversify_copies: bool = True,
) -> str:
    """CI-TEC 지식 하이브리드 검색 (FTS+vector).

    section/source_type: support_history|checkitems|tech_repo|tuning_ai|confluence_docs|incident_reports|dept_archive|…
    area: domain 필터 (os|dbms|network|cloud|storage|…)
    environment: csp|onprem|…  work_type: 기술지원|장애지원|…
    exclude_page_ids: 결과에서 뺄 external_id — 그 페이지 하나만 빠지고 하위 페이지는 남는다
    exclude_subtree_ids: 그 페이지와 모든 하위 페이지를 뺀다 (정답 누출 방지에는 이쪽을 쓸 것.
        하위 관계는 confluence_map 색인에만 있다)
    exclude_source_types: 결과에서 뺄 source_type
    include_irrelevant_maps: 기술 무관 confluence_map 포함 여부. 미지정이면 section=confluence_map
        일 때만 포함(필터 꺼짐). 그 섹션에서도 필터를 유지하려면 false 를 명시
    diversify_copies: confluence_map 의 사본/Copy of/백업 페이지를 최고 점수 1건으로 접는다 (기본 true)
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
        exclude_page_ids=exclude_page_ids,
        exclude_source_types=exclude_source_types,
        exclude_subtree_ids=exclude_subtree_ids,
        include_irrelevant_maps=include_irrelevant_maps,
        diversify_copies=diversify_copies,
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
    exclude_page_ids: Optional[list[str]] = None,
    exclude_source_types: Optional[list[str]] = None,
    exclude_subtree_ids: Optional[list[str]] = None,
    include_irrelevant_maps: Optional[bool] = None,
    diversify_copies: bool = True,
) -> str:
    # P0-A / D11 (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md
    # §4 item 5, §5): the legacy GET /api/wiki/search path only forwards
    # q/section/area/category/limit — every other filter used to be silently
    # dropped when use_v1=False, which is exactly how an exclusion meant to
    # keep an evaluation's own answer page out of results (exclude_subtree_ids
    # etc.) could pass validation while doing nothing. A caller-requested
    # filter that legacy cannot carry now forces the v1 path instead of being
    # dropped; this is reported back, never silent.
    #
    # The reverse also holds: SearchFilters (v1) has no `category` field at
    # all — it is a legacy-only concept, not something v1 can be asked to
    # carry. So auto-upgrading to v1 while `category` is set would silently
    # drop *that* filter instead, the same bug in the other direction. When
    # both are requested together there is no side that carries both, so
    # this returns an explicit unsupported-combination error rather than
    # silently picking one (§4 item 5's other option).
    _v1_only_filters_requested = bool(
        exclude_page_ids
        or exclude_source_types
        or exclude_subtree_ids
        or environment
        or work_type
        or include_irrelevant_maps is not None
        or not diversify_copies
    )
    if not use_v1 and category and _v1_only_filters_requested:
        return (
            "오류: category와 exclude_*/environment/work_type/include_irrelevant_maps/"
            "diversify_copies=false를 함께 쓸 수 없습니다 — category는 legacy 전용이고 "
            "나머지는 v1 전용이라 어느 한쪽도 둘 다 지원하지 않습니다. use_v1=true로 "
            "category 없이 다시 호출하거나, category를 빼고 호출하세요."
        )
    _auto_upgraded_to_v1 = (not use_v1) and _v1_only_filters_requested
    if _auto_upgraded_to_v1:
        use_v1 = True
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
                if exclude_page_ids:
                    filters["exclude_page_ids"] = list(exclude_page_ids)
                if exclude_source_types:
                    filters["exclude_source_types"] = list(exclude_source_types)
                if exclude_subtree_ids:
                    filters["exclude_subtree_ids"] = list(exclude_subtree_ids)
                if include_irrelevant_maps is not None:
                    filters["include_irrelevant_maps"] = include_irrelevant_maps
                if not diversify_copies:
                    filters["diversify_copies"] = False
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
        if _auto_upgraded_to_v1:
            return "검색 결과가 없습니다. (참고: use_v1=false였지만 지원되지 않는 필터가 있어 v1로 자동 전환했습니다)"
        return "검색 결과가 없습니다."

    lines = [
        f"검색 결과 {len(results)}건"
        + (f" total={data.get('total')}" if data.get("total") is not None else "")
        + (
            f" total_candidates={data.get('total_candidates')}"
            if data.get("total_candidates") is not None
            else ""
        )
        + (f" vector={data.get('vector_used')}" if "vector_used" in data else "")
        + (f" expanded={data.get('expanded_queries')}" if data.get("expanded_queries") else "")
        + (
            " [note: use_v1=false였지만 지원되지 않는 필터가 있어 v1로 자동 전환]"
            if _auto_upgraded_to_v1
            else ""
        )
        + ". 원문: kb_get_document(path=…)"
    ]
    for r in results:
        st = r.get("section") or r.get("source_type") or ""
        title = r.get("title", "(제목 없음)")
        score = r.get("score")
        score_s = f" score={score:.4f}" if isinstance(score, (int, float)) else ""
        eid = r.get("external_id") or ""
        dup = r.get("duplicate_count") or 0
        dup_s = f" (사본 {dup}건 접힘)" if dup else ""
        lines.append(f"- [{st}] {title}{score_s}" + (f" ({eid})" if eid else "") + dup_s)
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
async def kb_list_insights(limit: int = 20, status: str = "approved") -> str:
    """승인된 Insight 목록. status=review|draft|rejected|all 로만 그 외를 본다."""
    return await _list_synthesis_impl(limit, status)


@mcp.tool()
async def wiki_list_synthesis(limit: int = 20) -> str:
    """(wiki-qa 호환) kb_list_insights 와 동일 — synthesis≈insights."""
    return await _list_synthesis_impl(limit)


async def _list_synthesis_impl(limit: int, status: str = "approved") -> str:
    try:
        async with _client() as client:
            resp = await client.get("/api/synthesis", params={"limit": limit, "status": status or "approved"})
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
async def kb_ask(
    query: str,
    template: str = "general",
    mode: str = "fast",
    exclude_page_ids: Optional[list[str]] = None,
    exclude_source_types: Optional[list[str]] = None,
    exclude_subtree_ids: Optional[list[str]] = None,
    include_irrelevant_maps: Optional[bool] = None,
) -> str:
    """CI-TEC 지식 근거로 자연어 질문에 답변한다 (RAG + Trust).
    template: general|checkitems|support_history|tech_repo|tuning_ai|incident_reports|synthesis
    mode: fast|deep
    exclude_page_ids/exclude_subtree_ids/exclude_source_types/include_irrelevant_maps:
        kb_search/kb_query와 동일한 제외 정책. 독립 평가에서 자신의 정답 문서를
        근거로 쓰지 않으려면 지정할 것 — 이전에는 kb_ask에 이 인자가 없어
        /api/query(WikiQueryRequest)가 이미 지원하는 제외 옵션을 MCP에서 쓸 수
        없었다(REVIEW.md §2 kb_ask 인자 불일치).
    """
    return await _ask_impl(
        query,
        template,
        mode,
        exclude_page_ids=exclude_page_ids,
        exclude_source_types=exclude_source_types,
        exclude_subtree_ids=exclude_subtree_ids,
        include_irrelevant_maps=include_irrelevant_maps,
    )


@mcp.tool()
async def wiki_ask(query: str, template: str = "general") -> str:
    """(wiki-qa 호환) kb_ask 와 동일 (mode=fast)."""
    return await _ask_impl(query, template, "fast")


async def _ask_impl(
    query: str,
    template: str,
    mode: str,
    *,
    exclude_page_ids: Optional[list[str]] = None,
    exclude_source_types: Optional[list[str]] = None,
    exclude_subtree_ids: Optional[list[str]] = None,
    include_irrelevant_maps: Optional[bool] = None,
) -> str:
    events: list[dict] = []
    body: dict[str, Any] = {
        "query": query,
        "template": template or "general",
        "mode": mode if mode in {"fast", "deep"} else "fast",
        "stream": True,
    }
    if exclude_page_ids:
        body["exclude_page_ids"] = list(exclude_page_ids)
    if exclude_source_types:
        body["exclude_source_types"] = list(exclude_source_types)
    if exclude_subtree_ids:
        body["exclude_subtree_ids"] = list(exclude_subtree_ids)
    if include_irrelevant_maps is not None:
        body["include_irrelevant_maps"] = include_irrelevant_maps
    try:
        async with _client(timeout=180.0) as client:
            async with client.stream(
                "POST",
                "/api/query",
                json=body,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        event = json.loads(line[len("data: ") :])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict):
                        events.append(event)
    except httpx.HTTPError as e:
        raise KbAskError(_err(e)) from e
    return fold_ask_events(events)


@mcp.tool()
async def kb_query(
    q: str,
    top_k: int = 10,
    exclude_page_ids: Optional[list[str]] = None,
    exclude_source_types: Optional[list[str]] = None,
    exclude_subtree_ids: Optional[list[str]] = None,
    include_irrelevant_maps: Optional[bool] = None,
    diversify_copies: bool = True,
) -> str:
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
            body: dict[str, Any] = {"q": q, "include_search": True, "top_k": top_k}
            if exclude_page_ids:
                body["exclude_page_ids"] = list(exclude_page_ids)
            if exclude_source_types:
                body["exclude_source_types"] = list(exclude_source_types)
            if exclude_subtree_ids:
                body["exclude_subtree_ids"] = list(exclude_subtree_ids)
            if include_irrelevant_maps is not None:
                body["include_irrelevant_maps"] = include_irrelevant_maps
            if not diversify_copies:
                body["diversify_copies"] = False
            resp = await client.post("/v1/query", json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    return _format_query_response(data)


def _format_entity_share(result: dict[str, Any]) -> str:
    """엔티티 점유율 — 분자/분모를 라벨과 함께 보여준다(`total`은 분모임을 명시)."""
    rng = ""
    if result.get("date_from") or result.get("date_to"):
        rng = f" range={result.get('range_label') or ''} {result.get('date_from') or ''}~{result.get('date_to') or ''}".rstrip()
    lines = [
        f"entity_share entity={result.get('entity')!r} source_type={result.get('source_type') or '(default)'}{rng}",
        f"matched(엔티티 포함 건수)={result.get('matched')} / total(분모: 같은 기간·소스 전체)={result.get('total')}"
        f" → share={result.get('share')}",
        f"method={result.get('method')}",
    ]
    if result.get("definition"):
        lines.append(f"정의: {result['definition']}")
    return "\n".join(lines)


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

    if isinstance(result, dict) and "matched" in result and "share" in result and not result.get("group_by"):
        # entity_share 모드: `total`은 분모(전체)이므로 건수로 오해하지 않게 따로 포맷한다.
        lines.append(_format_entity_share(result))
    elif intent == "analytics" or result.get("group_by"):
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
        cand = result.get("total_candidates")
        lines.append(
            f"hits={result.get('total', len(items))}"
            + (f" total_candidates={cand}" if cand is not None else "")
        )
        for it in items[:12]:
            if not isinstance(it, dict):
                continue
            dup = it.get("duplicate_count") or 0
            lines.append(
                f"- {it.get('title') or it.get('external_id')} "
                f"[{it.get('source_type', '')}] score={it.get('score', '')}"
                + (f" (사본 {dup}건 접힘)" if dup else "")
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
async def kb_recent_changes(
    relative: str = "",
    date_from: str = "",
    date_to: str = "",
    change_type: str = "modified",
    source_types: str = "",
    space_key: str = "",
    title_contains: str = "",
    include_folders: bool = False,
    limit: int = 30,
    offset: int = 0,
) -> str:
    """Confluence 문서의 최근 수정/신규 목록 조회 (confluence_map·confluence_docs·tech_repo).

    기간: relative 예 '최근 7일', '지난 주', '이번 달' 또는 date_from/date_to(ISO date).
          아무것도 안 주면 최근 7일.
    change_type:
      modified(기본) = Confluence에서 수정된 시각(source_modified_at) 기준
      added          = KB가 처음 수집한 시각 기준 (Confluence 페이지 생성일이 아님)
      all            = 둘 중 하나라도 기간 내
    source_types: 콤마 구분(기본 3종 전부). space_key: 예 'DevOps001','LOOKIN','TechRepo'.
    include_folders=False면 confluence_map의 폴더(이정표) 페이지는 제외.
    KB 반영은 Confluence 수정 후 최대 수 시간 지연될 수 있다(confluence_sync 2시간, map_sync 하루 3회).
    수정 시각 정보가 없는 문서는 modified 결과에서 빠지며 결과 머리에 건수를 표시한다.
    """
    params: dict[str, Any] = {
        "change_type": change_type or "modified",
        "include_folders": bool(include_folders),
        "limit": min(max(limit, 1), 200),
        "offset": max(offset, 0),
    }
    for key, val in (
        ("relative", relative),
        ("date_from", date_from),
        ("date_to", date_to),
        ("source_types", source_types),
        ("space_key", space_key),
        ("title_contains", title_contains),
    ):
        if val and val.strip():
            params[key] = val.strip()
    try:
        async with _client() as client:
            resp = await client.get("/v1/documents/recent-changes", params=params)
            if resp.status_code == 400:
                return f"status: invalid_request\n오류: {_api_error_detail(resp)}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return _format_recent_changes(data)


def _format_recent_changes(data: dict[str, Any]) -> str:
    rng = data.get("range") or {}
    items = data.get("items") or []
    label = rng.get("label") or f"{rng.get('date_from')}~{rng.get('date_to')}"
    lines = [
        f"recent_changes total={data.get('total', len(items))} change_type={data.get('change_type')} "
        f"range={label} ({rng.get('date_from')}~{rng.get('date_to')}, KST) "
        f"offset={data.get('offset', 0)} shown={len(items)}"
    ]
    missing = data.get("missing_modified_at") or 0
    if missing:
        lines.append(
            f"참고: 수정 시각 정보가 없는 문서 {missing}건은 modified 결과에서 제외됨 "
            "(added/all의 KB 수집 시각 기준에는 포함될 수 있음)."
        )
    if data.get("change_type") != "modified":
        lines.append("참고: added는 KB가 처음 수집한 시각이며 Confluence 페이지 생성일이 아닙니다.")
    for it in items:
        if not isinstance(it, dict):
            continue
        lines.append(
            f"- [{it.get('change')}] {it.get('title')} "
            f"(space={it.get('space_key')}, type={it.get('source_type')}, page_id={it.get('page_id')}, "
            f"modified={it.get('source_modified_at') or '-'}, kb_created={it.get('kb_created_at') or '-'}, "
            f"v={it.get('source_version') if it.get('source_version') is not None else '-'})"
        )
        if it.get("path"):
            lines.append(f"    path: {it['path']}")
        if it.get("url"):
            lines.append(f"    url: {it['url']}")
    if not items:
        lines.append("조건에 맞는 변경이 없습니다.")
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
    """특정 키워드(엔티티) 점유율/건수. 예: entity='SCP', relative='올해'.

    matched = 같은 기간·소스에서 제목/ID/본문 앞부분에 엔티티 문자열이 있는 건수
    (kb_analytics entity= 와 같은 정의), total = 같은 기간·소스 전체 건수(분모).
    """
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
    return _format_entity_share(data)


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
    # API(`app.analytics.title_tokens`)는 `buckets`에 {token,count,share}를 담는다.
    # `tokens`/`items`는 예전 응답 형태 호환용 폴백.
    tokens = data.get("buckets") or data.get("tokens") or data.get("items") or []
    n_docs = data.get("document_n")
    uniq = data.get("unique_tokens")
    head = f"title_tokens top_k={top_k} component={component or '(all)'}"
    if n_docs is not None:
        head += f" source_type={data.get('source_type') or source_type} document_n={n_docs}"
    if uniq is not None:
        head += f" unique_tokens={uniq}"
    lines = [head]
    if isinstance(tokens, list):
        for t in tokens[:top_k]:
            if isinstance(t, dict):
                row = f"- {t.get('token') or t.get('key')}: {t.get('count')}"
                if isinstance(t.get("share"), (int, float)):
                    row += f" ({t['share'] * 100:.1f}%)"
                lines.append(row)
            else:
                lines.append(f"- {t}")
        if not tokens:
            lines.append("(조건에 맞는 제목 토큰이 없습니다 — component/source_type 값을 확인하세요)")
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
    source_types: Optional[list[str]] = None,
) -> str:
    """유사 장애(SI) 검색 — 증상 문장 기반 과거 사례 + 적용성.
    예: symptom='Redis timeout after deploy', product='모니모'

    기본은 지원이력(support_history)과 SWIM 장애(incident_reports)를 함께 검색한다.
    source_types=['incident_reports'] 처럼 지정하면 그 소스만 본다.
    environment 는 필터가 아니라 가중치다 — 환경이 같으면 올리고 다르면 내리되, 환경 정보가 없는
    사례(SWIM 의 대부분)는 제외하지 않는다.
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
    if source_types:
        body["source_types"] = list(source_types)
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
        src = f"[{c['source_type']}] " if c.get("source_type") else ""
        lines.append(
            f"- {src}{c.get('external_id')} {c.get('title')}\n"
            f"  적용성={appl.get('label')} score={c.get('score')}"
        )
        acc = _access_lines(c)
        if acc:
            lines.append(acc)
    if len(lines) == 1:
        lines.append("(유사 사례 없음)")
    return "\n".join(lines)


@mcp.tool()
async def kb_graph_explore(
    anchor_type: str,
    anchor_value: str,
    exclude_page_ids: str = "",
    exclude_subtree_ids: str = "",
    exclude_source_types: str = "",
    documents_offset: int = 0,
    components_offset: int = 0,
    failure_buckets_offset: int = 0,
    include_archived: bool = False,
) -> str:
    """장애 분석 시 연관 컴포넌트·문서·과거 장애를 지식그래프에서 탐색한다(2hop).

    anchor_type: "failure_bucket" | "document" | "component" | "symptom_text"
    anchor_value: 각각 FailureBucket.id / documents.id 또는 external_id /
                  컴포넌트 이름(예: "NetApp", "넷앱") / 자유 텍스트 증상 설명
    exclude_page_ids/exclude_subtree_ids/exclude_source_types: 쉼표구분 선택 인자 —
                  평가/검증 보고서처럼 결과에서 빼야 할 페이지·하위트리·source_type을
                  candidate 단계에서 제외한다(일반 업무 질문에는 보통 비워 둔다).
    documents_offset/components_offset/failure_buckets_offset: 한 카테고리의
                  전체 후보가 50건을 넘으면("N건 중 X-Y건 표시" + 다음 페이지
                  안내로 알 수 있음) 그 수만큼 올려 재호출한다(예: documents_offset=50).
    include_archived: 기본 False — archived 문서는 "연관 문서" 목록에서
                  기본 제외된다(pg_missing은 대상 아님 — 결과의 pg_missing
                  플래그로 계속 노출만 됨). 과거 이력/감사 목적으로 비활성
                  문서까지 보고 싶을 때만 True로 준다. 단, archived
                  문서가 다른 두 문서 사이의 중간 경로(bridge)로 쓰이는 것
                  자체는 이 옵션과 무관하게 막히지 않는다(알려진 한계).

    응답 첫 줄 status(found/not_found/no_match/isolated)로 성공 여부를 판단할
    것 — 문장으로 추측하지 말 것. 각 결과의 [Nhop 경로]는 anchor부터 그
    노드까지의 전체 경로다(마지막 관계 하나가 아님) — PARENT_OF는 ↑(anchor가
    자식 쪽)/↓(anchor가 부모 쪽), 그 외 관계는 →/←. document 앵커는 같은
    Confluence 페이지의 여러 수집본(confluence_map/tech_repo/confluence_docs)
    중 어느 쪽으로 걸어도 같은 계층·관계를 찾는다(대표 1건이 anchor.resolved_id
    로 표시됨). "연관 문서" 결과도 같은 페이지의 다른 수집본이 있으면
    copies=N으로 묶여 나온다(_EN 접미사 체크항목 등은 안 묶임).
    archived는 비활성화된 원문이라는 뜻 — 결론에 그대로 쓰지 말 것.
    각 줄의 "원문: kb_get_document(path=...)"가 실제 호출 경로다 —
    confluence_map 소스는 본문이 발췌(snapshot)뿐이니 참고.

    그래프는 하루 1회 배치로 갱신되므로(응답의 as_of 날짜 참고) 그 이후 변경은
    반영 안 돼 있을 수 있다 — 최종 확인은 kb_search/kb_get_document로.
    쓰임새: kb_match_failure_bucket/kb_similar_incident로 1차 후보를 찾은 다음,
    그 주변을 더 깊이 파는 용도. 검색의 대체재가 아니다.
    """
    # R07(2026-10-13 2차 핸드오프) §2.7 — found/not_found/no_match/filtered/
    # isolated/backend_error뿐 아니라 invalid_request도 기계가 읽는 status로
    # 노출한다. 과거엔 이 두 분기가 "오류: ..." 문장만 돌려줘 isError=false와
    # 구조화된 status 라인이 전혀 없었다(V17 요구 위반).
    if anchor_type not in ("failure_bucket", "document", "component", "symptom_text"):
        return (
            "status: invalid_request\n"
            f"오류: anchor_type은 failure_bucket|document|component|symptom_text 중 하나여야 합니다 (받음: {anchor_type})"
        )
    if not (anchor_value or "").strip():
        return "status: invalid_request\n오류: anchor_value가 비어 있습니다."

    def _split(s: str) -> list[str]:
        return [t.strip() for t in (s or "").split(",") if t.strip()]

    try:
        async with _client(timeout=30.0) as client:
            resp = await client.post(
                "/v1/graph/explore",
                json={
                    "anchor_type": anchor_type, "anchor_value": anchor_value.strip(),
                    "exclude_page_ids": _split(exclude_page_ids),
                    "exclude_subtree_ids": _split(exclude_subtree_ids),
                    "exclude_source_types": _split(exclude_source_types),
                    "documents_offset": documents_offset,
                    "components_offset": components_offset,
                    "failure_buckets_offset": failure_buckets_offset,
                    "include_archived": include_archived,
                },
            )
            if resp.status_code == 404:
                # R07(2026-10-13 2차 핸드오프) §2.7 실제 재현 — 이 분기만 status
                # 라인이 없는 문장 하나를 돌려줘서 isError=false와 함께 보면
                # "성공"처럼 보였다(unknown_component 케이스, raw evidence로
                # 재현됨). no_match(symptom_text)는 이미 status를 포함하므로
                # 여기도 같은 포맷으로 맞춘다.
                return (
                    "status: not_found\n"
                    f"그래프에서 '{anchor_value}'를 찾을 수 없습니다 (as-of 데이터 기준)."
                )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        # F01(2026-10-13 최종 핸드오프) — kb_graph_explore만 전체 구조화 계약을
        # 쓴다(§2.1.2-4). mcp.types.CallToolResult를 직접 반환하면 FastMCP가
        # 함수 반환형 애너테이션(-> str)으로 만든 output_schema 변환을 건너뛰고
        # 이 값을 그대로 쓴다(FuncMetadata.convert_result가 CallToolResult
        # instance를 최우선으로 통과시킴, mcp SDK 1.29.0 확인) — 기존 텍스트만
        # 읽는 클라이언트와의 호환은 content[0].text로 유지되고, 기계가 읽는
        # 클라이언트는 structuredContent로 status/error_code/retryable/
        # field_errors를 문자열 정규식 없이 판별할 수 있다.
        info = _classify_http_error(e)
        text = (
            f"status: {info['status']}\n"
            f"오류: {info['detail']}"
            + (f" (HTTP {info['http_status']})" if info["http_status"] else "")
        )
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structuredContent={
                # "result"는 기존(-> str) 도구 계약이 advertise한 outputSchema가
                # 요구하는 필드다 — 이미 그 스키마를 캐시해둔 클라이언트가
                # 재연결 전에도 깨지지 않도록 그대로 유지하고, F01이 요구하는
                # 기계 판별 필드들을 같이 추가한다(모델이 extra 필드를 막지
                # 않는 한 두 계약이 공존한다).
                "result": text,
                "status": info["status"],
                "error_code": info["error_code"],
                "retryable": info["retryable"],
                "http_status": info["http_status"],
                "field_errors": info["field_errors"],
            },
            isError=True,
        )

    lines = [f"status: {data.get('status', '(unknown)')}", f"as_of: {data.get('as_of') or '(아직 백필 안 됨)'}"]
    # F04(2026-10-13 최종 핸드오프) §7/8 — as_of는 MAX(synced_at)일 뿐,
    # "마지막 전체 배치가 언제·어떤 상태로 끝났는지"는 알려주지 않는다.
    # 그 둘을 혼동하지 말라는 요구에 따라 한 줄 더 보여준다.
    fr = data.get("freshness") or {}
    if fr.get("full_run_in_progress"):
        lines.append("freshness: 지금 새 전체 배치가 진행 중 — 이 응답은 혼합 상태일 수 있음")
    elif fr.get("latest_full_completed_at"):
        warn = " (일부 실패 포함)" if fr.get("latest_full_completed_status") == "completed_warn" else ""
        lines.append(f"freshness: 마지막 완료된 전체 배치 {fr['latest_full_completed_at']}{warn}")
    else:
        lines.append("freshness: 완료된 전체 배치 기록 없음(unknown)")
    anchor = data.get("anchor") or {}
    if anchor.get("type") == "symptom_text":
        mc = anchor.get("matched_components") or []
        lines.append(
            f"매칭된 컴포넌트: {', '.join(mc)}" if mc
            else "매칭된 컴포넌트: 없음 — 증상 텍스트에서 알려진 컴포넌트를 못 찾음(kb_similar_incident 권장)"
        )
    elif anchor.get("resolved_name") or anchor.get("resolved_id"):
        lines.append(f"해석된 앵커: {anchor.get('resolved_name') or anchor.get('resolved_id')}")
    if anchor.get("type") == "document" and anchor.get("representative_excluded"):
        # F05(2026-10-13 최종 핸드오프) §2.5.4/§2.5.6 — 대표 사본이
        # exclude_source_types 등으로 제외됐는데도 resolved_id가 여전히 그
        # 사본을 가리키는 경우, 실제 탐색이 시작된 사본(traversal_start_ids)
        # 과 다르다는 걸 명시한다 — 안 그러면 응답의 resolved_id만 보고
        # "그 문서로 탐색했다"고 오해하기 쉽다.
        starts = anchor.get("traversal_start_ids") or []
        lines.append(
            f"주의: 대표 사본({anchor.get('resolved_id')})이 제외 조건에 걸려, "
            f"실제 탐색은 다른 사본으로 시작함: {', '.join(starts) if starts else '(남은 사본 없음)'}"
        )
    if anchor.get("type") == "failure_bucket":
        # F02(2026-10-13 최종 핸드오프) — 조회한 bucket 자신의 사유. 과거엔
        # 이웃 bucket(_render_failure_bucket_line) 줄에만 evidence_reasons가
        # 붙고 anchor 자신에는 안 붙어서, isolated 응답이 "원인 불명"처럼
        # 보였다(실제로는 외부 capture/legacy라 원래 연결이 없는 정상
        # 케이스일 수 있음 — unknown과 구분해 보여준다).
        anchor_reasons = anchor.get("evidence_reasons")
        lines.append(
            f"앵커 자신의 evidence_reasons={anchor_reasons}" if anchor_reasons
            else "앵커 자신의 evidence_reasons=unknown (아직 이 라운드 코드로 재백필되지 않음)"
        )
    if data.get("found") is False:
        lines.append("(그래프에 해당 노드 없음)")
    if data.get("excluded_hub_components"):
        lines.append(f"제외된 범용 컴포넌트: {', '.join(data['excluded_hub_components'])}")

    def _render_route(route: list) -> str:
        # G08(2차 리뷰) — "마지막 relation 하나"가 아니라 anchor부터 이
        # 노드까지의 전체 경로를 보여준다. PARENT_OF는 계층 의미가 있어 ↑/↓,
        # 나머지(REFERENCES/HAS_COMPONENT 등)는 방향만 있는 →/←로 표시해
        # "down=자식"이라는 계층 어휘를 다른 관계 타입에 잘못 끌어오지 않는다.
        parts = []
        for step in route or []:
            relation = step.get("relation")
            is_out = step.get("direction") == "out"
            marker = ("↓" if is_out else "↑") if relation == "PARENT_OF" else ("→" if is_out else "←")
            tag = step.get("tag")
            # R06(2026-10-13 2차 핸드오프) §1 — API에는 tag(EXTRACTED/INFERRED)
            # 가 있지만 MCP formatter가 그동안 관계명/방향만 찍어 사용자가
            # "명시 추출"과 "본문 추론"을 구분할 수 없었다.
            tag_suffix = f"[{tag}]" if tag else ""
            parts.append(f"{marker}{relation}{tag_suffix}")
            via = step.get("via")
            if via:
                name = via.get("id") or via.get("canonical_name") or via.get("bucket_name") or via.get("external_id") or "?"
                parts.append(str(name))
        return " ".join(parts)

    def _render_category(label: str, items: list, offset: int, total_key: str, trunc_key: str,
                          offset_param: str, line_fn) -> None:
        # 표시 줄 수 == 페이지 크기여야 한다 — 과거엔 페이지(최대 50)를 20줄만
        # 찍고 "offset+len(items)"를 다음 offset으로 안내해 21~50번째 항목이
        # 영원히 안 보이는 구멍이 있었다(2차 리뷰에서 지적). 전부 찍는다.
        total = data.get(total_key, len(items))
        truncated = data.get(trunc_key, False)
        shown_from = offset + 1 if items else offset
        shown_to = offset + len(items)
        lines.append(f"\n{label} ({total}건 중 {shown_from}-{shown_to}건 표시):")
        for item in items:
            lines.append(line_fn(item))
        if truncated:
            next_offset = offset + len(items)
            lines.append(f"  … 다음 페이지: {offset_param}={next_offset}로 재호출")

    def _render_document_line(d: dict) -> str:
        # G08(2차 리뷰) — copies/archived/원문 접근 경로를 한 줄에 더한다.
        access = d.get("access") or {}
        mcp_args = access.get("mcp_args") or {}
        tags = []
        copies = d.get("copies") or []
        if copies:
            tags.append(f"copies={len(copies)}")
        if d.get("archived"):
            tags.append("archived")
        if d.get("pg_missing"):
            # R04(2026-10-13 2차 핸드오프) §4 — PG에서 못 찾은 문서(삭제됨 등)를
            # 조용히 "활성 상태 정상 문서"로 보이게 하지 않는다.
            tags.append("pg_missing")
        if access.get("body_kind") == "snapshot":
            tags.append("snapshot")
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        access_str = f" 원문: kb_get_document(path='{mcp_args['path']}')" if mcp_args.get("path") else ""
        return (
            f"  - [{d.get('hops')}hop {_render_route(d.get('route'))}] {d.get('title')}{tag_str} "
            f"({d.get('source_type')}, evidence_grade={d.get('evidence_grade')}) id={d.get('id')}{access_str}"
        )

    docs = data.get("documents") or []
    _render_category(
        "연관 문서", docs, documents_offset, "documents_total", "documents_truncated",
        "documents_offset", _render_document_line,
    )

    comps = data.get("components") or []
    _render_category(
        "연관 컴포넌트", comps, components_offset, "components_total", "components_truncated",
        "components_offset",
        lambda c: f"  - [{c.get('hops')}hop {_render_route(c.get('route'))}] {c.get('canonical_name')}",
    )

    def _render_failure_bucket_line(b: dict) -> str:
        # R06(2026-10-13 2차 핸드오프) §7 — evidence_ref/본문 URL의
        # resolved/external_artifact/legacy_missing/target_not_ingested/
        # unresolved_reference 사유를 MCP에서도 확인할 수 있게 한다.
        reasons = b.get("evidence_reasons")
        reasons_str = f" evidence_reasons={reasons}" if reasons else ""
        return (
            f"  - [{b.get('hops')}hop {_render_route(b.get('route'))}] "
            f"{b.get('bucket_name')} id={b.get('id')}{reasons_str}"
        )

    fbs = data.get("failure_buckets") or []
    _render_category(
        "연관 과거 장애", fbs, failure_buckets_offset, "failure_buckets_total", "failure_buckets_truncated",
        "failure_buckets_offset", _render_failure_bucket_line,
    )

    return "\n".join(lines)


@mcp.tool()
async def kb_upload_document(
    filename: str,
    content: str,
    source_type: str = "incident_reports",
) -> str:
    """[쓰기 도구] 문서 1건을 업로드해 즉시 ingest 큐에 넣는다 (기존 POST /api/upload 래핑).
    주 용도: MY-OS SWIM 증분 수집 파이프라인이 신규/변경 장애보고서 소수 건을 즉시 반영.

    filename: 확장자 포함 파일명 (예: 26090761356.md)
    content: 파일 전체 텍스트(마크다운 원문)
    source_type: 기본 incident_reports — 다른 source_type도 넘길 수 있지만
                 이번 도구의 주 용도는 SWIM 장애보고서 증분 반영이다.

    ingest는 백그라운드로 처리된다 — 이 도구는 큐 등록까지만 확인한다. 적재 완료
    확인은 몇 초 뒤 kb_ticket(external_id=..., source_type="incident_reports")로
    별도 조회할 것.
    """
    if not filename.strip() or not content.strip():
        return "오류: filename/content가 비어 있습니다."
    try:
        async with _client(timeout=60.0) as client:
            files = {"file": (filename, content.encode("utf-8"), "text/markdown")}
            data_form = {"source_type": source_type}
            resp = await client.post("/api/upload", files=files, data=data_form)
            resp.raise_for_status()
            body = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return (
        f"업로드 큐 등록됨: job_id={body.get('job_id')} "
        f"filename={body.get('filename')} status={body.get('status')}"
    )


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
  kb_recent_changes(relative=|date_from=|date_to=, change_type=modified|added|all,
                    space_key=, source_types=, title_contains=)
                  Confluence(map/docs/tech_repo) 최근 수정·신규 문서 목록
                  예: relative="최근 7일", space_key="DevOps001", change_type="all"

[집계 · 분석]
  kb_analytics(group_by=, relative=, entity=, component=)
                  group_by=year|month|component|issue_type|status|assignee|total
  kb_entity_share(entity=, relative=)
  kb_title_tokens(component=)

[유사 장애 · 체크리스트 · 용량]
  kb_similar_incident(symptom=, product=, environment=)
  kb_list_checkitems(q=, area=) / kb_get_checkitem(code=)
  kb_capacity_estimate(period_days=, basis=)

[실패 버킷 · 다중 플러그인 진단 지식(network/cluster/windows 등)]
  kb_register_failure_bucket(bucket_name=, symptom=, discriminating_signals=, root_cause=, recommended_action=, fb_domain=, evidence_ref=, counter_signals=, protocol=, environment=, source_plugin=)
  kb_refine_failure_bucket(bucket_id=, add_signal=, add_counter_signal=, environment=, confirm=)
  kb_match_failure_bucket(observed_signals=, symptom=, fb_domain=, protocol=, environment=)
  kb_list_failure_buckets(fb_domain=, protocol=, environment=, min_confidence=)
  kb_get_failure_bucket(bucket_id=)
                  environment: csp|msp|onprem|hybrid — fb_domain/protocol과 직교하는 별도 축.
                  캡처/로그로 실제 확인됐을 때만 채운다(근거 없는 값 금지). 미지정 시 기존
                  동작 그대로(등록은 environment=null, 매칭/목록은 필터 없음, refine은 기존
                  값 유지). match/list에서 다른 값으로 태깅된 버킷은 후보에서 제외되고,
                  environment가 비어있는(미확인) 버킷은 계속 후보에 남는다.

[지식그래프 — 연관 탐색]
  kb_graph_explore(anchor_type=, anchor_value=, exclude_page_ids=, exclude_subtree_ids=,
                    exclude_source_types=, documents_offset=, components_offset=,
                    failure_buckets_offset=, include_archived=)
                  anchor_type: failure_bucket|document|component|symptom_text
                  장애 분석 시 연관 컴포넌트/문서/과거 장애를 2hop까지 탐색.
                  kb_match_failure_bucket/kb_similar_incident로 1차 후보를 찾은
                  다음 단계로 쓸 것 — 검색 대체재 아님. 하루 1회 배치 갱신(as_of 확인,
                  응답의 freshness 한 줄로 "마지막 완료된 전체 배치가 언제·어떤
                  상태였는지"와 "지금 새 배치가 진행 중인지"도 같이 보여줌).
                  카테고리당 최대 50건, 그 이상이면 응답에 "N건 중 X-Y건 표시"와
                  다음 페이지 안내가 나온다 — *_offset으로 재호출해 끝까지 볼 것.
                  맨 첫 줄 status(found/not_found/no_match/isolated)로 성공 여부를
                  판단할 것 — 문장으로 추측하지 말 것. 각 결과 줄의 [Nhop 경로]는
                  anchor부터 그 노드까지 전체 경로다(마지막 관계만이 아님) —
                  PARENT_OF는 ↑(anchor가 자식 쪽)/↓(anchor가 부모 쪽), 나머지
                  관계는 →/←로 방향 표시. document 앵커는 같은 페이지의 여러
                  수집본(confluence_map/tech_repo/confluence_docs) 중 어느 쪽으로
                  걸어도 같은 계층을 찾는다 — 대표 사본이 exclude_source_types로
                  제외되면 anchor.resolved_id는 그대로 대표를 가리키되
                  representative_excluded=True와 traversal_start_ids(실제 탐색
                  시작 사본)로 그 사실이 드러난다. 결과의 같은 페이지 수집본도
                  하나로 묶여 copies=N으로 표시(_EN 체크항목 등은 안 묶임).
                  archived는 비활성 원문 — 결론에 그대로 쓰지 말 것. 기본은
                  archived 문서를 결과에서 뺀다, include_archived=true로 과거
                  이력까지 볼 수 있음(단 archived 문서가 다른 두 문서 사이의
                  중간 경로로 쓰이는 것 자체는 막지 않음 — 알려진 한계).
                  failure_bucket 앵커는 앵커 자신의 evidence_reasons(resolved/
                  external_artifact/legacy_missing/target_not_ingested/
                  unresolved_reference 건수)도 같이 나옴 — 전부 0이고 isolated면
                  "원인 불명"이 아니라 이 사유 분해를 먼저 볼 것. "원문:
                  kb_get_document(path=...)"가 실제 호출 경로, confluence_map은
                  발췌(snapshot)뿐.
  kb_graph_status()
                  지식그래프 동기화 run/generation/freshness 상세(GET
                  /v1/graph/status와 동일 근거). kb_graph_explore의 freshness
                  한 줄보다 자세한 정보가 필요할 때 — 지금 진행 중인 배치가
                  정말 살아있는지(liveness, pg_locks로 실제 확인), Neo4j
                  store identity가 맞는지, read_state가 consistent/mixed/
                  unknown 중 뭔지 먼저 볼 것.

[문서 업로드 — 쓰기]
  kb_upload_document(filename=, content=, source_type=)   문서 1건 즉시 ingest 큐 등록
                  (kb_register_failure_bucket과 같은 급의 쓰기 도구 — 결과가 코퍼스에 반영됨)

[Confluence draw.io 다이어그램]
  kb_confluence_find_pages(space_key=, title=)    공간명으로 페이지 검색 (page_id 모를 때 먼저 사용)
  kb_confluence_list_diagrams(page_id=)          페이지의 다이어그램 목록
  kb_confluence_get_diagram(page_id=, diagram_name=)   원본 XML 조회
  kb_confluence_put_diagram(page_id=, diagram_name=, xml_content=)   업로드/갱신

[티켓 · Insight · 상태]
  kb_ticket(external_id=)
  kb_list_insights / kb_get_insight
  kb_ask / wiki_ask   RAG 답변
  kb_health / kb_stats

[CI-TEC 11개 도메인 · 반복장애 · failure_bucket 정리 갭]
  kb_citec_domain_catalog()   도메인/심각도 티어 정확한 값 확인 (다른 둘 쓰기 전 먼저)
  kb_citec_recurring_patterns(group_by=, domains=, severity_tiers=, since_days=, min_count=)
  kb_citec_failure_bucket_coverage(since_days=, min_count=)   반복 확인됐는데 failure_bucket 미등록인 도메인

[신뢰도 — 답하기 전에 반드시 확인]
  evidence_grade: A/A- 본문 그대로 인용 가능 · B 아직 미종결(열린 티켓 등, 결론 바뀔 수 있음)
  · C(=confluence_map 전용) — 2026-10 이후 크롤/백필된 공간은 본문도 들어있지만
  (전엔 제목/경로만) evidence_grade="C"는 정책상 그대로 유지: 자동 크롤이라
  tech_repo/confluence_docs만큼의 검증·최신성 보장이 없다는 뜻. 같은 page_id가
  tech_repo/confluence_docs에도 있으면 항상 그걸 우선 쓰고(confluence_map은
  후순위), 본문이 짧고 경로만 있는 모양이면 2026-10-01 이전 데이터라 아직 전체본문
  백필 전이니 실제 페이지를 열어 확인할 것. 자세한 소스타입별 등급표/근거 모델은
  docs/AI_AGENT_GUIDE.md §6 참고.
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
async def kb_confluence_find_pages(space_key: str, title: str = "", limit: int = 25) -> str:
    """Confluence 공간(space) 안에서 페이지를 검색해 page_id를 찾는다 (CQL space 검색).
    space_key는 바뀔 수 있으므로 매번 인자로 받는다. title은 부분 일치(선택)."""
    try:
        async with _client() as client:
            resp = await client.get(
                f"/v1/confluence/spaces/{space_key}/pages",
                params={"title": title, "limit": limit},
            )
            if resp.status_code == 503:
                return "오류: Confluence 연동이 설정되지 않았습니다 (CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD)."
            if resp.status_code >= 400:
                return f"오류: 페이지 검색 실패 (HTTP {resp.status_code}): {_api_error_detail(resp)}"
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    items = data.get("items") or []
    if not items:
        return f"공간 {space_key}에서 조건에 맞는 페이지를 찾을 수 없습니다."
    lines = [f"페이지 {len(items)}건:"]
    for it in items:
        lines.append(
            f"- {it.get('title')} (page_id={it.get('page_id')}, web_url={it.get('web_url')})"
        )
    return "\n".join(lines)


@mcp.tool()
async def kb_confluence_list_diagrams(page_id: str) -> str:
    """Confluence 페이지의 draw.io 다이어그램 목록을 조회한다 (본문 매크로 + 첨부파일 기준).
    page_id는 Confluence 페이지 ID(숫자 문자열)."""
    try:
        async with _client() as client:
            resp = await client.get(f"/v1/confluence/pages/{page_id}/diagrams")
            if resp.status_code == 503:
                return "오류: Confluence 연동이 설정되지 않았습니다 (CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD)."
            if resp.status_code >= 400:
                return f"오류: 다이어그램 목록 조회 실패 (HTTP {resp.status_code}): {_api_error_detail(resp)}"
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    items = data.get("items") or []
    if not items:
        return f"페이지 {page_id}에서 draw.io 다이어그램을 찾을 수 없습니다."
    lines = [f"다이어그램 {len(items)}건:"]
    for it in items:
        lines.append(
            f"- {it.get('diagram_name')} (attachment_id={it.get('attachment_id')}, "
            f"version={it.get('version')}, media_type={it.get('media_type')}, "
            f"inline={it.get('inline')})"
        )
        if it.get("attachment_id") is None:
            candidates = it.get("candidate_attachment_titles")
            if candidates:
                lines.append(f"  ⚠ 매칭 실패 — 페이지의 실제 첨부파일 목록: {candidates}")
            else:
                lines.append("  ⚠ 매칭 실패 — 페이지에 첨부파일이 전혀 없음")
    return "\n".join(lines)


@mcp.tool()
async def kb_confluence_get_diagram(page_id: str, diagram_name: str) -> str:
    """Confluence 페이지의 draw.io 다이어그램 원본 XML을 조회한다.
    diagram_name은 kb_confluence_list_diagrams 결과의 diagram_name 값."""
    try:
        async with _client() as client:
            resp = await client.get(f"/v1/confluence/pages/{page_id}/diagrams/{diagram_name}")
            if resp.status_code == 503:
                return "오류: Confluence 연동이 설정되지 않았습니다 (CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD)."
            if resp.status_code == 404:
                return f"오류: 다이어그램을 찾을 수 없습니다: {diagram_name}"
            if resp.status_code >= 400:
                return f"오류: 다이어그램 조회 실패 (HTTP {resp.status_code}): {_api_error_detail(resp)}"
            return resp.text
    except httpx.HTTPError as e:
        return _err(e)


@mcp.tool()
async def kb_confluence_put_diagram(page_id: str, diagram_name: str, xml_content: str) -> str:
    """Claude가 생성/수정한 draw.io XML을 Confluence 페이지에 첨부파일로 업로드(갱신 또는 신규 생성)한다.
    기존 매크로가 이 diagram_name을 참조 중이면 페이지에 바로 반영된다."""
    try:
        async with _client() as client:
            resp = await client.put(
                f"/v1/confluence/pages/{page_id}/diagrams/{diagram_name}",
                content=xml_content.encode("utf-8"),
                headers={"Content-Type": "application/xml"},
            )
            if resp.status_code == 503:
                return "오류: Confluence 연동이 설정되지 않았습니다 (CONFLUENCE_BASE_URL/CONFLUENCE_USERNAME/CONFLUENCE_PASSWORD)."
            if resp.status_code >= 400:
                return f"오류: 다이어그램 업로드 실패 (HTTP {resp.status_code}): {_api_error_detail(resp)}"
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return f"업로드됨: {diagram_name} (attachment_id={data.get('attachment_id')}, version={data.get('version')})"


@mcp.tool()
async def kb_register_failure_bucket(
    bucket_name: str,
    symptom: str,
    discriminating_signals: list[str],
    root_cause: str,
    recommended_action: str,
    fb_domain: str,
    evidence_ref: str,
    counter_signals: list[str] | None = None,
    protocol: str = "",
    environment: str = "",
    source_plugin: str = "",
) -> str:
    """새로운 실패 버킷(장애 패턴)을 등록한다. 즉시 검색에 노출된다.
    예: bucket_name='LB idle-timeout RST', discriminating_signals=['RST 직전 idle 60초 이상'],
    fb_domain='network', evidence_ref='capture:2026-08-06-upload01.pcapng#frame=4821'
    environment: csp|msp|onprem|hybrid — 이 패턴이 특정 환경에서만 성립하면 채운다(예: 'hybrid'는
    서비스 메시/쿠버네티스처럼 온프레미스·클라우드 어디서나 나타나는 복잡 토폴로지). 확신이 없으면
    비워둔다 — 비워두면 환경 무관 패턴으로 취급되어 모든 environment 필터에 계속 후보로 노출된다."""
    if not bucket_name.strip():
        return "오류: bucket_name 이 비어 있습니다."
    if not discriminating_signals:
        return "오류: discriminating_signals 가 비어 있습니다."
    if not symptom.strip():
        return "오류: symptom 이 비어 있습니다."
    if not root_cause.strip():
        return "오류: root_cause 가 비어 있습니다."
    if not recommended_action.strip():
        return "오류: recommended_action 이 비어 있습니다."
    if not fb_domain.strip():
        return "오류: fb_domain 이 비어 있습니다."
    if not evidence_ref.strip():
        return "오류: evidence_ref 가 비어 있습니다."
    body: dict[str, Any] = {
        "bucket_name": bucket_name.strip(),
        "fb_domain": fb_domain.strip(),
        "symptom": symptom or "",
        "discriminating_signals": discriminating_signals,
        "counter_signals": counter_signals or [],
        "root_cause": root_cause or "",
        "recommended_action": recommended_action or "",
        "evidence_ref": evidence_ref.strip(),
        "protocol": protocol.strip() or None,
        "environment": environment.strip() or None,
        "created_by": source_plugin.strip() or None,
    }
    try:
        async with _client() as client:
            resp = await client.post("/v1/failure-buckets", json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    lines = [
        f"등록됨: {data.get('bucket_name')} (id={data.get('id')}, "
        f"confidence={data.get('confidence')})"
    ]
    dup = data.get("possible_duplicate_of")
    if dup:
        lines.append(
            f"주의: 기존 버킷과 유사할 수 있습니다 — {dup.get('bucket_name')} "
            f"(id={dup.get('bucket_id')}, confidence={dup.get('confidence')})"
        )
    warning = data.get("fb_domain_warning")
    if warning:
        lines.append(f"경고: {warning}")
    ev_warning = data.get("evidence_ref_warning")
    if ev_warning:
        lines.append(f"경고: {ev_warning}")
    return "\n".join(lines)


@mcp.tool()
async def kb_refine_failure_bucket(
    bucket_id: str,
    add_signal: str = "",
    add_counter_signal: str = "",
    environment: str = "",
    confirm: bool = True,
) -> str:
    """실패 버킷을 정제한다 — 신호 추가 및 확인(confirm=True)/반박(confirm=False) 기록.
    신뢰도(confidence)가 자동 재계산된다.
    environment: csp|msp|onprem|hybrid — 기존 버킷에 environment가 비어있었거나 잘못 태깅됐음을
    이번에 확인했을 때 채운다(덮어씀). 비워두면(기본값) 기존 environment는 그대로 유지된다 — 이
    호출과 무관한 값을 실수로 지우지 않는다."""
    bucket_id = (bucket_id or "").strip()
    if not bucket_id:
        return "오류: bucket_id 가 비어 있습니다."
    body: dict[str, Any] = {"confirm": confirm}
    if add_signal.strip():
        body["add_signal"] = add_signal.strip()
    if add_counter_signal.strip():
        body["add_counter_signal"] = add_counter_signal.strip()
    if environment.strip():
        body["environment"] = environment.strip()
    try:
        async with _client() as client:
            resp = await client.post(f"/v1/failure-buckets/{bucket_id}/refine", json=body)
            if resp.status_code == 404:
                return f"오류: 실패 버킷을 찾을 수 없습니다: {bucket_id}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return (
        f"정제됨: {data.get('bucket_name')} environment={data.get('environment')} "
        f"confidence={data.get('confidence')} "
        f"support={data.get('support_count')} counter={data.get('counter_count')}"
    )


@mcp.tool()
async def kb_match_failure_bucket(
    observed_signals: list[str],
    symptom: str = "",
    fb_domain: str = "",
    protocol: str = "",
    environment: str = "",
    top_k: int = 5,
) -> str:
    """관찰된 신호로 후보 실패 버킷을 순위화한다.
    예: observed_signals=['RST 직전 idle 62초'], symptom='다운로드 중 연결 끓김', fb_domain='network'
    environment(csp|msp|onprem|hybrid)를 채우면 다른 환경 전용으로 태깅된 버킷은 후보에서 제외된다
    (environment 미태깅 버킷은 계속 후보에 남는다)."""
    if not observed_signals and not symptom.strip():
        return "오류: observed_signals 또는 symptom 중 하나는 필요합니다."
    body: dict[str, Any] = {
        "observed_signals": observed_signals,
        "symptom": symptom or "",
        "top_k": min(max(top_k, 1), 20),
    }
    if fb_domain.strip():
        body["fb_domain"] = fb_domain.strip()
    if protocol.strip():
        body["protocol"] = protocol.strip()
    if environment.strip():
        body["environment"] = environment.strip()
    try:
        async with _client() as client:
            resp = await client.post("/v1/failure-buckets/match", json=body)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    results = data.get("results") or []
    if not results:
        return "(일치하는 실패 버킷 없음)"
    lines = [f"실패 버킷 후보 {len(results)}건:"]
    for r in results:
        if not isinstance(r, dict):
            continue
        lines.append(
            f"- {r.get('bucket_name')} (id={r.get('bucket_id')}) "
            f"confidence={r.get('confidence')} label={r.get('label')}\n"
            f"  matched: {r.get('matched_signals')}\n"
            f"  contradicted: {r.get('contradicted')}"
        )
    return "\n".join(lines)


@mcp.tool()
async def kb_list_failure_buckets(
    fb_domain: str = "",
    protocol: str = "",
    environment: str = "",
    min_confidence: float = 0.0,
    limit: int = 20,
) -> str:
    """등록된 실패 버킷 목록. fb_domain 예: network|cluster|windows, protocol 예: TCP|TLS|HTTP,
    environment 예: csp|msp|onprem|hybrid"""
    params: dict[str, Any] = {"limit": min(max(limit, 1), 200), "min_confidence": min_confidence}
    if fb_domain.strip():
        params["fb_domain"] = fb_domain.strip()
    if protocol.strip():
        params["protocol"] = protocol.strip()
    if environment.strip():
        params["environment"] = environment.strip()
    try:
        async with _client() as client:
            resp = await client.get("/v1/failure-buckets", params=params)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    items = data.get("items") or []
    lines = [f"failure_buckets total={data.get('total', len(items))}"]
    for it in items:
        if not isinstance(it, dict):
            continue
        lines.append(
            f"- {it.get('bucket_name')} (id={it.get('id')}) fb_domain={it.get('fb_domain')} "
            f"protocol={it.get('protocol')} environment={it.get('environment')} "
            f"confidence={it.get('confidence')}"
        )
    if not items:
        lines.append("(결과 없음)")
    return "\n".join(lines)


@mcp.tool()
async def kb_get_failure_bucket(bucket_id: str) -> str:
    """실패 버킷 상세(판별 신호/반증 신호/근본원인/조치 포함)를 조회한다."""
    bucket_id = (bucket_id or "").strip()
    if not bucket_id:
        return "오류: bucket_id 가 비어 있습니다."
    try:
        async with _client() as client:
            resp = await client.get(f"/v1/failure-buckets/{bucket_id}")
            if resp.status_code == 404:
                return f"오류: 실패 버킷을 찾을 수 없습니다: {bucket_id}"
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)
    return (
        f"# {data.get('bucket_name')} (id={data.get('id')})\n"
        f"fb_domain={data.get('fb_domain')} protocol={data.get('protocol')} "
        f"environment={data.get('environment')} "
        f"confidence={data.get('confidence')} "
        f"support={data.get('support_count')} counter={data.get('counter_count')}\n"
        f"evidence_ref={data.get('evidence_ref')} created_by={data.get('created_by')}\n\n"
        f"증상: {data.get('symptom')}\n"
        f"판별 신호: {data.get('discriminating_signals')}\n"
        f"반증 신호: {data.get('counter_signals')}\n"
        f"근본원인: {data.get('root_cause')}\n"
        f"조치: {data.get('recommended_action')}"
    )


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
            llm = await client.get("/v1/health/llm")
            llm_data: dict[str, Any] = llm.json() if llm.status_code == 200 else {}
            light_data = light.json()
    except httpx.HTTPError as e:
        return _err(e)

    return (
        f"ok={light_data.get('ok')} version={light_data.get('version')} "
        f"service={light_data.get('service')}\n"
        f"full_status={full_data.get('status')} env={full_data.get('env')}\n"
        f"llm_backend={llm_data.get('backend')} chat_supported={llm_data.get('chat_supported', True)} "
        f"chat_stream_supported={llm_data.get('chat_stream_supported', True)}\n"
        f"checks={json.dumps(full_data.get('checks') or {}, ensure_ascii=False)[:800]}"
    )


@mcp.tool()
async def kb_graph_status() -> str:
    """F04(2026-10-13 최종 핸드오프) §8 — 그래프 동기화 run/revision/
    freshness 상세를 MCP에서도 조회한다(REST GET /v1/graph/status와 같은
    근거). kb_graph_explore의 freshness 한 줄 요약보다 자세한 정보가
    필요할 때(예: 지금 진행 중인 배치가 정말 살아있는지, Neo4j store
    identity가 맞는지) 이 도구를 먼저 부른다.

    읽는 법: latest_run이 전체(full) 배치이고 status=running인데
    liveness.state가 likely_dead/stalled면, 그 run은 실제로는 끝났거나
    멈춘 것이다(advisory lock을 pg_locks로 직접 확인한 결과) — "진행 중"
    문구만 보고 기다리지 말 것. read_state=mixed면 지금 보이는 그래프
    데이터가 완료된 단일 스냅샷이 아닐 수 있다는 뜻이다.
    """
    try:
        async with _client(timeout=15.0) as client:
            resp = await client.get("/v1/graph/status")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    def _run_line(label: str, run: dict[str, Any] | None) -> str:
        if run is None:
            return f"{label}: 없음"
        liveness = run.get("liveness")
        liveness_str = f" liveness={liveness['state']}" if liveness else ""
        return (
            f"{label}: run_id={run['run_id']} scope={run['scope']} status={run['status']}"
            f"{liveness_str} started_at={run['started_at']} completed_at={run['completed_at']} "
            f"generation={run['generation']} documents_synced={run['documents_synced']} "
            f"documents_failed={run['documents_failed']} "
            f"documents_state_write_failed={run['documents_state_write_failed']}"
        )

    store = data.get("neo4j_store_identity") or {}
    lines = [
        f"read_state: {data.get('read_state')}",
        f"as_of: {data.get('as_of')}",
        f"generation_matches: {data.get('generation_matches')}",
        f"neo4j_generation(write marker): {data.get('neo4j_generation')}",
        f"neo4j_store_identity(store id): {store.get('id')} created={store.get('creation_date')}",
        f"contract_version: {data.get('contract_version')}  api_build_sha: {data.get('api_build_sha')}",
        _run_line("latest_run", data.get("latest_run")),
        _run_line("latest_full_completed_run", data.get("latest_full_completed_run")),
    ]
    return "\n".join(lines)


@mcp.tool()
async def kb_stats() -> str:
    """코퍼스 통계 (소스 타입별 문서 수). as_of 는 조회 시각(UTC)이다."""
    try:
        async with _client() as client:
            resp = await client.get("/api/wiki-stats")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    lines = [
        f"as_of={data.get('as_of')} total_documents={data.get('total')} insights={data.get('insights')}",
        "by_source_type:",
    ]
    for k, v in sorted((data.get("by_source_type") or {}).items(), key=lambda x: -int(x[1] or 0)):
        lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


@mcp.tool()
async def kb_citec_domain_catalog() -> str:
    """CI-TEC 반복장애 대시보드가 쓰는 11개 도메인(Linux/Windows/VMware/
    OpenStack/Kubernetes/Middleware/Network/Storage/Ceph/Database/성능)과
    SWIM 심각도 티어(major/minor/failover_no_impact/customer_fault/
    vendor_fault/unknown) 정의, group_by에 쓸 수 있는 차원 목록을 반환한다.
    다른 kb_citec_* 도구를 쓰기 전에 한 번 조회해서 값을 확인하는 걸 권장."""
    try:
        async with _client() as client:
            resp = await client.get("/v1/citec-dashboard/domains")
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    lines = ["CI-TEC 도메인:"]
    lines.append(", ".join(data.get("domains") or []))
    lines.append("")
    lines.append("심각도 티어:")
    for tier, desc in (data.get("severity_tiers") or {}).items():
        lines.append(f"  - {tier}: {desc}")
    lines.append("")
    lines.append(
        f"group_by 차원(최대 {data.get('max_group_by_dimensions')}개 조합): "
        + ", ".join(data.get("group_by_dimensions") or [])
    )
    return "\n".join(lines)


@mcp.tool()
async def kb_citec_recurring_patterns(
    group_by: str,
    domains: str = "",
    severity_tiers: str = "",
    dept_contains: str = "",
    customer_contains: str = "",
    since_days: int = 0,
    min_count: int = 3,
    limit: int = 30,
) -> str:
    """CI-TEC SWIM(incident_reports) 반복 장애 패턴 조회 — 태깅된 11개 도메인
    (Linux/Windows/VMware/OpenStack/Kubernetes/Middleware/Network/Storage/
    Ceph/Database/성능) 기준으로 집계한다. 값 확인이 필요하면 먼저
    kb_citec_domain_catalog()를 호출할 것.

    group_by: 쉼표로 1~3개, domain/severity_tier/dept/customer/year 중에서
      (예: "domain" 또는 "domain,severity_tier" 또는 "domain,dept").
    domains: 필터할 도메인 쉼표 목록(예: "Network,Database"), 비우면 전체.
    severity_tiers: 필터할 티어 쉼표 목록(예: "major,minor"), 비우면 전체.
    dept_contains / customer_contains: 운영부서/고객사 부분일치 필터.
    since_days: N일 이내 발생 건만(예: 730=최근 2년), 0이면 전체 기간.
    min_count: 이 값 미만인 그룹은 결과에서 제외(기본 3 — "반복"의 최소
      기준, citec-kb 대화에서 박재화가 확정한 값).
    limit: 반환할 그룹 수 상한(건수 내림차순 정렬).

    한 장애가 여러 도메인 태그를 가질 수 있어(예: "DB Hang"은 Database와
    성능 둘 다), group_by에 domain이 들어가면 그 장애는 해당하는 각 도메인
    그룹에 한 번씩 잡힌다 — count는 "그 도메인이 걸린 건수"이지 "그 도메인
    단독인 건수"가 아니다."""
    params: dict[str, Any] = {"group_by": group_by, "min_count": min_count, "limit": limit}
    if domains.strip():
        params["domains"] = domains.strip()
    if severity_tiers.strip():
        params["severity_tiers"] = severity_tiers.strip()
    if dept_contains.strip():
        params["dept_contains"] = dept_contains.strip()
    if customer_contains.strip():
        params["customer_contains"] = customer_contains.strip()
    if since_days > 0:
        params["since_days"] = since_days

    try:
        async with _client() as client:
            resp = await client.get("/v1/citec-dashboard/recurring-patterns", params=params)
            if resp.status_code >= 400:
                return f"오류: 반복 패턴 조회 실패 (HTTP {resp.status_code}): {_api_error_detail(resp)}"
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    groups = data.get("groups") or []
    if not groups:
        return f"조건에 맞는 반복 패턴이 없습니다 (candidate_count={data.get('candidate_count')}, min_count={min_count})."

    lines = [
        f"반복 패턴 {len(groups)}건 (전체 후보 {data.get('candidate_count')}건 중"
        + (", 결과 더 있음(limit 도달)" if data.get("truncated") else "")
        + "):"
    ]
    for g in groups:
        group_desc = ", ".join(f"{k}={v}" for k, v in g["group"].items())
        lines.append(f"- [{group_desc}] {g['count']}건, 고객사 {g['customer_count']}곳")
        for s in g.get("samples") or []:
            lines.append(f"    · {s.get('title')} ({s.get('occurred_at')}) {s.get('url') or ''}")
    return "\n".join(lines)


@mcp.tool()
async def kb_citec_failure_bucket_coverage(since_days: int = 730, min_count: int = 3) -> str:
    """CI-TEC 11개 도메인별로, 반복 장애가 확인됐는데 아직 failure_bucket
    (실패 패턴 라이브러리)에 등록 안 된 "정리 갭"을 보여준다.

    주의: failure_bucket의 fb_domain 어휘(현재 network/cluster/windows/
    dbms/linux/virtualization/middleware/storage 8개, references/
    failure-bucket-domains.md 참고)는 진단 플러그인이 소유하는 별도
    체계라 CI-TEC 11개 도메인과 완전히 겹치지 않는다. 이 도구가 실제
    gap 비교에 쓰는 CI-TEC→fb_domain 매핑은 9개 CI-TEC 도메인 → fb_domain 8개
    전부(identity 7개 + Linux에 합산되는 cluster):
    Network→network,
    Windows→windows, Linux→linux(+cluster 합산), VMware/OpenStack→virtualization(공유),
    Middleware→middleware, Storage/Ceph→storage(공유), Database→dbms.
    Linux는 pacemaker-tools가 등록한 cluster 버킷도 함께 센다 — Pacemaker/
    Corosync가 항상 Linux 호스트에서 도는 이 부서 환경에서는 그 버킷들도
    실질적으로 Linux 커버리지이기 때문이다(응답의 `fb_domains` 배열에
    ["linux", "cluster"]로 노출됨). VMware/OpenStack, Storage/Ceph처럼 같은
    fb_domain을 공유하는 도메인 쌍은 failure_bucket_count가 동일하게
    나온다 — 의도된 설계이며(둘 중 한쪽에만 해당하는 버킷도 다른 쪽
    커버리지로 잡힐 수 있음), 이중집계 버그가 아니다. Kubernetes/성능은
    아직 대응하는 fb_domain이 없어
    "no_fb_domain_defined"로 나온다 — 이건 "커버리지 0%"가 아니라 "아직
    이 도메인을 위한 fb_domain 자체가 없다"는 뜻이다."""
    try:
        async with _client() as client:
            resp = await client.get(
                "/v1/citec-dashboard/failure-bucket-coverage",
                params={"since_days": since_days, "min_count": min_count},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        return _err(e)

    status_label = {
        "gap": "⚠ 정리 필요 (반복 확인됨, failure_bucket 없음)",
        "covered": "✓ 등록됨",
        "no_recurring_pattern": "- 반복 패턴 없음",
        "no_fb_domain_defined": "(fb_domain 미정의)",
    }
    lines = [f"CI-TEC 도메인별 failure_bucket 커버리지 (최근 {since_days}일, min_count={min_count}):"]
    for row in data.get("domains") or []:
        fb_count = row.get("failure_bucket_count")
        fb_count_str = "" if fb_count is None else f", 등록된 failure_bucket {fb_count}건"
        lines.append(
            f"- {row['domain']}: 반복 장애 {row['recurring_incident_count']}건{fb_count_str} "
            f"— {status_label.get(row['status'], row['status'])}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    # streamable-http: Claude Desktop remote / Docker (default)
    # stdio: local Claude Desktop / Claude Code subprocess
    if MCP_TRANSPORT in {"stdio", "std"}:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")
