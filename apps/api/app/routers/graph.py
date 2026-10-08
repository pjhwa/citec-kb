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
from app.graph.neo4j_client import Neo4jClient
from app.graph.sync_state import get_latest_synced_at
from app.lexicon.seed import load_lexicon_map

router = APIRouter(prefix="/v1", tags=["graph"])


def _build_client() -> Neo4jClient:
    return Neo4jClient()


class GraphExploreBody(BaseModel):
    anchor_type: Literal["failure_bucket", "document", "component", "symptom_text"]
    anchor_value: str = Field(..., min_length=1, max_length=2000)


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

    anchors: list[tuple[str, str, str]] = []
    if body.anchor_type == "symptom_text":
        fake_doc = {"body_md": body.anchor_value}
        edges = extract_lexicon_components(fake_doc, lexicon_map=lexicon_map)
        if not edges:
            return shape_explore_result(
                {"found": True, "documents": [], "components": [], "failure_buckets": [],
                 "excluded_hub_components": []},
                as_of=as_of_str,
                anchor={"type": "symptom_text", "resolved_id": None, "matched_components": []},
            )
        anchors = [("Component", "canonical_name", e.target_value) for e in edges]
    elif body.anchor_type == "component":
        canonical = resolve_component_anchor(body.anchor_value, lexicon_map)
        anchors = [("Component", "canonical_name", canonical)]
    elif body.anchor_type == "document":
        anchors = [("Document", "id", _resolve_document_anchor_value(body.anchor_value))]
    else:  # failure_bucket
        anchors = [("FailureBucket", "id", body.anchor_value)]

    client = _build_client()
    try:
        merged: dict[str, Any] = {
            "found": False, "documents": [], "components": [], "failure_buckets": [],
            "excluded_hub_components": [],
        }
        for label, key, value in anchors:
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
    finally:
        client.close()

    if not merged["found"] and body.anchor_type != "symptom_text":
        raise HTTPException(status_code=404, detail="anchor_value를 그래프에서 찾을 수 없습니다")

    doc_ids = [d["id"] for d in merged["documents"]]
    merged["documents"] = enrich_with_evidence_grade(merged["documents"], _fetch_evidence_grades(doc_ids))

    anchor_info: dict[str, Any] = {"type": body.anchor_type, "resolved_id": body.anchor_value}
    if body.anchor_type == "symptom_text":
        anchor_info["matched_components"] = [a[2] for a in anchors]

    return shape_explore_result(merged, as_of=as_of_str, anchor=anchor_info)
