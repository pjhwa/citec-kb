"""Phase 3 query planner — intent detection + optional execution.

Priority:
  capacity → analytics → time_scoped_list → similar_incident → prevention
  → exhaustive → checklist → entity_aggregate → hybrid_search
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from app.analytics.aggregate import aggregate_tickets, entity_share
from app.analytics.title_tokens import title_token_stats
from app.capacity.calculator import estimate_capacity
from app.capacity.intent import detect_capacity_intent
from app.query.analytics_intent import detect_analytics_intent
from app.query.exhaustive import detect_exhaustive_intent, run_exhaustive
from app.query.prevention import detect_prevention_intent, run_prevention
from app.query.time_range import detect_time_scoped_list

# --- additional intent detectors ---

_CHECKLIST = re.compile(
    r"체크\s*리스트|체크리스트|"
    r"PISA\s*(항목|점검|진단\s*항목|Area)|점검\s*항목\s*(목록|개수)|"
    r"진단\s*체크|PISA\s*점검\s*항목",
    re.I,
)
_SI = re.compile(
    r"유사\s*장애|과거\s*유사|비슷한\s*(과거|건|장애|사례|증상)|유사\s*건|"
    r"이전\s*유사|이번에도\s*(유효|도움|맞는지)|현재\s*(장애|증상)|"
    r"가장\s*비슷한\s*과거|유사\s*사례|가장\s*유사|"
    r"과거\s*.{0,20}(사례|해결|유사)|"
    r"현재.{0,40}장애|"
    r"적용\s*가능한가|재발\s*여부",
    re.I,
)
_ENTITY_AGG = re.compile(
    r"카테고리화|분야별로\s*(분류|카테고리)|유형\s*이슈\s*목록|"
    r"이력을\s*분야별",
    re.I,
)
_ENTITY_NEEDLE = re.compile(
    r"모니모|monimo|\bSCP\b|Redis|레디스|Oracle|오라클",
    re.I,
)


def detect_checklist_intent(text: str) -> Optional[dict[str, Any]]:
    t = (text or "").strip()
    if not t or not _CHECKLIST.search(t):
        return None
    area = None
    if re.search(r"Linux|리눅스", t, re.I):
        area = "Linux"
    elif re.search(r"Oracle|오라클", t, re.I):
        area = "Oracle"
    elif re.search(r"Windows|윈도우", t, re.I):
        area = "Windows"
    # residual free-text for checkitems q=
    q = t
    for noise in ("관련", "목록", "항목", "은?", "는?", "개수", "와", "과"):
        q = q.replace(noise, " ")
    # keep keywords
    terms = []
    for pat, term in [
        (r"파일\s*시스템|filesystem|fsck", "파일시스템"),
        (r"네트워크", "네트워크"),
        (r"Oracle|오라클", "Oracle"),
    ]:
        if re.search(pat, t, re.I):
            terms.append(term)
    return {
        "intent": "checklist",
        "area": area,
        "q": " ".join(terms) if terms else None,
        "endpoint": "GET /v1/checkitems",
    }


def detect_similar_incident_intent(text: str) -> Optional[dict[str, Any]]:
    t = (text or "").strip()
    if not t or not _SI.search(t):
        return None
    return {
        "intent": "similar_incident",
        "symptom": t,
        "endpoint": "POST /v1/similar-incident",
    }


def detect_entity_aggregate_intent(text: str) -> Optional[dict[str, Any]]:
    t = (text or "").strip()
    if not t or not _ENTITY_AGG.search(t):
        return None
    entity = None
    m = _ENTITY_NEEDLE.search(t)
    if m:
        raw = m.group(0)
        entity = {"monimo": "모니모", "모니모": "모니모"}.get(raw.lower(), raw)
    component = None
    if re.search(r"진단\s*컨설팅|진단컨설팅", t):
        component = "진단컨설팅"
    return {
        "intent": "entity_aggregate",
        "entity": entity,
        "component": component,
        "source_type": "support_history",
        "group_by": "component",
        "endpoint": "GET /v1/analytics/tickets",
    }


def plan_query(q: str) -> dict[str, Any]:
    """Return structured plan (intent + params). Does not execute handlers."""
    q = (q or "").strip()
    if not q:
        return {"intent": "error", "error": "q required"}

    capacity = detect_capacity_intent(q)
    if capacity:
        return capacity

    analytics = detect_analytics_intent(q)
    if analytics:
        return analytics

    listed = detect_time_scoped_list(q)
    if listed:
        return listed

    # SI before checklist — "유사장애 + 체크리스트" 질의는 SI 우선
    si = detect_similar_incident_intent(q)
    if si:
        return si

    prevention = detect_prevention_intent(q)
    if prevention:
        return prevention

    exhaustive = detect_exhaustive_intent(q)
    if exhaustive:
        return exhaustive

    checklist = detect_checklist_intent(q)
    if checklist:
        return checklist

    ent = detect_entity_aggregate_intent(q)
    if ent:
        return ent

    return {
        "intent": "hybrid_search",
        "params": {"q": q, "multi_query": True},
        "qtype_hint": "factoid_or_synthesize",
        "endpoint": "POST /v1/search (multi_query=true) | POST /v1/chat",
        "note": "일반 검색/합성 의도 — multi-query hybrid 확장 병합",
    }


def execute_plan(plan: dict[str, Any], *, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Execute a plan produced by plan_query. Returns route-style response."""
    body = body or {}
    intent = plan.get("intent")

    if intent == "capacity":
        result = estimate_capacity(
            period_days=int(plan.get("period_days") or 7),
            basis=str(plan.get("basis") or "1안"),
            fields=plan.get("fields"),
            include_pricing=bool(plan.get("include_pricing", True)),
        )
        return {
            "intent": "capacity",
            "params": plan,
            "result": result,
            "note": "공수·대수·단가 — capacity_rules 계산 (LLM 미사용).",
        }

    if intent == "analytics":
        df = date.fromisoformat(plan["date_from"]) if plan.get("date_from") else None
        dt = date.fromisoformat(plan["date_to"]) if plan.get("date_to") else None
        if plan.get("mode") == "title_tokens":
            result = title_token_stats(
                source_type=plan.get("source_type") or "support_history",
                component=plan.get("component"),
                top_k=int(body.get("top_k") or 20),
            )
        elif plan.get("mode") == "entity_share" and plan.get("entity"):
            result = entity_share(
                entity=str(plan["entity"]),
                source_type=plan.get("source_type") or "support_history",
                date_field=plan.get("date_field") or "Created",
                date_from=df,
                date_to=dt,
            )
        else:
            include_samples = bool(
                plan.get("include_samples")
                or body.get("include_samples")
                or (plan.get("group_by") == "component")
            )
            result = aggregate_tickets(
                source_type=plan.get("source_type") or "support_history",
                group_by=plan.get("group_by") or "total",
                date_field=plan.get("date_field") or "Created",
                date_from=df,
                date_to=dt,
                component=plan.get("component"),
                entity=plan.get("entity"),
                top_k=int(body.get("top_k") or 50),
                include_samples=include_samples,
                sample_limit=int(
                    body.get("sample_limit") or plan.get("sample_limit") or 8
                ),
            )
        if plan.get("range_label"):
            result["range_label"] = plan["range_label"]
        note = "집계·건수/제목토큰 — support_history metadata COUNT (LLM 미사용)."
        if result.get("group_by") == "component" and result.get("include_samples"):
            note = (
                "지원 유형(Component)별 건수 + 샘플 상세 — "
                "source_type=support_history only. 상세: GET /v1/tickets/{external_id}"
            )
        return {
            "intent": "analytics",
            "range_label": plan.get("range_label"),
            "params": plan,
            "result": result,
            "note": note,
        }

    if intent == "prevention":
        result = run_prevention(
            symptom=str(plan.get("symptom") or body.get("q") or ""),
            area=plan.get("area"),
            checkitem_q=plan.get("checkitem_q"),
            top_k=int(body.get("top_k") or 3),
        )
        return {
            "intent": "prevention",
            "params": plan,
            "result": result,
            "note": "예방 hop — 유사장애 + PISA checkitems (LLM 미사용).",
        }

    if intent == "exhaustive":
        result = run_exhaustive(
            q=str(plan.get("q") or body.get("q") or ""),
            top_k=int(plan.get("top_k") or body.get("top_k") or 20),
        )
        return {
            "intent": "exhaustive",
            "params": plan,
            "result": result,
            "note": "고재현 multi-query 목록 (전량 스캔 아님 · completeness 메타 포함).",
        }

    if intent == "time_scoped_list":
        df = date.fromisoformat(plan["date_from"])
        dt = date.fromisoformat(plan["date_to"])
        from app.tickets.query import list_tickets

        listed = list_tickets(
            source_type=plan.get("source_type") or "support_history",
            date_field=plan.get("date_field") or "Created",
            date_from=df,
            date_to=dt,
            limit=int(body.get("limit") or 50),
            offset=int(body.get("offset") or 0),
            order=str(body.get("order") or "desc"),
        )
        return {
            "intent": "time_scoped_list",
            "range_label": plan.get("range_label"),
            "params": plan,
            "result": listed,
            "note": "기간·목록 질의 — metadata 날짜 필터.",
        }

    if intent == "checklist":
        from app.routers.checkitems import list_checkitems

        result = list_checkitems(
            q=plan.get("q"),
            area=plan.get("area"),
            category_1=None,
            limit=int(body.get("limit") or 50),
            offset=0,
        )
        return {
            "intent": "checklist",
            "params": plan,
            "result": result,
            "note": "PISA checkitems 테이블 조회.",
        }

    if intent == "similar_incident":
        from app.si.retrieve import similar_incidents

        result = similar_incidents(
            symptom=str(plan.get("symptom") or body.get("q") or ""),
            top_k=int(body.get("top_k") or 3),
            product=body.get("product"),
            service=body.get("service"),
            environment=body.get("environment"),
        )
        return {
            "intent": "similar_incident",
            "params": plan,
            "result": result,
            "note": "유사장애 브리핑 — SI pipeline.",
        }

    if intent == "entity_aggregate":
        result = aggregate_tickets(
            source_type=plan.get("source_type") or "support_history",
            group_by=plan.get("group_by") or "component",
            entity=plan.get("entity"),
            component=plan.get("component"),
            top_k=int(body.get("top_k") or 50),
        )
        return {
            "intent": "entity_aggregate",
            "params": plan,
            "result": result,
            "note": "엔티티/유형 메타 집계 (LLM 미사용).",
        }

    if intent == "hybrid_search":
        # Unified ask UI: default run hybrid search when plan is executed.
        # Opt out with include_search=false / execute_search=false.
        q = str((plan.get("params") or {}).get("q") or body.get("q") or "")
        result = None
        include = body.get("include_search", body.get("execute_search", True))
        if str(include).lower() not in {"0", "false", "no"}:
            from app.db.session import session_scope
            from app.retrieval.multi_query import multi_hybrid_search
            from app.retrieval.search import SearchFilters, SearchRequest

            try:
                from app.embed.model import embed_query

                embed_fn = embed_query
                qvec = embed_query(q)
            except Exception:  # noqa: BLE001
                embed_fn = None
                qvec = None
            req = SearchRequest(
                q=q,
                top_k=int(body.get("top_k") or 10),
                filters=SearchFilters(status="active"),
            )
            with session_scope() as session:
                resp, meta = multi_hybrid_search(
                    session, req, query_vector=qvec, embed_fn=embed_fn, multi_query=True
                )
            result = {
                "total": resp.total,
                "vector_used": meta.get("vector_used"),
                "multi_query": meta.get("multi_query"),
                "expanded_queries": meta.get("queries"),
                "items": [
                    {
                        "external_id": r.external_id,
                        "title": r.title,
                        "score": r.score,
                        "snippet": r.snippet,
                        "source_type": getattr(r, "source_type", None),
                    }
                    for r in resp.results
                ],
            }
        return {
            "intent": "hybrid_search",
            "params": plan.get("params") or {"q": q, "multi_query": True},
            "result": result,
            "note": plan.get("note")
            or "일반 검색 의도 — multi-query hybrid (답변은 POST /v1/chat)",
        }


    return {"intent": intent or "error", "params": plan, "note": "unhandled intent"}


def route_query(q: str, *, body: Optional[dict[str, Any]] = None, execute: bool = True) -> dict[str, Any]:
    plan = plan_query(q)
    if plan.get("intent") == "error":
        return plan
    if not execute:
        return {"intent": plan.get("intent"), "params": plan, "executed": False}
    return execute_plan(plan, body=body or {"q": q})
