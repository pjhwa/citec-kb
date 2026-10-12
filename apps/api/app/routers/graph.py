"""POST /v1/graph/explore — 지식그래프 조회(읽기 전용). 쓰기 경로는 scripts/graph_sync.sh
배치뿐이고 이 라우터는 전혀 건드리지 않는다(1단계 설계 §0 원칙 유지)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.models import Document
from app.db.session import session_scope
from app.graph.explore import (
    dedup_by_key,
    enrich_with_evidence_grade,
    filter_excluded_documents,
    resolve_component_anchor,
    shape_explore_result,
)
from app.graph.extract import extract_lexicon_components
from app.graph.identity import resolve_representative
from app.graph.neo4j_client import Neo4jClient, get_shared_client
from app.graph.sync_state import get_latest_synced_at
from app.lexicon.seed import load_lexicon_map

router = APIRouter(prefix="/v1", tags=["graph"])


def _build_client() -> Neo4jClient:
    """요청마다 새 Driver를 만들지 않도록 프로세스 전역 공유 클라이언트를 재사용한다
    (Fix 2) — app.graph.neo4j_client.get_shared_client()가 lazily 생성하고,
    app.main의 lifespan이 종료 시 close_shared_client()로 정리한다."""
    return get_shared_client()


class GraphExploreBody(BaseModel):
    anchor_type: Literal["failure_bucket", "document", "component", "symptom_text"]
    anchor_value: str = Field(..., min_length=1, max_length=2000)
    # G04 — app.retrieval.search.SearchFilters와 동일한 의미. candidate 생성
    # 이후, dedup/정렬/cap 이전에 적용한다(최소 구현 — 중간 경로 bridge 차단,
    # anchor 자체 제외는 이번 범위 밖, docs/features/graph-evidence.md 참고).
    exclude_page_ids: list[str] = Field(default_factory=list)
    exclude_subtree_ids: list[str] = Field(default_factory=list)
    exclude_source_types: list[str] = Field(default_factory=list)
    # G07 — 카테고리별 독립 페이지 시작 위치. cap(50) 뒤에 숨은 후보에 접근하는
    # 유일한 수단이다.
    documents_offset: int = Field(default=0, ge=0)
    components_offset: int = Field(default=0, ge=0)
    failure_buckets_offset: int = Field(default=0, ge=0)


def _rows_to_candidates(rows) -> list[dict[str, Any]]:
    return [
        {"document_id": r[0], "evidence_grade": r[1], "status": r[2], "source_type": r[3],
         "space_key": (r[4] or {}).get("space_key")}
        for r in rows
    ]


_DOC_IDENTITY_COLUMNS = (
    Document.id, Document.evidence_grade, Document.status, Document.source_type, Document.metadata_,
)


def _resolve_document_anchor_ids(anchor_value: str) -> tuple[str, list[str]]:
    """document 앵커는 documents.id 또는 external_id 둘 다 받는다. G01: 같은
    external_id를 가진 수집본이 여러 개(예: confluence_map과 tech_repo가 같은
    페이지를 따로 수집)일 수 있는데, 그래프 구조(PARENT_OF 등)는 그중 결정적으로
    고른 "대표" 문서에만 붙는다 — 그래서 어느 수집본으로 앵커를 걸어도 같은
    계층/관계를 찾으려면, resolved_id는 대표 하나를 보여주되 explore()는 같은
    external_id를 공유하는 모든 수집본에 대해 수행하고 병합해야 한다.
    반환: (응답에 보여줄 대표 document_id, 실제로 explore할 전체 document_id 목록)."""
    with session_scope() as session:
        doc = session.get(Document, anchor_value)
        external_id = doc.external_id if doc is not None else anchor_value
        if not external_id:
            # external_id가 NULL/빈 문자열이면 Document.external_id == external_id가
            # SQL의 "IS NULL"로 해석돼 같은 상태인 모든 문서로 fan-out하는 사고가
            # 난다(2차 리뷰에서 지적) — 그 경우는 fan-out 없이 단일 앵커로 둔다.
            return (doc.id if doc is not None else anchor_value), [doc.id if doc is not None else anchor_value]
        rows = session.execute(
            select(*_DOC_IDENTITY_COLUMNS).where(Document.external_id == external_id)
        ).all()
    candidates = _rows_to_candidates(rows)
    if not candidates:
        # external_id로도 못 찾았다 — anchor_value 자체가 documents.id일 수 있으니
        # 그대로 단일 앵커로 둔다(존재하지 않으면 404로 자연히 떨어진다).
        return anchor_value, [anchor_value]
    best = resolve_representative(candidates)
    representative_id = best["document_id"] if best else anchor_value
    # 정렬 — SQL 쪽에 ORDER BY가 없어 fan-out 앵커 순서가 요청마다 달라질 수
    # 있었다(2차 리뷰 지적). explore() 호출 순서가 바뀌어도 merged 결과 자체는
    # dedup_by_key의 결정적 tie-break로 같아지지만, 중간 과정(어떤 anchor가
    # 먼저 hub-check에 걸리는지 등)까지 재현 가능하게 정렬해 둔다.
    all_ids = sorted(c["document_id"] for c in candidates)
    return representative_id, all_ids


def _fetch_evidence_grades(document_ids: list[str]) -> dict[str, str]:
    if not document_ids:
        return {}
    with session_scope() as session:
        rows = session.execute(
            select(Document.id, Document.evidence_grade).where(Document.id.in_(document_ids))
        ).all()
    return {row[0]: row[1] for row in rows}


def _fetch_ancestor_ids(document_ids: list[str]) -> dict[str, list[str]]:
    if not document_ids:
        return {}
    with session_scope() as session:
        rows = session.execute(
            select(Document.id, Document.metadata_).where(Document.id.in_(document_ids))
        ).all()
    return {row[0]: list((row[1] or {}).get("ancestor_ids") or []) for row in rows}


@router.post("/graph/explore")
def graph_explore(body: GraphExploreBody) -> dict[str, Any]:
    as_of = get_latest_synced_at()
    as_of_str = as_of.date().isoformat() if as_of else None
    lexicon_map = load_lexicon_map()

    # 이 함수는 더 이상 client.close()를 호출하지 않는다(Fix 2) — _build_client()가
    # 반환하는 공유 Neo4jClient는 프로세스 전역에서 재사용되며, app.main의 lifespan
    # 종료 시 한 번만 닫힌다. 그래서 더 이상 try/finally로 감쌀 필요가 없다 — 아래
    # 두 try/except는 각각 Neo4j 호출 구간만 격리해 503으로 변환한다(Fix 3 검토 결과:
    # 그 사이에 있는 document 앵커 분기는 Postgres를 쓰므로 하나로 합치면 Postgres
    # 오류가 "그래프 저장소 연결 실패"로 잘못 표시될 수 있어 합치지 않았다).
    client = _build_client()
    resolved_name: str | None = None
    resolved_document_id: str | None = None
    anchors: list[tuple[str, str, str]] = []
    if body.anchor_type == "symptom_text":
        fake_doc = {"body_md": body.anchor_value}
        edges = extract_lexicon_components(fake_doc, lexicon_map=lexicon_map)
        if not edges:
            return shape_explore_result(
                {"found": True, "documents": [], "components": [], "failure_buckets": [],
                 "excluded_hub_components": []},
                as_of=as_of_str,
                anchor={
                    "type": "symptom_text", "resolved_id": None, "resolved_name": None,
                    "matched_components": [],
                },
                status="no_match",
            )
        # 중복 target_value 제거 — 같은 component로 두 번 explore() 안 부르게
        seen_values: set[str] = set()
        anchors = []
        for e in edges:
            if e.target_value not in seen_values:
                seen_values.add(e.target_value)
                anchors.append(("Component", "canonical_name", e.target_value))
    elif body.anchor_type == "component":
        canonical = resolve_component_anchor(body.anchor_value, lexicon_map)
        if canonical == body.anchor_value:
            # lexicon이 못 알아들었다 — 대소문자만 다른 그래프 노드가 있는지 확인
            try:
                ci_match = client.resolve_component_case_insensitive(canonical)
            except Exception as exc:  # noqa: BLE001 — Neo4j 장애 격리
                raise HTTPException(
                    status_code=503, detail=f"그래프 저장소 연결 실패: {exc}"
                ) from exc
            if ci_match:
                canonical = ci_match
        anchors = [("Component", "canonical_name", canonical)]
        resolved_name = canonical
    elif body.anchor_type == "document":
        resolved_document_id, document_ids = _resolve_document_anchor_ids(body.anchor_value)
        anchors = [("Document", "id", doc_id) for doc_id in document_ids]
    else:  # failure_bucket
        anchors = [("FailureBucket", "id", body.anchor_value)]

    merged: dict[str, Any] = {
        "found": False, "documents": [], "components": [], "failure_buckets": [],
        "excluded_hub_components": [],
    }
    try:
        for label, key, value in anchors:
            # Fix 1 (스펙 §2): 앵커 자신이 허브 Component면 2hop 순회를 생략하고
            # 즉시 excluded_hub_components에만 기록한다 — 토큰 낭비 방지. 이 체크를
            # 루프 안에 두면 symptom_text의 멀티 앵커 경로에서 매칭된 Component가
            # 허브인 경우도 같은 방식으로 처리된다(단일 component 앵커 타입만이 아니라).
            if label == "Component":
                hub_check = client.is_component_hub(value)
                if hub_check is True:
                    merged["found"] = True
                    merged["excluded_hub_components"].append(value)
                    continue
            raw = client.explore(label, key, value, max_hops=2)
            if not raw.get("found"):
                continue
            merged["found"] = True
            merged["documents"].extend(raw["documents"])
            merged["components"].extend(raw["components"])
            merged["failure_buckets"].extend(raw["failure_buckets"])
            merged["excluded_hub_components"].extend(raw["excluded_hub_components"])
    except Exception as exc:  # noqa: BLE001 — Neo4j 장애가 기존 검색/API에 안 퍼지게 격리
        raise HTTPException(status_code=503, detail=f"그래프 저장소 연결 실패: {exc}") from exc

    if not merged["found"] and body.anchor_type != "symptom_text":
        raise HTTPException(status_code=404, detail="anchor_value를 그래프에서 찾을 수 없습니다")

    if body.anchor_type == "document":
        # G01 fan-out 부작용: 같은 external_id의 다른 수집본들은 본문/컴포넌트가
        # 같아 서로의 2hop 이웃으로 잡힌다(둘 다 같은 Component를 거쳐 도달) —
        # 앵커 자신의 복제본들은 "연관 문서"가 아니라 앵커 자신이므로 제외한다.
        fan_out_ids = {a[2] for a in anchors}
        merged["documents"] = [d for d in merged["documents"] if d["id"] not in fan_out_ids]

    # G04 — 제외 필터를 dedup/정렬/cap 이전에 적용
    if body.exclude_page_ids or body.exclude_subtree_ids or body.exclude_source_types:
        ancestor_ids_by_doc_id = _fetch_ancestor_ids([d["id"] for d in merged["documents"]])
        merged["documents"] = filter_excluded_documents(
            merged["documents"],
            exclude_page_ids=set(body.exclude_page_ids),
            exclude_subtree_ids=set(body.exclude_subtree_ids),
            exclude_source_types=set(body.exclude_source_types),
            ancestor_ids_by_doc_id=ancestor_ids_by_doc_id,
        )

    # 복수 앵커(symptom_text가 여러 component에 매칭된 경우) 결과 병합 시 중복 제거
    merged["documents"] = dedup_by_key(merged["documents"], "id")
    merged["components"] = dedup_by_key(merged["components"], "canonical_name")
    merged["failure_buckets"] = dedup_by_key(merged["failure_buckets"], "id")
    merged["excluded_hub_components"] = sorted(set(merged["excluded_hub_components"]))

    doc_ids = [d["id"] for d in merged["documents"]]
    merged["documents"] = enrich_with_evidence_grade(merged["documents"], _fetch_evidence_grades(doc_ids))

    if body.anchor_type == "symptom_text":
        anchor_info: dict[str, Any] = {
            "type": "symptom_text", "resolved_id": None, "resolved_name": None,
            "matched_components": [a[2] for a in anchors],
        }
    elif body.anchor_type == "document":
        anchor_info = {
            "type": body.anchor_type, "resolved_id": resolved_document_id, "resolved_name": resolved_name,
        }
    else:
        anchor_info = {
            "type": body.anchor_type, "resolved_id": anchors[0][2], "resolved_name": resolved_name,
        }

    return shape_explore_result(
        merged, as_of=as_of_str, anchor=anchor_info,
        offsets={
            "documents": body.documents_offset,
            "components": body.components_offset,
            "failure_buckets": body.failure_buckets_offset,
        },
    )
