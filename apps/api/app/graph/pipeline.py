"""Orchestration: priority groups (spec §0.1) + per-document idempotent sync
(spec §4.1/§4.4). This is the only module that touches both Postgres and
Neo4j — every extractor it calls stays a pure function."""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import select

from app.db.models import (
    Checkitem,
    Document,
    DocumentEntity,
    Entity,
    FailureBucket,
    IssueFrame,
)
from app.db.session import session_scope
from app.graph import sync_state
from app.graph.extract import (
    extract_business_entities,
    extract_hierarchy,
    extract_lexicon_components,
    extract_references,
    extract_structured_components,
)
from app.graph.failure_bucket import extract_evidence, extract_similar_to
from app.graph.hashing import compute_graph_hash
from app.graph.neo4j_client import Neo4jClient
from app.lexicon.seed import load_lexicon_map

logger = logging.getLogger("citec.graph")

EXTRACTOR_VERSION = "2026-10-08.1"

# 스펙 §0.1 — CI-TEC 1차 산출물이 confluence_map의 유일한 두 "승격" 공간이다.
_TIER_1_SOURCE_TYPES = {"tech_repo", "confluence_docs", "checkitem"}
_TIER_1_CONFLUENCE_MAP_SPACES = {"LOOKIN", "TechRepo"}
_TIER_2_SOURCE_TYPES = {"incident_reports"}


def compute_priority_tier(source_type: str, *, space_key: str | None) -> Literal[1, 2, 3]:
    if source_type in _TIER_1_SOURCE_TYPES:
        return 1
    if source_type == "confluence_map":
        return 1 if space_key in _TIER_1_CONFLUENCE_MAP_SPACES else 3
    if source_type in _TIER_2_SOURCE_TYPES:
        return 2
    return 3


def build_neo4j_client(*, uri: str | None = None, user: str | None = None, password: str | None = None) -> Neo4jClient:
    client = Neo4jClient(uri=uri, user=user, password=password)
    # 제약조건은 데이터가 머지되기 "전에" 존재해야 한다(Task 12 리뷰 노트) —
    # CREATE CONSTRAINT는 이미 중복 데이터가 있으면 실패할 수 있으므로, 이
    # 함수를 거쳐 만들어지는 모든 실제 호출자가 자동으로 먼저 제약조건을
    # 보장받게 한다.
    client.ensure_constraints()
    return client


def build_external_id_index() -> dict[str, list[dict]]:
    """external_id(소문자 정규화) -> [{"document_id","evidence_grade"}, ...].
    extract_evidence가 그대로 쓰고, extract_references는 app.graph.failure_bucket.
    best_candidate()로 단일 document_id만 뽑아 쓴다(§4.2 — evidence_grade 높은
    쪽 우선). 세션당 한 번만 만들어 모든 문서/버킷이 공유한다."""
    with session_scope() as session:
        rows = session.execute(
            select(Document.id, Document.external_id, Document.evidence_grade)
        ).all()
    index: dict[str, list[dict]] = {}
    for doc_id, external_id, grade in rows:
        key = (external_id or "").strip().lower()
        if not key:
            continue
        index.setdefault(key, []).append({"document_id": doc_id, "evidence_grade": grade})
    return index


def _simple_reference_index(rich_index: dict[str, list[dict]]) -> dict[str, str]:
    """extract_references가 쓰는 "단일 best document_id" 매핑으로 축약."""
    from app.graph.failure_bucket import best_candidate

    out: dict[str, str] = {}
    for key, candidates in rich_index.items():
        best = best_candidate(candidates)
        if best:
            out[key] = best
    return out


def sync_document(
    document_id: str, *, client: Neo4jClient, external_id_index: dict[str, list[dict]] | None = None
) -> Literal["synced", "skipped", "failed"]:
    """문서 1건 동기화 — 문서 단위 격리(§4.4): 실패해도 예외를 밖으로 던지지 않고
    "failed"를 반환해, pipeline을 호출하는 쪽(향후 CLI)이 배치를 멈추지 않게 한다."""
    try:
        with session_scope() as session:
            doc = session.get(Document, document_id)
            if doc is None:
                logger.warning("graph sync: document_id=%s not found, skipping", document_id)
                return "failed"
            issue_frame = session.scalar(
                select(IssueFrame).where(IssueFrame.document_id == document_id)
            )
            checkitem = session.scalar(
                select(Checkitem).where(Checkitem.document_id == document_id)
            )
            doc_entities_rows = list(
                session.execute(
                    select(DocumentEntity.entity_id, Entity.type)
                    .join(Entity, Entity.id == DocumentEntity.entity_id)
                    .where(DocumentEntity.document_id == document_id)
                ).all()
            )
            metadata = dict(doc.metadata_ or {})
            space_key = metadata.get("space_key")
            doc_dict = {
                "id": doc.id,
                "source_type": doc.source_type,
                "external_id": doc.external_id,
                "title": doc.title,
                "body_md": doc.body_md,
                "source_uri": doc.source_uri,
                "environment": doc.environment,
                "space_key": space_key,
                "priority_tier": compute_priority_tier(doc.source_type, space_key=space_key),
                "metadata": metadata,
            }
            issue_frame_dict = (
                {"components": list(issue_frame.components or [])} if issue_frame else None
            )
            checkitem_dict = {"area": checkitem.area} if checkitem else None
            document_entities = [
                {"entity_id": row.entity_id, "entity_type": row.type} for row in doc_entities_rows
            ]

            extra_hash_input = {
                "ancestor_ids": metadata.get("ancestor_ids"),
                "components": issue_frame_dict.get("components") if issue_frame_dict else None,
                "area": checkitem_dict.get("area") if checkitem_dict else None,
                "document_entities": document_entities,
            }
            # 알려진 한계(의도적으로 범위 밖): lexicon_map/external_id_index 내용이
            # 바뀌어도 이 해시에는 안 들어간다 — 문서 로컬 상태가 아니라 실행 전체가
            # 공유하는 상태라, 넣으면 사전/코퍼스가 바뀔 때마다 전체 문서가 재동기화돼
            # 캐시의 의미가 없어진다. 이 두 extractor(lexicon_components, references)의
            # 재추출이 필요하면 별도의 전체 재실행(--from-scratch류)으로 처리한다.
            input_hash = compute_graph_hash(doc.content_hash, extra_hash_input)

        state = sync_state.get_state(document_id)
        if (
            state
            and state["input_hash"] == input_hash
            and state["graph_extractor_version"] == EXTRACTOR_VERSION
        ):
            return "skipped"

        lexicon_map = load_lexicon_map()
        reference_index = _simple_reference_index(external_id_index or {})
        edges = (
            extract_hierarchy(doc_dict)
            + extract_structured_components(doc_dict, issue_frame=issue_frame_dict, checkitem=checkitem_dict)
            + extract_business_entities(doc_dict, document_entities=document_entities)
            + extract_lexicon_components(doc_dict, lexicon_map=lexicon_map)
            + extract_references(doc_dict, external_id_index=reference_index)
        )
        # 알려진 한계(의도적으로 이 태스크 범위 밖): 문서 내용이 바뀌어 재동기화될 때
        # 이전 추출에서 나왔지만 새 추출에서는 더 안 나오는 엣지(예: 본문에서 사라진
        # 컴포넌트 언급)는 지워지지 않는다 — merge_document는 MERGE만 하고 DELETE는
        # 안 한다. 그래프가 단조증가만 하는 문제 — 나중 태스크에서
        # Neo4jClient._merge_document_tx가 재병합 전에 해당 문서의 기존
        # EXTRACTED/INFERRED 엣지를 지우도록 고쳐야 한다.
        client.merge_document(doc_dict, edges)
        try:
            sync_state.mark_synced(document_id, input_hash=input_hash, extractor_version=EXTRACTOR_VERSION)
        except Exception:  # noqa: BLE001 — 그래프엔 이미 반영됐는데 상태 기록만 실패한 경우도
            # 배치를 죽이면 안 된다(§4.4와 같은 이유). 다음 실행에서 해시 불일치로 재시도된다.
            logger.exception("failed to record graph_sync_state for document_id=%s (merge itself succeeded)", document_id)
        return "synced"
    except Exception as exc:  # noqa: BLE001 — §4.4: 문서 단위 격리, 배치 안 죽인다
        logger.exception("graph sync failed for document_id=%s", document_id)
        try:
            sync_state.mark_failed(document_id, error=str(exc))
        except Exception:  # noqa: BLE001 — 실패 기록 자체가 실패해도(예: 문서가 동시에
            # 삭제됨) sync_document는 여전히 "failed"를 반환해야 한다 — 상태 기록 실패가
            # 배치 전체를 죽이게 하지 않는다.
            logger.exception("failed to record mark_failed for document_id=%s", document_id)
        return "failed"


def sync_failure_bucket(
    bucket_id: str,
    *,
    client: Neo4jClient,
    all_buckets: list[dict],
    external_id_index: dict[str, list[dict]] | None = None,
) -> Literal["synced", "failed"]:
    try:
        with session_scope() as session:
            bucket = session.get(FailureBucket, bucket_id)
            if bucket is None:
                return "failed"
            bucket_dict = {
                "id": bucket.id,
                "bucket_name": bucket.bucket_name,
                "fb_domain": bucket.fb_domain,
                "protocol": bucket.protocol,
                "environment": bucket.environment,
                "evidence_ref": bucket.evidence_ref,
                "symptom": bucket.symptom,
                "discriminating_signals": list(bucket.discriminating_signals or []),
            }
        edges = extract_evidence(
            bucket_dict, external_id_index=external_id_index or {}
        ) + extract_similar_to(bucket_dict, other_buckets=all_buckets)
        client.merge_failure_bucket(bucket_dict, edges)
        return "synced"
    except Exception:  # noqa: BLE001
        logger.exception("graph sync failed for failure_bucket_id=%s", bucket_id)
        return "failed"
