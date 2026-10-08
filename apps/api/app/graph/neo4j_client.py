"""Thin Neo4j wrapper — only the MERGE operations app.graph.pipeline needs."""

from __future__ import annotations

from typing import Iterable, Optional

from neo4j import GraphDatabase

from app.graph.extract import Edge
from app.settings import get_settings

_CONSTRAINTS = [
    ("Document", "id"),
    ("FailureBucket", "id"),
    ("Component", "canonical_name"),
    ("BusinessEntity", "id"),
]

_HUB_DEGREE_THRESHOLD = 5000


def _ensure_constraints_tx(tx) -> None:
    for label, key in _CONSTRAINTS:
        tx.run(
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
        )


def _recompute_hub_flags_tx(tx) -> None:
    tx.run(
        """
        MATCH (c:Component)
        OPTIONAL MATCH (c)<-[r:HAS_COMPONENT]-()
        WITH c, count(r) AS degree
        SET c.is_hub = (degree > $threshold)
        """,
        threshold=_HUB_DEGREE_THRESHOLD,
    )


_DOCUMENT_SET_CLAUSE = """
    SET d.source_type = $source_type,
        d.external_id = $external_id,
        d.title = $title,
        d.source_uri = $source_uri,
        d.environment = $environment,
        d.space_key = $space_key,
        d.priority_tier = $priority_tier
"""


class Neo4jClient:
    def __init__(
        self, uri: str | None = None, user: str | None = None, password: str | None = None
    ) -> None:
        settings = get_settings()
        self._driver = GraphDatabase.driver(
            uri or settings.neo4j_uri,
            auth=(user or settings.neo4j_username, password or settings.neo4j_password),
        )

    def close(self) -> None:
        self._driver.close()

    def ensure_constraints(self) -> None:
        """Idempotent — safe to call on every pipeline run, not just once.
        Without these, every MERGE in _merge_edges/_merge_document_tx/
        _merge_failure_bucket_tx is an unindexed full-label scan (review
        finding, Task 12) and MERGE isn't race-safe under concurrent writers."""
        with self._driver.session() as session:
            session.execute_write(_ensure_constraints_tx)

    def recompute_hub_flags(self) -> None:
        """백필 1회 실행이 끝난 뒤 호출 — 모든 Component의 HAS_COMPONENT 입력 degree를
        다시 집계해 is_hub를 갱신한다(스펙 §3.1). 하드코딩 목록이 아니라 매 실행마다
        실측으로 재계산되므로, 1단계 설계 §6의 13종 목록처럼 데이터가 바뀌면 틀려지는
        문제가 구조적으로 없다."""
        with self._driver.session() as session:
            session.execute_write(_recompute_hub_flags_tx)

    def explore(self, anchor_label: str, anchor_key: str, anchor_value: str, *, max_hops: int = 2) -> dict:
        """읽기 전용 2-hop 순회. anchor_label/anchor_key는 이 모듈의 고정 4-레이블 enum뿐이라
        f-string 삽입이 안전하다(merge_* 메서드와 동일한 전제).
        max_hops는 현재 항상 2로 고정이며 파라미터 값은 무시된다(향후 가변 깊이 지원을 위한 자리 — 1단계는 쓰지 않음)."""
        query = f"""
        MATCH (a:{anchor_label} {{{anchor_key}: $value}})
        OPTIONAL MATCH (a)-[r1]-(n1)
        WHERE n1 <> a
        WITH a, collect(DISTINCT {{node: n1, relation: type(r1), hops: 1}}) AS hop1
        OPTIONAL MATCH (a)-[]-(p)-[r2]-(n2)
        WHERE n2 <> a AND NOT coalesce(p.is_hub, false)
        WITH a, hop1, collect(DISTINCT {{node: n2, relation: type(r2), hops: 2}}) AS hop2
        RETURN a AS anchor, hop1 + hop2 AS neighbors
        """
        with self._driver.session() as session:
            record = session.run(query, value=anchor_value).single()
        if record is None or record["anchor"] is None:
            return {
                "found": False, "documents": [], "components": [], "failure_buckets": [],
                "excluded_hub_components": [], "truncated": False,  # truncated=False — 결과 cap/절단은 app.graph.explore(Task 4)의 순수 함수에서 처리, 여기선 항상 False
            }

        best_by_id: dict[str, dict] = {}
        for entry in record["neighbors"]:
            node = entry["node"]
            if node is None:
                continue
            elem_id = node.element_id
            hops = entry["hops"]
            if elem_id not in best_by_id or hops < best_by_id[elem_id]["hops"]:
                best_by_id[elem_id] = {"node": node, "relation": entry["relation"], "hops": hops}

        documents: list[dict] = []
        components: list[dict] = []
        failure_buckets: list[dict] = []
        excluded_hub_components: list[str] = []
        for entry in best_by_id.values():
            node = entry["node"]
            labels = set(node.labels)
            if "Document" in labels:
                documents.append({
                    "id": node["id"], "title": node.get("title"), "source_type": node.get("source_type"),
                    "relation": entry["relation"], "hops": entry["hops"],
                })
            elif "Component" in labels:
                if node.get("is_hub"):
                    excluded_hub_components.append(node["canonical_name"])
                    continue
                components.append({
                    "canonical_name": node["canonical_name"],
                    "relation": entry["relation"], "hops": entry["hops"],
                })
            elif "FailureBucket" in labels:
                failure_buckets.append({
                    "id": node["id"], "bucket_name": node.get("bucket_name"),
                    "relation": entry["relation"], "hops": entry["hops"],
                })

        return {
            "found": True,
            "documents": documents, "components": components, "failure_buckets": failure_buckets,
            "excluded_hub_components": sorted(set(excluded_hub_components)),
            "truncated": False,  # truncated=False — 결과 cap/절단은 app.graph.explore(Task 4)의 순수 함수에서 처리, 여기선 항상 False
        }

    def resolve_component_case_insensitive(self, value: str) -> Optional[str]:
        """lexicon에 없는 컴포넌트명이 대소문자만 다르게 들어왔을 때(스펙 §2 "대소문자
        무시" 요구) canonical_name을 찾아준다. explore()의 exact-match Cypher는 그대로
        두고, 라우터가 이 메서드로 먼저 정규화한 뒤 explore()를 부른다."""
        with self._driver.session() as session:
            record = session.run(
                "MATCH (c:Component) WHERE toLower(c.canonical_name) = toLower($value) "
                "RETURN c.canonical_name AS name ORDER BY (c.canonical_name = $value) DESC LIMIT 1",
                value=value,
            ).single()
        return record["name"] if record else None

    def merge_document(self, doc: dict, edges: Iterable[Edge]) -> None:
        with self._driver.session() as session:
            session.execute_write(_merge_document_tx, doc, list(edges))

    def merge_failure_bucket(self, bucket: dict, edges: Iterable[Edge]) -> None:
        with self._driver.session() as session:
            session.execute_write(_merge_failure_bucket_tx, bucket, list(edges))


def _merge_edges(tx, source_label: str, source_key: str, source_value: str, edges: list[Edge]) -> None:
    for e in edges:
        # target_label/rel_type은 이 저장소의 고정 enum("PARENT_OF" 등)뿐이라
        # f-string 삽입이 안전하다 — 외부 입력이 이 값으로 들어오는 경로는 없다.
        tx.run(f"MERGE (t:{e.target_label} {{{e.target_key}: $value}})", value=e.target_value)
        tx.run(
            f"""
            MATCH (s:{source_label} {{{source_key}: $source_value}})
            MATCH (t:{e.target_label} {{{e.target_key}: $target_value}})
            MERGE (s)-[r:{e.rel_type}]->(t)
            SET r.tag = $tag
            """,
            source_value=source_value,
            target_value=e.target_value,
            tag=e.tag,
        )


def _merge_document_tx(tx, doc: dict, edges: list[Edge]) -> None:
    tx.run(f"MERGE (d:Document {{id: $id}}){_DOCUMENT_SET_CLAUSE}", **doc)
    _merge_edges(tx, "Document", "id", doc["id"], edges)


def _merge_failure_bucket_tx(tx, bucket: dict, edges: list[Edge]) -> None:
    tx.run(
        """
        MERGE (b:FailureBucket {id: $id})
        SET b.bucket_name = $bucket_name,
            b.fb_domain = $fb_domain,
            b.protocol = $protocol,
            b.environment = $environment,
            b.evidence_ref = $evidence_ref
        """,
        **bucket,
    )
    _merge_edges(tx, "FailureBucket", "id", bucket["id"], edges)
