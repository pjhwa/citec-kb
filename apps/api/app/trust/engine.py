"""Multi-signal Trust assessment for RAG answers.

Design: four discrete levels + reasons — never a single 0–100 confidence score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


@dataclass
class TrustAssessment:
    level: str  # strong | medium | weak | empty | abstain
    banner: str
    retrieval: str
    evidence: str
    faithfulness: str
    abstain: bool = False
    reasons: list[str] = field(default_factory=list)


_BANNERS = {
    "strong": "근거 충분 — 출처를 확인한 뒤 적용하세요.",
    "medium": "부분 근거 — 출처와 환경을 대조하세요.",
    "weak": "근거 약함 — 참고만 하고 단정하지 마세요.",
    "empty": "검색 결과 없음 — 질의를 바꾸거나 소스를 확인하세요.",
    "abstain": "기권 — 단정 답변을 제공하지 않습니다. 검색 결과·원문을 직접 확인하세요.",
}


def _level_rank(level: str) -> int:
    order = {"strong": 4, "medium": 3, "weak": 2, "empty": 1, "abstain": 0}
    return order.get(level, 0)


def assess_trust(
    *,
    retrieval_trust: str,
    n_hits: int,
    n_citations_used: int,
    answer: str,
    context_blobs: Sequence[str],
    force_abstain: bool = False,
) -> TrustAssessment:
    """Combine retrieval / evidence / lightweight faithfulness into one banner level."""
    reasons: list[str] = []
    retrieval = retrieval_trust if retrieval_trust in {"strong", "medium", "weak", "empty"} else "weak"

    if force_abstain or n_hits == 0 or retrieval == "empty":
        reasons.append("검색 히트 없음 또는 검색 trust=empty")
        return TrustAssessment(
            level="abstain",
            banner=_BANNERS["abstain"],
            retrieval=retrieval if n_hits else "empty",
            evidence="empty",
            faithfulness="n/a",
            abstain=True,
            reasons=reasons,
        )

    # Evidence: how many distinct sources the answer cites
    if n_citations_used >= 2 and n_hits >= 2:
        evidence = "strong"
    elif n_citations_used >= 1:
        evidence = "medium"
    else:
        evidence = "weak"
        reasons.append("답변에 [C#] 인용이 없거나 매핑 실패")

    # Lightweight faithfulness: overlapping content tokens (not LLM judge)
    faithfulness = _overlap_faithfulness(answer, context_blobs)
    if faithfulness == "weak":
        reasons.append("답변 토큰이 출처 스니펫과 거의 겹치지 않음")

    # Aggregate (min-ish with abstain rules)
    level = retrieval
    if _level_rank(evidence) < _level_rank(level):
        level = evidence
    if faithfulness == "weak" and level == "strong":
        level = "medium"
        reasons.append("faithfulness weak → trust 한 단계 하향")

    # Hard abstain: weak retrieval AND weak evidence
    abstain = False
    if retrieval == "weak" and evidence == "weak":
        abstain = True
        level = "abstain"
        reasons.append("검색·근거 모두 약함 → 기권")
    if n_hits > 0 and n_citations_used == 0 and len((answer or "").strip()) > 40:
        # Model answered without citations despite context
        if level not in {"abstain", "empty"}:
            level = "weak"
        reasons.append("인용 없이 장문 생성")

    return TrustAssessment(
        level=level,
        banner=_BANNERS.get(level, _BANNERS["weak"]),
        retrieval=retrieval,
        evidence=evidence,
        faithfulness=faithfulness,
        abstain=abstain,
        reasons=reasons,
    )


def _overlap_faithfulness(answer: str, contexts: Sequence[str]) -> str:
    import re

    ans = (answer or "").strip()
    if not ans or ans.startswith("기권") or "근거가 부족" in ans:
        return "n/a"
    if not contexts:
        return "weak"

    # Content tokens length >= 2 (Korean/English)
    tokens = [t for t in re.findall(r"[A-Za-z0-9가-힣_\-]{2,}", ans) if t.upper() not in {"C1", "C2", "C3"}]
    if not tokens:
        return "n/a"
    blob = "\n".join(contexts).lower()
    hits = sum(1 for t in tokens if t.lower() in blob)
    ratio = hits / max(len(tokens), 1)
    if ratio >= 0.25:
        return "ok"
    if ratio >= 0.1:
        return "medium"
    return "weak"


def trust_to_dict(t: TrustAssessment) -> dict[str, Any]:
    return {
        "level": t.level,
        "banner": t.banner,
        "retrieval": t.retrieval,
        "evidence": t.evidence,
        "faithfulness": t.faithfulness,
        "abstain": t.abstain,
        "reasons": t.reasons,
        # Explicit: no single confidence percentage by design
        "confidence_pct": None,
    }
