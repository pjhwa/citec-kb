"""FailureBucket 전용 추출기 — (:FailureBucket)은 (:Document)와 별도 레이블이라
app.graph.extract의 Document 추출기와 입력 타입이 다르다(스펙 §3.2)."""

from __future__ import annotations

import base64
import binascii
import re

from app.failure_buckets.match import rank_buckets
from app.graph.extract import Edge
from app.graph.identity import best_candidate_id as best_candidate

_CONFLUENCE_RE = re.compile(r"confluence:(?:[A-Za-z0-9_]+/)?(\d+)", re.IGNORECASE)
_CITECTS_RE = re.compile(r"citects-\d+", re.IGNORECASE)
_SIMILAR_TO_THRESHOLD = 0.75  # apps/api/app/failure_buckets/service.py:99 와 동일하게 유지

# G10 — evidence_ref 밖, root_cause/recommended_action 본문에 명시된 Confluence
# URL(예: "분석 원문: https://devops.sdsdev.co.kr/confluence/x/PVSFlw")을 찾는다.
# 정해진 호스트만 — 임의 외부 URL은 절대 fetch하지 않는다(§2.7 금지사항).
_CONFLUENCE_HOST = "devops.sdsdev.co.kr/confluence"
_TINY_LINK_RE = re.compile(
    # 코드 전체(-, _ 포함)를 캡처해야 decode_confluence_tiny_link의 URL-safe
    # 문자 거부가 실제로 작동한다 — [A-Za-z0-9]+만 캡처하면 "/x/ab-cd_e"가
    # "ab"로 잘려 들어가 거부 로직을 거치지 않고 잘못된 page id로 디코딩될
    # 뻔했다(2차 리뷰에서 지적).
    rf"https?://{re.escape(_CONFLUENCE_HOST)}/x/([A-Za-z0-9_\-]+)"
)
_PAGE_ID_PARAM_RE = re.compile(
    rf"https?://{re.escape(_CONFLUENCE_HOST)}/[^\s)]*pageId=(\d+)"
)
_CAPTURE_LOG_RE = re.compile(r"\b(?:capture|log):", re.IGNORECASE)
_LEGACY_RE = re.compile(r"\blegacy:", re.IGNORECASE)

_EVIDENCE_REASONS = ("resolved", "external_artifact", "legacy_missing",
                     "target_not_ingested", "unresolved_reference")


def classify_evidence(
    bucket: dict, *, external_id_index: dict[str, list[dict]],
    page_identity_index: dict[str, list[dict]] | None = None,
) -> dict[str, int]:
    """evidence_ref 토큰과 root_cause/recommended_action에 언급된 URL을 모두
    사유별로 집계한다(§2.7 "resolved/external_artifact/legacy_missing/
    target_not_ingested/unresolved_reference 사유별 수 제공" 요구) — edge 생성
    (extract_evidence/extract_mentioned_references)과는 독립적인 참고 집계라,
    한 bucket이 여러 사유를 동시에 가질 수 있다(예: evidence_ref는 log: 하나뿐
    이지만 recommended_action에 resolved되는 URL이 있으면 둘 다 +1)."""
    reasons = {r: 0 for r in _EVIDENCE_REASONS}
    ref = bucket.get("evidence_ref") or ""

    for m in _CONFLUENCE_RE.finditer(ref):
        key = m.group(1)
        reasons["resolved" if best_candidate(external_id_index.get(key, [])) else "target_not_ingested"] += 1
    for m in _CITECTS_RE.finditer(ref):
        key = m.group(0).lower()
        reasons["resolved" if best_candidate(external_id_index.get(key, [])) else "target_not_ingested"] += 1
    reasons["external_artifact"] += len(_CAPTURE_LOG_RE.findall(ref))
    reasons["legacy_missing"] += len(_LEGACY_RE.findall(ref))
    if ref.strip() and not any(
        reasons[r] for r in ("resolved", "target_not_ingested", "external_artifact", "legacy_missing")
    ):
        reasons["unresolved_reference"] += 1

    text = f"{bucket.get('root_cause') or ''} {bucket.get('recommended_action') or ''}"
    page_index = page_identity_index or {}
    for m in _TINY_LINK_RE.finditer(text):
        page_id = decode_confluence_tiny_link(m.group(1))
        if page_id is None:
            continue
        key = str(page_id).lower()
        reasons["resolved" if best_candidate(page_index.get(key, [])) else "target_not_ingested"] += 1
    for m in _PAGE_ID_PARAM_RE.finditer(text):
        key = m.group(1).lower()
        reasons["resolved" if best_candidate(page_index.get(key, [])) else "target_not_ingested"] += 1

    return reasons


def decode_confluence_tiny_link(code: str) -> int | None:
    """Confluence "tiny link"(/x/<code>)는 page id(4바이트 little-endian)를
    base64로 인코딩한 뒤 끝의 'A'(값 0인 6비트 묶음) 런을 잘라낸 것이다 —
    디코딩은 그 반대로 'A'를 다시 채워 넣는다. 알려진 두 쌍으로 검증됨:
    "9uAFJ" -> 604365046, "PVSFlw" -> 2542097469. code에 -/_/+//=가 있으면
    (URL-safe 변형 매핑이 검증되지 않았으므로) None — 추정으로 채우지 않는다."""
    if not code or any(c in code for c in "-_+/="):
        return None
    try:
        raw = base64.b64decode(code + "A" * (-len(code) % 4))
    except (binascii.Error, ValueError):
        return None
    # 'A' 패딩은 추가 base64 문자를 1개 더 요구할 수 있어(예: 길이 5 -> 3개 추가 ->
    # 8문자 -> 6바이트) 4바이트보다 길게 디코딩될 수 있다 — 하지만 패딩이 만드는
    # 초과 바이트는 little-endian에서 최상위(=끝)에 오는 0이라 값에 영향이 없다
    # (검증: "9uAFJ"(5자)도 6바이트로 디코딩되지만 604365046과 정확히 일치).
    return int.from_bytes(raw, "little")


def extract_mentioned_references(
    bucket: dict, *, page_identity_index: dict[str, list[dict]] | None = None,
) -> list[Edge]:
    """evidence_ref가 아니라 root_cause/recommended_action 본문에 명시적으로
    언급된 Confluence 원문 URL을 REFERENCES로 낸다(HAS_EVIDENCE가 아님 — 단순
    언급과 명시적 근거 연결은 구분해야 한다, §2.7). URL을 fetch하지 않고 로컬
    디코딩만 한다. page_identity_index로 해석 안 되면(코퍼스에 없음) 조용히
    건너뛴다 — 없는 페이지를 가리키는 edge를 만들지 않는다."""
    text = f"{bucket.get('root_cause') or ''} {bucket.get('recommended_action') or ''}"
    if not text.strip():
        return []
    index = page_identity_index or {}

    raw_ids: set[str] = set()
    for m in _TINY_LINK_RE.finditer(text):
        page_id = decode_confluence_tiny_link(m.group(1))
        if page_id is not None:
            raw_ids.add(str(page_id))
    for m in _PAGE_ID_PARAM_RE.finditer(text):
        raw_ids.add(m.group(1))

    targets: list[str] = []
    seen: set[str] = set()
    for raw_id in sorted(raw_ids):
        resolved = best_candidate(index.get(raw_id.lower(), []))
        if resolved and resolved not in seen:
            seen.add(resolved)
            targets.append(resolved)

    return [
        Edge(rel_type="REFERENCES", target_label="Document", target_key="id",
             target_value=t, tag="EXTRACTED")
        for t in targets
    ]


def extract_evidence(bucket: dict, *, external_id_index: dict[str, list[dict]]) -> list[Edge]:
    """evidence_ref에서 confluence:<space>/<pageId> 또는 CITECTS-#### 를 뽑아
    external_id_index(= external_id -> [{"document_id","evidence_grade"}, ...])로
    해석한다. capture:/log:/legacy: 등은 코퍼스 밖 아티팩트라 건너뛴다."""
    ref = bucket.get("evidence_ref") or ""
    targets: list[str] = []

    for m in _CONFLUENCE_RE.finditer(ref):
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


def extract_similar_to(bucket: dict, *, other_buckets: list[dict]) -> list[Edge]:
    """app.failure_buckets.match.rank_buckets()를 그대로 재사용 — 새 스코어러를
    만들지 않는다. others에서 자기 자신(id 동일)은 제외."""
    candidates = [b for b in other_buckets if b.get("id") != bucket.get("id")]
    if not candidates:
        return []
    ranked = rank_buckets(
        observed_signals=bucket.get("discriminating_signals") or [],
        symptom=bucket.get("symptom") or "",
        buckets=candidates,
        top_k=len(candidates),
    )
    return [
        Edge(
            rel_type="SIMILAR_TO",
            target_label="FailureBucket",
            target_key="id",
            target_value=r["bucket_id"],
            tag="INFERRED",
        )
        for r in ranked
        if r["confidence"] >= _SIMILAR_TO_THRESHOLD
    ]
