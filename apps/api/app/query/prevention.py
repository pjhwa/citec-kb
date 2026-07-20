"""Prevention hop: similar incidents + related checkitems (no LLM numbers)."""

from __future__ import annotations

import re
from typing import Any, Optional

from app.db.session import session_scope
from app.routers.checkitems import list_checkitems
from app.si.retrieve import similar_incidents

_PREVENTION = re.compile(
    r"예방|재발\s*방지|방지\s*조치|무엇을\s*점검|점검해야|"
    r"후속\s*개선|안정화|취약\s*시\s*문제",
    re.I,
)


def detect_prevention_intent(text: str) -> Optional[dict[str, Any]]:
    t = (text or "").strip()
    if not t or not _PREVENTION.search(t):
        return None
    # prefer SI wording for pure similar-incident
    if re.search(r"유사\s*장애|과거\s*유사", t) and not re.search(r"예방|재발\s*방지|점검해야", t):
        return None
    area = None
    if re.search(r"Linux|리눅스", t, re.I):
        area = "Linux"
    elif re.search(r"Redis|레디스", t, re.I):
        area = None  # checkitems q=
    q_terms = []
    for pat, term in [
        (r"Redis|레디스", "Redis"),
        (r"파일\s*시스템|fsck", "파일시스템"),
        (r"네트워크|timeout|타임아웃", "네트워크"),
        (r"인증서", "인증서"),
    ]:
        if re.search(pat, t, re.I):
            q_terms.append(term)
    return {
        "intent": "prevention",
        "symptom": t,
        "area": area,
        "checkitem_q": " ".join(q_terms) if q_terms else None,
        "endpoint": "SI + checkitems hop",
    }


def run_prevention(
    *,
    symptom: str,
    area: Optional[str] = None,
    checkitem_q: Optional[str] = None,
    top_k: int = 3,
) -> dict[str, Any]:
    si = similar_incidents(symptom=symptom, top_k=top_k)
    checks = list_checkitems(
        q=checkitem_q or symptom[:80],
        area=area,
        category_1=None,
        limit=15,
        offset=0,
    )
    # build short action hints from SI resolutions + checkitem subjects
    actions: list[str] = []
    for c in (si.get("cases") or [])[:3]:
        res = (c.get("resolution") or "").strip()
        if res:
            actions.append(f"[{c.get('external_id')}] {res[:160]}")
    for it in (checks.get("items") or [])[:8]:
        subj = it.get("subject") or it.get("code")
        if subj:
            actions.append(f"PISA {it.get('code') or ''}: {subj}"[:180])

    return {
        "brief": (
            f"예방 점검 패키지: 유사사례 {len(si.get('cases') or [])}건 + "
            f"체크항목 {checks.get('total') or 0}건 (Rules/SI hop, LLM 미사용)."
        ),
        "similar_incident": si,
        "checkitems": checks,
        "actions": actions[:12],
        "method": "prevention_hop",
        "llm_used": False,
    }
