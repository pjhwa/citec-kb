"""Thin Neo4j wrapper — only the MERGE operations app.graph.pipeline needs."""

from __future__ import annotations

from typing import Iterable

from neo4j import GraphDatabase

from app.graph.extract import Edge
from app.settings import get_settings

_CONSTRAINTS = [
    ("Document", "id"),
    ("FailureBucket", "id"),
    ("Component", "canonical_name"),
    ("BusinessEntity", "id"),
]


def _ensure_constraints_tx(tx) -> None:
    for label, key in _CONSTRAINTS:
        tx.run(
            f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE"
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
