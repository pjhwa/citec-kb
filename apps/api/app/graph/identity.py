"""Page/document identity resolution — single source of truth for "which
Postgres documents.id represents this external_id/raw page id" (G01).

Pure functions only (no DB access here) — app.graph.pipeline builds the
candidate index from Postgres and passes it in, same pattern as the existing
external_id_index. This lets extract_hierarchy/extract_references/
extract_evidence and the graph router's document-anchor lookup all share one
deterministic tie-break instead of each picking an arbitrary first row.
"""

from __future__ import annotations

from typing import Any, Optional

# 단일 소스 — app.graph.explore과 app.graph.failure_bucket이 여기서 가져온다.
# "A-"가 빠져 C와 동일하게 정렬되던 버그(G09)를 이 통합으로 제거한다.
GRADE_RANK: dict[str, int] = {"A": 0, "A-": 1, "B": 2, "C": 3, "machine": 4, "draft": 5}

# app.graph.pipeline.compute_priority_tier와 동일한 승격 정책 — 여기서 다시
# import하면 pipeline(이 모듈의 호출자) <-> identity 순환 임포트가 생기므로,
# tie-break에 필요한 최소 로직만 복제한다. 정책이 바뀌면 두 곳을 함께 고친다.
_TIER_1_SOURCE_TYPES = {"tech_repo", "confluence_docs", "checkitem"}
_TIER_1_CONFLUENCE_MAP_SPACES = {"LOOKIN", "TechRepo"}
_TIER_2_SOURCE_TYPES = {"incident_reports"}


def _source_tier(source_type: str | None, space_key: str | None) -> int:
    if source_type in _TIER_1_SOURCE_TYPES:
        return 1
    if source_type == "confluence_map":
        return 1 if space_key in _TIER_1_CONFLUENCE_MAP_SPACES else 3
    if source_type in _TIER_2_SOURCE_TYPES:
        return 2
    return 3


def _tie_break_key(candidate: dict[str, Any]) -> tuple:
    status_rank = 0 if candidate.get("status") == "active" else 1
    grade_rank = GRADE_RANK.get(candidate.get("evidence_grade") or "", 99)
    tier_rank = _source_tier(candidate.get("source_type"), candidate.get("space_key"))
    return (status_rank, grade_rank, tier_rank, candidate.get("document_id") or "")


def resolve_representative(candidates: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """같은 external_id/raw page id를 가리키는 여러 documents 행 중 대표 1건을
    결정적으로 고른다. tie-break: active 상태 > evidence_grade > source tier >
    document_id 오름차순. 후보가 비면 None."""
    if not candidates:
        return None
    return min(candidates, key=_tie_break_key)


def best_candidate_id(candidates: list[dict[str, Any]]) -> Optional[str]:
    """resolve_representative()에서 document_id만 뽑는 래퍼 — 기존
    app.graph.failure_bucket.best_candidate()와
    app.routers.graph._resolve_document_anchor_value()의 "임의 첫 행" 선택을
    대체한다(동일 정책 재사용)."""
    best = resolve_representative(candidates)
    return best["document_id"] if best else None
