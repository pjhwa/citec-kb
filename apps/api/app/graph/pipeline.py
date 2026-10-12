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

EXTRACTOR_VERSION = "2026-10-12.1"  # G01/G02: 계층 추출 로직이 바뀌어 올림 — hash가
# 같아도 이 버전 변경만으로 기존 graph_sync_state가 전부 재동기화 대상이 된다.

DEFAULT_GENERATION = "2026-10-12.g1"  # G05 — 환경변수 GRAPH_GENERATION으로 override

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


def build_page_identity_index() -> dict[str, list[dict]]:
    """external_id/raw page id(소문자 정규화) ->
    [{"document_id","evidence_grade","status","source_type","space_key"}, ...].
    build_external_id_index()보다 넓은 상위집합 — app.graph.identity의 결정적
    tie-break(active/grade/source tier/id)에 필요한 필드를 전부 담는다(G01).
    세션당 한 번만 만들어 모든 문서/버킷/계층 추출이 공유한다."""
    with session_scope() as session:
        rows = session.execute(
            select(
                Document.id, Document.external_id, Document.evidence_grade,
                Document.status, Document.source_type, Document.metadata_,
            )
        ).all()
    index: dict[str, list[dict]] = {}
    for doc_id, external_id, grade, status, source_type, metadata in rows:
        key = (external_id or "").strip().lower()
        if not key:
            continue
        index.setdefault(key, []).append({
            "document_id": doc_id, "evidence_grade": grade, "status": status,
            "source_type": source_type, "space_key": (metadata or {}).get("space_key"),
        })
    return index


def build_external_id_index() -> dict[str, list[dict]]:
    """build_page_identity_index()의 하위집합(document_id/evidence_grade만) —
    기존 호출자(extract_evidence)와의 하위 호환을 위해 유지."""
    return {
        key: [{"document_id": c["document_id"], "evidence_grade": c["evidence_grade"]} for c in candidates]
        for key, candidates in build_page_identity_index().items()
    }


def _simple_reference_index(rich_index: dict[str, list[dict]]) -> dict[str, str]:
    """extract_references가 쓰는 "단일 best document_id" 매핑으로 축약."""
    from app.graph.identity import best_candidate_id

    out: dict[str, str] = {}
    for key, candidates in rich_index.items():
        best = best_candidate_id(candidates)
        if best:
            out[key] = best
    return out


_reference_index_cache: dict[int, tuple[dict, dict[str, str]]] = {}


def _cached_simple_reference_index(rich_index: dict[str, list[dict]]) -> dict[str, str]:
    """_simple_reference_index()를 매 sync_document() 호출마다 처음부터 다시 돌리면
    115k 문서 규모에서 수 시간짜리 불필요한 오버헤드가 된다(리뷰에서 실측: 호출당
    ~0.15초 × 115k건 ≈ 4.7시간) — 같은 external_id_index 객체가 한 번의 CLI 실행
    내내 그대로 재사용된다는 걸 이용해 객체 identity로 캐시한다.

    id()만으로 캐시하면 원본 객체가 GC된 뒤 전혀 다른 객체가 같은 id()를 재사용할 때
    틀린 캐시 적중이 날 수 있다(리뷰에서 실제로 재현됨) — 그래서 원본 객체 자체도
    같이 저장해 `is`로 진짜 동일 객체인지 확인한 뒤에만 캐시를 신뢰한다. 다른
    객체라면(같은 id라도) 다시 계산하고 캐시를 갈아 끼운다."""
    key = id(rich_index)
    cached = _reference_index_cache.get(key)
    if cached is not None and cached[0] is rich_index:
        return cached[1]
    result = _simple_reference_index(rich_index)
    _reference_index_cache[key] = (rich_index, result)
    return result


def _dedup_edges_prefer_extracted(edges: list) -> list:
    """같은 (target_label,target_key,target_value,rel_type) 쌍에 EXTRACTED와
    INFERRED가 둘 다 나오면(예: 구조화 컴포넌트 + 본문 lexicon 매칭이 같은
    컴포넌트를 가리킴) EXTRACTED를 우선한다 — Neo4j에 쓰기 전에 끝내서,
    과거처럼 "마지막 SET r.tag가 이긴다"는 순서 의존을 없앤다(G03)."""
    best: dict[tuple, object] = {}
    for e in edges:
        key = (e.target_label, e.target_key, e.target_value, e.rel_type)
        if key not in best or (best[key].tag != "EXTRACTED" and e.tag == "EXTRACTED"):
            best[key] = e
    return list(best.values())


def sync_document(
    document_id: str, *, client: Neo4jClient,
    external_id_index: dict[str, list[dict]] | None = None,
    page_identity_index: dict[str, list[dict]] | None = None,
    generation: str = DEFAULT_GENERATION,
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
            and state.get("graph_generation") == generation
        ):
            return "skipped"

        lexicon_map = load_lexicon_map()
        reference_index = _cached_simple_reference_index(external_id_index or {})
        # extract_lexicon_components(INFERRED)와 extract_structured_components
        # (EXTRACTED)가 같은 (document, Component) 쌍에 둘 다 엣지를 낼 수 있다 —
        # _dedup_edges_prefer_extracted가 Neo4j에 쓰기 전에 EXTRACTED를 우선해
        # 하나만 남긴다(G03 — 과거 "마지막 SET r.tag가 이긴다" 순서 의존 제거).
        edges = _dedup_edges_prefer_extracted(
            extract_hierarchy(doc_dict, page_identity_index=page_identity_index or {})
            + extract_structured_components(doc_dict, issue_frame=issue_frame_dict, checkitem=checkitem_dict)
            + extract_business_entities(doc_dict, document_entities=document_entities)
            + extract_lexicon_components(doc_dict, lexicon_map=lexicon_map)
            + extract_references(doc_dict, external_id_index=reference_index)
        )
        # Neo4jClient._merge_document_tx가 머지 전에 이 문서가 소유하는 managed
        # edge를 전부 지우고 새로 쓰므로(G03), 본문에서 사라진 멘션도 여기서
        # 자동 제거된다 — 더 이상 "그래프가 단조증가만 하는" 문제가 아니다.
        client.merge_document(doc_dict, edges)
        try:
            sync_state.mark_synced(
                document_id, input_hash=input_hash, extractor_version=EXTRACTOR_VERSION,
                generation=generation,
            )
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


def recompute_hub_flags(client: Neo4jClient) -> None:
    """app.graph.sync_cli가 쓰는 얇은 위임 — 실제 구현은 Neo4jClient.recompute_hub_flags()
    (neo4j_client.py)에 있다. 파이프라인 함수들과 같은 임포트 경로를 유지하기 위해 남겨둔다."""
    client.recompute_hub_flags()
