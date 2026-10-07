"""FailureBucket 전용 추출기 — (:FailureBucket)은 (:Document)와 별도 레이블이라
app.graph.extract의 Document 추출기와 입력 타입이 다르다(스펙 §3.2)."""

from __future__ import annotations

import re

from app.graph.extract import Edge

_GRADE_RANK = {"A": 0, "A-": 1, "B": 2, "C": 3, "machine": 4, "draft": 5}
_CONFLUENCE_RE = re.compile(r"confluence:[A-Za-z0-9_]+/(\d+)", re.IGNORECASE)
_CITECTS_RE = re.compile(r"citects-\d+", re.IGNORECASE)


def best_candidate(candidates: list[dict]) -> str | None:
    if not candidates:
        return None
    best = min(candidates, key=lambda c: _GRADE_RANK.get(c["evidence_grade"], 99))
    return best["document_id"]


def extract_evidence(bucket: dict, *, external_id_index: dict[str, list[dict]]) -> list[Edge]:
    """evidence_ref에서 confluence:<space>/<pageId> 또는 CITECTS-#### 를 뽑아
    external_id_index(= external_id -> [{"document_id","evidence_grade"}, ...])로
    해석한다. capture:/log:/legacy: 등은 코퍼스 밖 아티팩트라 건너뛴다."""
    ref = bucket.get("evidence_ref") or ""
    targets: list[str] = []

    m = _CONFLUENCE_RE.search(ref)
    if m:
        page_id = m.group(1)
        best = best_candidate(external_id_index.get(page_id, []))
        if best:
            targets.append(best)

    for m in _CITECTS_RE.finditer(ref):
        key = m.group(0).lower()
        best = best_candidate(external_id_index.get(key, []))
        if best:
            targets.append(best)

    seen: set[str] = set()
    edges: list[Edge] = []
    for t in targets:
        if t in seen:
            continue
        seen.add(t)
        edges.append(
            Edge(
                rel_type="HAS_EVIDENCE",
                target_label="Document",
                target_key="id",
                target_value=t,
                tag="EXTRACTED",
            )
        )
    return edges
