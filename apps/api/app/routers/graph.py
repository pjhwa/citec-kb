"""POST /v1/graph/explore, GET /v1/graph/status — 지식그래프 조회(읽기 전용).
쓰기 경로는 scripts/graph_sync.sh 배치뿐이고 이 라우터는 전혀 건드리지
않는다(1단계 설계 §0 원칙 유지)."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from app.db.models import Document, GraphRun
from app.db.session import session_scope
from app.doc_access import attach_document_access
from app.graph.explore import (
    collapse_duplicate_copies,
    dedup_by_key,
    enrich_with_document_metadata,
    filter_excluded_documents,
    is_document_excluded,
    resolve_component_anchor,
    shape_explore_result,
)
from app.graph.extract import extract_lexicon_components
from app.graph.identity import resolve_representative
from app.graph.neo4j_client import Neo4jClient, get_shared_client
from app.graph.sync_state import get_latest_synced_at
from app.lexicon.seed import load_lexicon_map

router = APIRouter(prefix="/v1", tags=["graph"])
logger = logging.getLogger("citec.graph.router")

# F04(2026-10-13 최종 핸드오프) §9 — /v1/graph/status 응답 "모양" 자체의
# 버전. 필드를 추가/제거/의미 변경할 때 올린다. API_BUILD_SHA는 배포
# 파이프라인이 채워줘야 하는 값인데, 아직 그 배선이 없다 — 지어내지
# 않고 정직하게 "unknown"으로 보고한다(scripts/git_pull_deploy.sh를
# 사용자 승인 없이 고치지 않는다는 범위 제약).
GRAPH_STATUS_CONTRACT_VERSION = "2026-10-13.1"


def _is_backend_error(exc: Exception) -> bool:
    """advisor(2026-10-13 2차 핸드오프) 지적 — backend_error는 Neo4j 드라이버
    장애만 뜻해야 한다. 과거엔 bare except Exception이라 이 코드 자체의 버그도
    "그래프 저장소 장애"로 둔갑해 로그도 안 남고 조용히 숨었다. neo4j 패키지를
    여기서 직접 import하면(모듈 최상단에서) requirements-ci.txt가 neo4j를
    빼는 CI에서 app.main import 전체가 깨진다(이미 한 번 겪은 사고,
    neo4j_client.py의 지연 import와 같은 이유) — 그래서 예외 타입의 모듈
    이름만으로 판정한다(패키지 설치 여부와 무관하게 동작)."""
    return type(exc).__module__.split(".")[0] == "neo4j"


def _build_client() -> Neo4jClient:
    """요청마다 새 Driver를 만들지 않도록 프로세스 전역 공유 클라이언트를 재사용한다
    (Fix 2) — app.graph.neo4j_client.get_shared_client()가 lazily 생성하고,
    app.main의 lifespan이 종료 시 close_shared_client()로 정리한다."""
    return get_shared_client()


class GraphExploreBody(BaseModel):
    anchor_type: Literal["failure_bucket", "document", "component", "symptom_text"]
    anchor_value: str = Field(..., min_length=1, max_length=2000)
    # G04 — app.retrieval.search.SearchFilters와 동일한 의미. candidate 생성
    # 이후, dedup/정렬/cap 이전에 적용한다. anchor 자체가 제외 대상이면 status=
    # "filtered"(§G08)로 즉시 답한다. 2hop 중간 경로 bridge 차단은
    # exclude_page_ids/exclude_source_types만 적용(Neo4j 노드 속성으로 판정
    # 가능한 것들) — exclude_subtree_ids로의 bridge 차단은 범위 밖(ancestor_ids는
    # PG 메타데이터뿐이라 Neo4j 쿼리 중엔 못 봄, docs/features/graph-evidence.md).
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


_ENRICHMENT_COLUMNS = (
    Document.id, Document.evidence_grade, Document.status, Document.source_type,
    Document.external_id,
    Document.metadata_["space_key"].astext,
)


def _fetch_document_enrichment(document_ids: list[str]) -> dict[str, dict]:
    """G08 2차 리뷰 — grade/상태(archived 포함)/동일 페이지 판별에 필요한
    source_type·external_id·space_key를 PG 왕복 한 번으로 전부 가져온다
    (과거엔 _fetch_evidence_grades와 _fetch_ancestor_ids 두 번 왕복).
    space_key가 없으면 resolve_representative의 source tier 계산이 confluence_map
    LOOKIN/TechRepo를 tier 3로 잘못 떨어뜨려, G01 resolver와 다른 대표를 고를
    수 있다 — 반드시 같이 가져온다. ancestor_ids는 더 이상 여기서 가져오지
    않는다(G04 잔여 해소) — explore()가 돌려주는 Document.ancestor_ids
    (merge_document가 Neo4j 노드에 직접 써둔 값)를 그대로 쓴다. 이 함수는
    NetApp류 수천 건 후보에도 호출되므로, metadata_ 전체(JSONB)가 아니라
    필요한 한 키만 서버 쪽에서 뽑아 전송량을 줄인다."""
    if not document_ids:
        return {}
    unique_ids = sorted(set(document_ids))
    with session_scope() as session:
        rows = session.execute(
            select(*_ENRICHMENT_COLUMNS).where(Document.id.in_(unique_ids))
        ).all()
    out: dict[str, dict] = {}
    for doc_id, grade, status, source_type, external_id, space_key in rows:
        out[doc_id] = {
            "evidence_grade": grade, "status": status, "source_type": source_type,
            "external_id": external_id, "space_key": space_key,
        }
    return out


def _fetch_ancestor_ids(document_ids: list[str]) -> dict[str, list[str]]:
    """document 앵커 자신의 ancestor_ids만 필요한 소규모 호출용(보통 같은
    external_id의 수집본 몇 건) — anchor 자체가 exclude_subtree_ids에 걸리는지
    판정하려면 explore()를 돌리기도 전에 PG에서 직접 봐야 한다. 큰 후보
    목록(수천 건)에는 이 컬럼을 안 실어 보내는 _fetch_document_enrichment와
    역할을 분리한다."""
    if not document_ids:
        return {}
    unique_ids = sorted(set(document_ids))
    with session_scope() as session:
        rows = session.execute(
            select(Document.id, Document.metadata_["ancestor_ids"]).where(Document.id.in_(unique_ids))
        ).all()
    return {doc_id: list(ancestor_ids or []) for doc_id, ancestor_ids in rows}


def _explore_freshness_summary() -> dict[str, Any]:
    """F04(2026-10-13 최종 핸드오프) §7/8 — "앵커·근거 문서의 추출 상태와
    전체 배치 상태를 구분하라. MAX(synced_at)만으로 freshness를 판정하지
    말라"는 요구. as_of(호출자가 이미 MAX(synced_at)로 따로 계산)와 별개로,
    마지막으로 '완료된' 전체 배치가 언제·어떤 상태로 끝났는지, 그리고 지금
    새 전체 배치가 도는 중인지를 함께 보여준다. kb_graph_explore(MCP)가
    매 호출마다 읽는 가벼운 요약이라 pg_locks 조회(비용이 더 드는 실시간
    liveness 진단, GET /v1/graph/status가 제공)는 하지 않는다 — status
    컬럼만 본다."""
    with session_scope() as session:
        latest_full_completed = session.scalar(
            select(GraphRun)
            .where(GraphRun.scope == "full", GraphRun.status.in_(["completed", "completed_warn"]))
            .order_by(GraphRun.seq.desc())
        )
        latest_run = session.scalar(select(GraphRun).order_by(GraphRun.seq.desc()))
        full_run_in_progress = (
            latest_run is not None and latest_run.scope == "full" and latest_run.status == "running"
        )
        return {
            "latest_full_completed_run_id": latest_full_completed.run_id if latest_full_completed else None,
            "latest_full_completed_generation": (
                latest_full_completed.generation if latest_full_completed else None
            ),
            "latest_full_completed_at": (
                latest_full_completed.completed_at.isoformat()
                if latest_full_completed and latest_full_completed.completed_at else None
            ),
            "latest_full_completed_status": latest_full_completed.status if latest_full_completed else None,
            "full_run_in_progress": full_run_in_progress,
        }


@router.post("/graph/explore")
def graph_explore(body: GraphExploreBody) -> dict[str, Any]:
    as_of = get_latest_synced_at()
    as_of_str = as_of.date().isoformat() if as_of else None
    freshness = _explore_freshness_summary()
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
    exclude_external_ids = set(body.exclude_page_ids)
    exclude_source_types = set(body.exclude_source_types)
    exclude_subtree_ids = set(body.exclude_subtree_ids)
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
                freshness=freshness,
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
            except Exception as exc:  # noqa: BLE001
                if not _is_backend_error(exc):
                    raise
                # G08: Neo4j 장애를 503이 아니라 기계가 구분 가능한 status로
                # 돌려준다(연결 자체가 아예 안 되는 경우도 "찾을 수
                # 없음"(404)과는 다른 신호가 필요하다). 코드 버그는 위에서
                # re-raise되어 500으로 보여 숨지 않는다.
                logger.exception(
                    "graph explore backend error (anchor_type=component, anchor_value=%s)", canonical,
                )
                return shape_explore_result(
                    {"found": False, "documents": [], "components": [], "failure_buckets": [],
                     "excluded_hub_components": []},
                    as_of=as_of_str,
                    anchor={
                        "type": "component", "resolved_id": None, "resolved_name": canonical,
                        "backend_error_reason": str(exc),
                    },
                    status="backend_error",
                    freshness=freshness,
                )
            if ci_match:
                canonical = ci_match
        anchors = [("Component", "canonical_name", canonical)]
        resolved_name = canonical
    elif body.anchor_type == "document":
        resolved_document_id, document_ids = _resolve_document_anchor_ids(body.anchor_value)
        if exclude_external_ids or exclude_subtree_ids or exclude_source_types:
            # R01.3/R01.4(2026-10-13 2차 핸드오프) — page_id/subtree 제외는
            # "같은 Confluence page의 모든 수집본"에 적용되는 페이지 단위
            # 판정이라 하나라도 걸리면 전체를 filtered로 답한다. source_type
            # 제외는 그 수집본 하나만 fan-out에서 빼고, 다른 허용된 수집본이
            # 남아 있으면 그걸로 계속 탐색한다 — 과거엔 수집본 하나만
            # 걸려도 전체를 filtered로 끝내 "confluence_docs만 빼고 같은
            # 페이지의 confluence_map 사본으로는 보고 싶다"는 요청을 못
            # 받았다(실제 재현: confluence_docs:1000706325 앵커 +
            # exclude_source_types=confluence_docs → confluence_map:1000706325
            # 사본이 있어도 무조건 filtered였음).
            anchor_enrichment = _fetch_document_enrichment(document_ids)
            anchor_ancestor_ids = _fetch_ancestor_ids(document_ids)
            page_level_excluded = any(
                (e.get("external_id") in exclude_external_ids)
                or (e.get("external_id") in exclude_subtree_ids)
                or bool(set(anchor_ancestor_ids.get(doc_id) or []) & exclude_subtree_ids)
                for doc_id, e in anchor_enrichment.items()
            )
            if page_level_excluded:
                return shape_explore_result(
                    {"found": True, "documents": [], "components": [], "failure_buckets": [],
                     "excluded_hub_components": []},
                    as_of=as_of_str,
                    anchor={
                        "type": "document", "resolved_id": resolved_document_id, "resolved_name": None,
                        "filter_reason": "page_or_subtree_excluded",
                        "traversal_start_ids": [], "representative_excluded": True,
                    },
                    status="filtered",
                    freshness=freshness,
                )
            document_ids = [
                doc_id for doc_id in document_ids
                if anchor_enrichment.get(doc_id, {}).get("source_type") not in exclude_source_types
            ]
            if not document_ids:
                return shape_explore_result(
                    {"found": True, "documents": [], "components": [], "failure_buckets": [],
                     "excluded_hub_components": []},
                    as_of=as_of_str,
                    anchor={
                        "type": "document", "resolved_id": resolved_document_id, "resolved_name": None,
                        "filter_reason": "all_copies_source_excluded",
                        "traversal_start_ids": [], "representative_excluded": True,
                    },
                    status="filtered",
                    freshness=freshness,
                )
        anchors = [("Document", "id", doc_id) for doc_id in document_ids]
    else:  # failure_bucket
        anchors = [("FailureBucket", "id", body.anchor_value)]

    merged: dict[str, Any] = {
        "found": False, "documents": [], "components": [], "failure_buckets": [],
        "excluded_hub_components": [],
    }
    backend_error = False
    backend_error_reason: str | None = None
    # F02(2026-10-13 최종 핸드오프) — anchor_type="failure_bucket"일 때 조회한
    # bucket 자신의 evidence_reasons(및 bucket_name)를 담는다. 이웃이 0개라도
    # (status=isolated) 채워져야 한다 — 과거엔 이 값이 어디에도 안 실렸다
    # (실제 재현: comparison/raw/bucket_3.json).
    anchor_failure_bucket: dict[str, Any] | None = None
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
            # G04 — 중간 경로 bridge 차단(exclude_page_ids/exclude_source_types/
            # exclude_subtree_ids 전부 Cypher 안에서 거름 — Document.ancestor_ids
            # 를 merge_document가 이제 Neo4j 노드에도 올려두므로 subtree도
            # 가능해졌다, G04 잔여 해소).
            raw = client.explore(
                label, key, value, max_hops=2,
                exclude_external_ids=exclude_external_ids, exclude_source_types=exclude_source_types,
                exclude_subtree_ids=exclude_subtree_ids,
            )
            if label == "FailureBucket" and raw.get("anchor_node"):
                anchor_failure_bucket = raw["anchor_node"]
            if not raw.get("found"):
                continue
            merged["found"] = True
            merged["documents"].extend(raw["documents"])
            merged["components"].extend(raw["components"])
            merged["failure_buckets"].extend(raw["failure_buckets"])
            merged["excluded_hub_components"].extend(raw["excluded_hub_components"])
    except Exception as exc:  # noqa: BLE001
        if not _is_backend_error(exc):
            raise
        # G08: Neo4j 장애가 기존 검색/API에 안 퍼지도록 격리하되, 503 대신
        # 기계가 구분 가능한 status로 돌려준다(MCP는 응답 body의 status 필드를
        # 본다 — HTTP 에러는 "오류: ..."로만 뭉개져 보임). 코드 버그는 위에서
        # re-raise되어 500으로 남아 "backend_error"로 숨지 않는다.
        logger.exception(
            "graph explore backend error (anchor_type=%s anchor_value=%s)",
            body.anchor_type, body.anchor_value,
        )
        backend_error = True
        backend_error_reason = str(exc)

    if backend_error:
        anchor_info_err: dict[str, Any] = {
            "type": body.anchor_type,
            "resolved_id": resolved_document_id if body.anchor_type == "document" else (
                anchors[0][2] if anchors and body.anchor_type != "symptom_text" else None
            ),
            "resolved_name": resolved_name,
            "backend_error_reason": backend_error_reason,
        }
        if body.anchor_type == "symptom_text":
            anchor_info_err["matched_components"] = [a[2] for a in anchors]
        return shape_explore_result(
            {"found": False, "documents": [], "components": [], "failure_buckets": [],
             "excluded_hub_components": []},
            as_of=as_of_str, anchor=anchor_info_err, status="backend_error",
            freshness=freshness,
        )

    if not merged["found"] and body.anchor_type != "symptom_text":
        raise HTTPException(status_code=404, detail="anchor_value를 그래프에서 찾을 수 없습니다")

    if body.anchor_type == "document":
        # G01 fan-out 부작용: 같은 external_id의 다른 수집본들은 본문/컴포넌트가
        # 같아 서로의 2hop 이웃으로 잡힌다(둘 다 같은 Component를 거쳐 도달) —
        # 앵커 자신의 복제본들은 "연관 문서"가 아니라 앵커 자신이므로 제외한다.
        fan_out_ids = {a[2] for a in anchors}
        merged["documents"] = [d for d in merged["documents"] if d["id"] not in fan_out_ids]

    # PG 왕복 1회로 통합 — grade/상태/동일 페이지 판별에 쓰는 source_type·
    # external_id를 전부 가져온다(G08). G04 필터와 collapse가 둘 다 이 하나의
    # 결과를 쓴다. ancestor_ids는 더 이상 여기서 PG로 가져오지 않는다 — Neo4j
    # 가 explore() 결과에 Document.ancestor_ids를 이미 포함해 돌려주므로
    # (merge_document가 써둠, G04 잔여 해소), 후보가 수천 건이어도 이 PG
    # 왕복은 ancestor_ids 컬럼을 안 실어 전송량이 그만큼 준다.
    enrichment = _fetch_document_enrichment([d["id"] for d in merged["documents"]])

    # G04 — 제외 필터를 dedup/정렬/cap 이전에 적용
    if body.exclude_page_ids or body.exclude_subtree_ids or body.exclude_source_types:
        ancestor_ids_by_doc_id = {d["id"]: d.get("ancestor_ids") or [] for d in merged["documents"]}
        merged["documents"] = filter_excluded_documents(
            merged["documents"],
            exclude_page_ids=set(body.exclude_page_ids),
            exclude_subtree_ids=exclude_subtree_ids,
            exclude_source_types=set(body.exclude_source_types),
            ancestor_ids_by_doc_id=ancestor_ids_by_doc_id,
        )

    # 복수 앵커(symptom_text가 여러 component에 매칭된 경우) 결과 병합 시 중복 제거
    merged["documents"] = dedup_by_key(merged["documents"], "id")
    merged["components"] = dedup_by_key(merged["components"], "canonical_name")
    merged["failure_buckets"] = dedup_by_key(merged["failure_buckets"], "id")
    merged["excluded_hub_components"] = sorted(set(merged["excluded_hub_components"]))

    # 동일 Confluence 페이지의 다른 소스 수집본을 하나의 논리 후보로 묶는다
    # (G08) — dedup 이후, 정렬/cap 이전. *_total은 이 묶인 뒤의 수를 센다.
    merged["documents"] = collapse_duplicate_copies(merged["documents"], enrichment_by_id=enrichment)
    merged["documents"] = enrich_with_document_metadata(merged["documents"], enrichment)

    if body.anchor_type == "symptom_text":
        anchor_info: dict[str, Any] = {
            "type": "symptom_text", "resolved_id": None, "resolved_name": None,
            "matched_components": [a[2] for a in anchors],
        }
    elif body.anchor_type == "document":
        # F05(2026-10-13 최종 핸드오프) §2.5.4/§2.5.6 — resolved_id는 과거
        # exclude_source_types 적용 "이전"의 전체 후보에서 고른 대표를 그대로
        # 돌려줬다(실제 재현: confluence_docs가 대표인데 exclude_source_types=
        # confluence_docs를 주면, 실제 탐색은 confluence_map 사본으로만 도는데
        # 응답은 여전히 제외된 confluence_docs를 resolved_id로 표시). 대표
        # 식별자(논리 문서)와 "실제로 이 탐색이 시작한 사본"을 분리해 둘 다
        # 노출한다 — representative_excluded로 둘이 갈렸는지 기계가 바로
        # 판별 가능하게 한다.
        anchor_info = {
            "type": body.anchor_type, "resolved_id": resolved_document_id, "resolved_name": resolved_name,
            "traversal_start_ids": sorted(document_ids),
            "representative_excluded": resolved_document_id not in document_ids,
        }
    else:
        anchor_info = {
            "type": body.anchor_type, "resolved_id": anchors[0][2], "resolved_name": resolved_name,
        }
        if body.anchor_type == "failure_bucket":
            # F02 — anchor 자신의 사유. 못 구했으면(아직 이 라운드 코드로
            # 재백필 안 된 버킷 등) None을 그대로 노출한다 — "unknown과
            # 부족한 필드를 반환하라"는 요구대로, isolated만 보고 원인을
            # 추정하지 않는다.
            anchor_info["resolved_name"] = (
                anchor_failure_bucket.get("bucket_name") if anchor_failure_bucket else None
            )
            anchor_info["evidence_reasons"] = (
                anchor_failure_bucket.get("evidence_reasons") if anchor_failure_bucket else None
            )

    shaped = shape_explore_result(
        merged, as_of=as_of_str, anchor=anchor_info,
        offsets={
            "documents": body.documents_offset,
            "components": body.components_offset,
            "failure_buckets": body.failure_buckets_offset,
        },
        freshness=freshness,
    )
    # attach_document_access(원문 접근 경로 — kb_search/kb_get_document와 같은
    # 빌더 재사용, §2.6 "존재하지 않는 path를 합성하지 않는다")는 실제로 보여줄
    # 페이지(최대 50건)에만 적용한다 — NetApp처럼 전체 후보가 수천 건이면 그
    # 전부에 대해 만들 필요가 없다.
    shaped["documents"] = [attach_document_access(dict(d)) for d in shaped["documents"]]
    return shaped


_STALE_HEARTBEAT_SECONDS = 1800  # 30분 — sync_cli가 문서 루프 안에서 약
# 60초마다 heartbeat를 찍으므로, 정상 실행이라면 이 값을 절대 넘지 않는다.


def _advisory_lock_key_parts(key: int) -> tuple[int, int]:
    """F04(2026-10-13 최종 핸드오프) §3/4 — pg_locks.classid/objid는 signed
    int4로, bigint 키의 상위/하위 32비트를 각각 담는다(운영 Postgres에
    실제로 락을 쥔 채 pg_locks를 조회해 실측 확인됨). 이 값으로 "락 획득을
    시도하지 않고" 지금 그 pid가 여전히 락을 쥐고 있는지만 읽는다 — 시도
    자체를 하면 막 시작하는 sync_cli와 경합할 수 있어서다."""
    high = (key >> 32) & 0xFFFFFFFF
    low = key & 0xFFFFFFFF

    def _to_signed32(v: int) -> int:
        return v - 0x100000000 if v >= 0x80000000 else v

    return _to_signed32(high), _to_signed32(low)


def _is_lock_currently_held(session, pid: int) -> bool:
    from app.graph.sync_cli import _ADVISORY_LOCK_KEY

    classid, objid = _advisory_lock_key_parts(_ADVISORY_LOCK_KEY)
    held = session.execute(
        text(
            "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
            "AND classid = :classid AND objid = :objid AND objsubid = 1 "
            "AND granted AND pid = :pid"
        ),
        {"classid": classid, "objid": objid, "pid": pid},
    ).first()
    return held is not None


def _run_liveness(session, row: GraphRun) -> dict[str, Any] | None:
    """F04 §3/4/6 — status='running'인 행에만 적용된다. "판별 근거와
    unknown도 노출하라"는 요구대로, 최종 결론(state)뿐 아니라 그 근거
    (lock_holder_pid/lock_currently_held/heartbeat_at)도 그대로 돌려준다."""
    if row.status != "running":
        return None
    if row.lock_holder_pid is None:
        return {
            "state": "unknown", "lock_holder_pid": None, "lock_currently_held": None,
            "heartbeat_at": None, "seconds_since_heartbeat": None,
        }
    lock_held = _is_lock_currently_held(session, row.lock_holder_pid)
    seconds_since_heartbeat = (
        (datetime.now(timezone.utc) - row.heartbeat_at).total_seconds()
        if row.heartbeat_at else None
    )
    if not lock_held:
        state = "likely_dead"
    elif seconds_since_heartbeat is None:
        state = "unknown"
    elif seconds_since_heartbeat > _STALE_HEARTBEAT_SECONDS:
        state = "stalled"
    else:
        state = "running"
    return {
        "state": state, "lock_holder_pid": row.lock_holder_pid, "lock_currently_held": lock_held,
        "heartbeat_at": row.heartbeat_at.isoformat() if row.heartbeat_at else None,
        "seconds_since_heartbeat": seconds_since_heartbeat,
    }


@router.get("/graph/status")
def graph_status() -> dict[str, Any]:
    """G05 §잔여 — graph_sync_state.max(synced_at)만으로는 "마지막 완료 배치가
    언제/어떻게 끝났는지"를 알 수 없다(부분 실패/진행 중과 구분 불가). 가장
    최근 graph_runs 행과, 이 Neo4j가 실제로 들고 있는 generation 마커를 함께
    보여줘 "PG의 마지막 run 기록"과 "Neo4j가 실제로 그 generation인지"를
    사람이 한 번에 비교할 수 있게 한다(마커가 run.generation과 다르면 다른
    Neo4j로 바뀌었거나 그 뒤로 새 배치가 전혀 안 돈 상태)."""
    as_of = get_latest_synced_at()
    run_info: dict[str, Any] | None = None
    def _run_dict(row: GraphRun, session) -> dict[str, Any]:
        return {
            "run_id": row.run_id,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            "status": row.status,
            "scope": row.scope,
            "extractor_version": row.extractor_version,
            "generation": row.generation,
            "lexicon_version": row.lexicon_version,
            "documents_synced": row.documents_synced,
            "documents_skipped": row.documents_skipped,
            "documents_failed": row.documents_failed,
            "documents_state_write_failed": row.documents_state_write_failed,
            "buckets_synced": row.buckets_synced,
            "buckets_failed": row.buckets_failed,
            "unresolved_references": row.unresolved_references,
            "hub_recompute_ok": row.hub_recompute_ok,
            # F04(2026-10-13 최종 핸드오프) §3/4/6 — status='running'일 때만
            # 채워진다. "unknown도 노출하라"는 요구대로, 판단 불가능한
            # 경우(마이그레이션 전 run 등)는 liveness.state="unknown"으로
            # 명시한다(그냥 생략하지 않음).
            "liveness": _run_liveness(session, row),
        }

    full_completed_info: dict[str, Any] | None = None
    with session_scope() as session:
        # F04(2026-10-13 최종 핸드오프) — started_at이 아니라 seq로 정렬한다.
        # Postgres now()는 한 트랜잭션 안에서 항상 같은 값이라, 같은
        # session_scope()에서 두 행이 추가되면 started_at이 같아져 "최신"을
        # 결정할 수 없었다(실제 재현됨). seq(BIGSERIAL)는 삽입마다 항상
        # 다른 값이라 안전하다.
        latest_run = session.scalar(select(GraphRun).order_by(GraphRun.seq.desc()))
        # dict는 session_scope() 블록 "안에서" 만들어야 한다 — 밖에서 ORM
        # 속성에 접근하면 세션이 이미 닫혀 DetachedInstanceError가 난다(실제
        # 테스트로 재현됨).
        if latest_run is not None:
            run_info = _run_dict(latest_run, session)
        # R02(2026-10-13 2차 핸드오프) §5/6 — latest_run은 --source-ids
        # 디버그 실행이나 하드킬로 영원히 "running"에 남은 행도 그대로
        # 돌려준다. "마지막으로 완료된 전체 그래프 스냅샷"이 필요한 호출자는
        # 이 필드를 따로 봐야 한다(scope=full AND status가 completed류).
        latest_full_completed = session.scalar(
            select(GraphRun)
            .where(GraphRun.scope == "full", GraphRun.status.in_(["completed", "completed_warn"]))
            .order_by(GraphRun.seq.desc())
        )
        if latest_full_completed is not None:
            full_completed_info = _run_dict(latest_full_completed, session)

    try:
        _client_for_status = _build_client()
        neo4j_generation = _client_for_status.read_generation_marker()
        neo4j_store_identity = _client_for_status.read_store_identity()
    except Exception:  # noqa: BLE001 — 상태 조회 자체가 그래프 저장소 장애로
        # 500이 되면 안 된다(§4.4와 같은 이유) — 모르면 null로 둔다. 다만
        # 조용히 삼키지 않고 로그는 남긴다(advisor 지적 — 원인 추적 가능해야).
        logger.exception("graph status: failed to read neo4j generation marker")
        neo4j_generation = None
        neo4j_store_identity = None

    # F04(2026-10-13 최종 핸드오프) §6 — "혼합 결과를 완료 snapshot으로
    # 표현하지 말라"는 요구. mixed = 지금 full run이 실제로 진행 중이거나
    # (liveness.state in running/stalled — likely_dead/unknown은 "진행
    # 중"이라는 확신이 없으므로 mixed로 안 본다), 마지막 완료가
    # completed_warn(일부 실패 섞인 완료)인 경우. 전부 아니면 unknown —
    # "완료된 full run 자체가 아직 없다"는 뜻이라 consistent라고 할 수 없다.
    run_in_progress = (
        run_info is not None and run_info["scope"] == "full"
        and run_info.get("liveness") is not None
        and run_info["liveness"]["state"] in ("running", "stalled")
    )
    if run_in_progress or (full_completed_info is not None and full_completed_info["status"] == "completed_warn"):
        read_state = "mixed"
    elif full_completed_info is not None and full_completed_info["status"] == "completed":
        read_state = "consistent"
    else:
        read_state = "unknown"

    return {
        "as_of": as_of.date().isoformat() if as_of else None,
        "latest_run": run_info,
        "latest_full_completed_run": full_completed_info,
        "neo4j_generation": neo4j_generation,
        "generation_matches": (
            full_completed_info is not None
            and neo4j_generation is not None
            and full_completed_info["generation"] == neo4j_generation
        ),
        "read_state": read_state,
        # F04 §1/9 — store identity(Neo4j 자신의 db.info())와 write
        # generation(우리가 쓴 마커)은 서로 다른 의미이므로 분리해서 노출한다.
        "neo4j_store_identity": neo4j_store_identity,
        "api_build_sha": os.environ.get("API_BUILD_SHA", "unknown"),
        "contract_version": GRAPH_STATUS_CONTRACT_VERSION,
    }
