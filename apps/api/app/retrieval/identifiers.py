"""Query identifiers that should exist in the corpus (A6).

질의에 `CITECTS-99999` 같은 식별자가 있는데 코퍼스에 그런 문서가 없으면, 검색은
식별자와 무관한 문서를 점수순으로 돌려주면서도 그럴듯한 trust를 줄 수 있다. 여기서는
질의의 "문서 ID처럼 생긴" 토큰만 골라 코퍼스 존재 여부를 확인하고, 없는 것을 응답에
싣는다. (fusion.extract_exact_tokens 의 `\\d{6,}` 같은 느슨한 패턴은 일반 숫자에도 걸려
오탐이 나므로 쓰지 않는다.)
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Document

# re.ASCII: 한글 조사가 바로 붙어도("CITECTS-2502의", "26051461110은") 경계로 본다.
_ID_PATTERNS = [
    re.compile(r"\bCITECTS-\d+\b", re.I | re.A),
    re.compile(r"\bISS-\d{8}-\d+\b", re.I | re.A),
    re.compile(r"\bPISA[A-Z0-9]+_\d+(?:\.\d+)+\b", re.I | re.A),
    re.compile(r"\bconfluence_\d+\b", re.I | re.A),
    re.compile(r"(?<![A-Za-z0-9_.])\d{9,}(?![A-Za-z0-9_.])"),  # SWIM 번호(11자리)·페이지 ID(10자리)
]


def identifier_tokens(query: str) -> list[str]:
    found: list[str] = []
    for pat in _ID_PATTERNS:
        for m in pat.finditer(query or ""):
            tok = m.group(0)
            if tok.lower() not in (f.lower() for f in found):
                found.append(tok)
    return found


def _external_id_forms(token: str) -> list[str]:
    t = token.strip()
    forms = [t.lower()]
    if t.lower().startswith("confluence_"):
        forms.append(t[len("confluence_"):].lower())
    return forms


def missing_identifiers(session: Session, tokens: Iterable[str]) -> list[str]:
    """tokens 중 어떤 활성 문서의 external_id 로도 존재하지 않는 것."""
    missing: list[str] = []
    for tok in tokens:
        forms = _external_id_forms(tok)
        hit = session.scalars(
            select(Document.id)
            .where(Document.status == "active")
            .where(func.lower(Document.external_id).in_(forms))
            .limit(1)
        ).first()
        if hit is None:
            missing.append(tok)
    return missing


def adjust_trust(trust: str, tokens: list[str], missing: list[str]) -> str:
    """질의의 식별자가 전부 코퍼스에 없으면 trust 를 weak 로 내린다(올리지는 않는다).

    일부만 없으면(다른 식별자는 존재) trust 는 그대로 두고 경고만 남긴다.
    """
    if tokens and missing and len(missing) == len(tokens) and trust in {"strong", "medium"}:
        return "weak"
    return trust


def check_query_identifiers(
    session: Session, query: str, trust: str
) -> tuple[str, list[str]]:
    tokens = identifier_tokens(query)
    if not tokens:
        return trust, []
    try:
        missing = missing_identifiers(session, tokens)
    except Exception:  # noqa: BLE001 — 부가 점검이 검색 자체를 깨면 안 된다
        return trust, []
    return adjust_trust(trust, tokens, missing), missing
