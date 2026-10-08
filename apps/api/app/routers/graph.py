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
    enrich_with_evidence_grade,
    resolve_component_anchor,
    shape_explore_result,
)
from app.graph.extract import extract_lexicon_components
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


def _dedup_by_key(items: list[dict], key: str) -> list[dict]:
    """복수 앵커(symptom_text가 여러 component에 매칭된 경우)의 explore() 결과를
    합칠 때 중복 제거 — 설계 스펙의 "복수 앵커를 합쳐 중복 제거" 요구. 중복이면
    hops가 더 작은(더 가까운) 쪽을 유지한다."""
    best: dict[str, dict] = {}
    for item in items:
        k = item[key]
        if k not in best or item.get("hops", 99) < best[k].get("hops", 99):
            best[k] = item
    return list(best.values())


def _resolve_document_anchor_value(anchor_value: str) -> str:
    """document 앵커는 documents.id 또는 external_id 둘 다 받는다 — Neo4j의
    Document.id는 항상 documents.id이므로, external_id로 왔으면 Postgres에서
    한 번 찾아 id로 바꾼다."""
    with session_scope() as session:
        doc = session.get(Document, anchor_value)
        if doc is not None:
            return doc.id
        doc = session.scalar(select(Document).where(Document.external_id == anchor_value))
        return doc.id if doc is not None else anchor_value


def _fetch_evidence_grades(document_ids: list[str]) -> dict[str, str]:
    if not document_ids:
        return {}
    with session_scope() as session:
        rows = session.execute(
            select(Document.id, Document.evidence_grade).where(Document.id.in_(document_ids))
        ).all()
    return {row[0]: row[1] for row in rows}


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
        anchors = [("Document", "id", _resolve_document_anchor_value(body.anchor_value))]
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

    # 복수 앵커(symptom_text가 여러 component에 매칭된 경우) 결과 병합 시 중복 제거
    merged["documents"] = _dedup_by_key(merged["documents"], "id")
    merged["components"] = _dedup_by_key(merged["components"], "canonical_name")
    merged["failure_buckets"] = _dedup_by_key(merged["failure_buckets"], "id")
    merged["excluded_hub_components"] = sorted(set(merged["excluded_hub_components"]))

    doc_ids = [d["id"] for d in merged["documents"]]
    merged["documents"] = enrich_with_evidence_grade(merged["documents"], _fetch_evidence_grades(doc_ids))

    if body.anchor_type == "symptom_text":
        anchor_info: dict[str, Any] = {
            "type": "symptom_text", "resolved_id": None, "resolved_name": None,
            "matched_components": [a[2] for a in anchors],
        }
    else:
        anchor_info = {
            "type": body.anchor_type, "resolved_id": anchors[0][2], "resolved_name": resolved_name,
        }

    return shape_explore_result(merged, as_of=as_of_str, anchor=anchor_info)
