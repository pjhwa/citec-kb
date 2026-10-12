"""Thin Neo4j wrapper — only the MERGE operations app.graph.pipeline needs."""

from __future__ import annotations

import threading
from typing import Iterable, Optional

try:
    from neo4j import GraphDatabase
except ImportError:  # pragma: no cover — requirements-ci.txt가 일부러 neo4j를 뺀다
    # (G01~G05 작업 중 발견): app.main이 이 모듈을 거치는 graph 라우터를 항상
    # import하므로, 이 import가 모듈 최상단에서 실패하면 app.main을 import하는
    # 모든 테스트(예: test_mock_idp_e2e.py)가 그래프와 전혀 무관해도 CI에서
    # 깨진다. 실제로 Neo4jClient를 생성할 때만 필요하므로 지연시킨다.
    GraphDatabase = None  # type: ignore[assignment]

from app.graph.extract import Edge
from app.settings import get_settings

_CONSTRAINTS = [
    ("Document", "id"),
    ("FailureBucket", "id"),
    ("Component", "canonical_name"),
    ("BusinessEntity", "id"),
    ("UnresolvedPage", "external_id"),  # G01 — 없으면 MERGE/MATCH가 전체 레이블 스캔
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
        if GraphDatabase is None:
            raise ImportError(
                "neo4j package not installed — not in requirements-ci.txt by design; "
                "install it (requirements.txt) to actually connect to Neo4j"
            )
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

    def read_generation_marker(self) -> Optional[str]:
        """G05 — 어떤 generation으로 이 Neo4j가 마지막으로 쓰였는지는 Neo4j
        자신에게 물어야 한다(Postgres의 --generation 플래그 값은 사람이 깜빡하고
        안 바꿔도 전혀 알 길이 없다 — 1차 리뷰에서 지적된 바로 그 문제)."""
        with self._driver.session() as session:
            record = session.run("MATCH (m:GraphMeta {key: 'generation'}) RETURN m.value AS v").single()
        return record["v"] if record else None

    def write_generation_marker(self, generation: str) -> None:
        with self._driver.session() as session:
            session.run(
                "MERGE (m:GraphMeta {key: 'generation'}) SET m.value = $v",
                v=generation,
            )

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
                    "external_id": node.get("external_id"),
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

    def is_component_hub(self, canonical_name: str) -> Optional[bool]:
        """component 앵커 자신이 허브인지 확인 — 스펙 §2: 허브 자신이 앵커면 2hop 순회를
        생략하고 즉시 반환해야 한다(토큰 낭비 방지). 존재하지 않는 컴포넌트면 None."""
        with self._driver.session() as session:
            record = session.run(
                "MATCH (c:Component {canonical_name: $name}) RETURN c.is_hub AS is_hub",
                name=canonical_name,
            ).single()
        return bool(record["is_hub"]) if record else None

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

    def merge_document(
        self, doc: dict, edges: Iterable[Edge], *,
        extractor_version: str = "unstamped", generation: str = "unstamped",
    ) -> None:
        with self._driver.session() as session:
            session.execute_write(_merge_document_tx, doc, list(edges), extractor_version, generation)

    def merge_failure_bucket(
        self, bucket: dict, edges: Iterable[Edge], *,
        extractor_version: str = "unstamped", generation: str = "unstamped",
    ) -> None:
        with self._driver.session() as session:
            session.execute_write(_merge_failure_bucket_tx, bucket, list(edges), extractor_version, generation)


_singleton_client: Optional["Neo4jClient"] = None
_singleton_lock = threading.Lock()


def get_shared_client() -> "Neo4jClient":
    """요청마다 새 Driver를 만들지 않도록(연결 풀링 혜택을 받도록) 프로세스 전역에서
    하나만 재사용한다. FastAPI lifespan이 종료 시 close_shared_client()를 호출해야 한다."""
    global _singleton_client
    if _singleton_client is None:
        with _singleton_lock:
            if _singleton_client is None:
                _singleton_client = Neo4jClient()
    return _singleton_client


def close_shared_client() -> None:
    global _singleton_client
    with _singleton_lock:
        if _singleton_client is not None:
            _singleton_client.close()
            _singleton_client = None


# G02: PARENT_OF는 extract_hierarchy 기준으로 "자신의 부모"를 가리키지만, 실제
# Neo4j에는 의미대로 parent->child로 써야 한다 — 그래서 이 rel_type만
# source/target을 뒤집어 MERGE한다(G02 결정 — docs/superpowers/specs/
# 2026-10-12-graph-correctness-decisions.md). 다른 rel_type은 그대로
# source->target.
_FLIPPED_REL_TYPES = {"PARENT_OF"}

# G03: 재동기화 때 지워야 할 "이 엔티티가 소유한" managed relationship 목록 —
# PARENT_OF는 자신이 타깃(자신은 child)이라 incoming, 나머지는 자신이
# 소스라 outgoing. 둘을 구분해야 "자신이 부모인 다른 문서들의 PARENT_OF"를
# 실수로 지우지 않는다.
_DOCUMENT_OWNED_OUTGOING = ["HAS_COMPONENT", "REFERENCES", "MENTIONS_ENTITY"]
_DOCUMENT_OWNED_INCOMING = ["PARENT_OF"]
_BUCKET_OWNED_OUTGOING = ["HAS_EVIDENCE", "SIMILAR_TO", "REFERENCES"]  # G10


def _replace_owned_edges_tx(
    tx, label: str, key: str, value: str, *, outgoing: list[str], incoming: list[str]
) -> None:
    """merge_* 쓰기 전에 호출 — 이 엔티티가 과거에 냈던 managed edge를 전부
    지운 뒤 새 edges로 재생성한다(G03 — 멱등 재동기화, 사라진 멘션도 자동 제거)."""
    if outgoing:
        rel_pattern = "|".join(outgoing)
        tx.run(
            f"MATCH (s:{label} {{{key}: $value}})-[r:{rel_pattern}]->() DELETE r",
            value=value,
        )
    if incoming:
        rel_pattern = "|".join(incoming)
        tx.run(
            f"MATCH (s:{label} {{{key}: $value}})<-[r:{rel_pattern}]-() DELETE r",
            value=value,
        )


def _delete_legacy_unstamped_parent_of_tx(tx, value: str) -> None:
    """G02 방향 수정 전 코드는 PARENT_OF를 (child)-[:PARENT_OF]->(ancestor)로
    썼다(자신 기준 outgoing) — 지금은 parent가 소스이므로 "자신이 parent인
    다른 문서로의 outgoing PARENT_OF"가 정상적으로 존재할 수 있어, 자신의
    outgoing PARENT_OF를 통째로 지울 수 없다. extractor_version이 찍힌 edge는
    새 코드가 쓴 "내가 부모인" 정상 edge이므로 보존하고, 찍히지 않은(이번
    수정 전에 쓰인) outgoing PARENT_OF만 유령으로 간주해 지운다."""
    tx.run(
        """
        MATCH (s:Document {id: $value})-[r:PARENT_OF]->()
        WHERE r.extractor_version IS NULL
        DELETE r
        """,
        value=value,
    )


def _merge_edges(
    tx, source_label: str, source_key: str, source_value: str, edges: list[Edge],
    extractor_version: str, generation: str,
) -> None:
    for e in edges:
        # target_label/rel_type은 이 저장소의 고정 enum("PARENT_OF" 등)뿐이라
        # f-string 삽입이 안전하다 — 외부 입력이 이 값으로 들어오는 경로는 없다.
        tx.run(f"MERGE (t:{e.target_label} {{{e.target_key}: $value}})", value=e.target_value)
        if e.rel_type in _FLIPPED_REL_TYPES:
            query = f"""
                MATCH (s:{source_label} {{{source_key}: $source_value}})
                MATCH (t:{e.target_label} {{{e.target_key}: $target_value}})
                MERGE (t)-[r:{e.rel_type}]->(s)
                SET r.tag = $tag, r.extractor_version = $extractor_version, r.generation = $generation
            """
        else:
            query = f"""
                MATCH (s:{source_label} {{{source_key}: $source_value}})
                MATCH (t:{e.target_label} {{{e.target_key}: $target_value}})
                MERGE (s)-[r:{e.rel_type}]->(t)
                SET r.tag = $tag, r.extractor_version = $extractor_version, r.generation = $generation
            """
        tx.run(
            query, source_value=source_value, target_value=e.target_value, tag=e.tag,
            extractor_version=extractor_version, generation=generation,
        )


def _merge_document_tx(tx, doc: dict, edges: list[Edge], extractor_version: str, generation: str) -> None:
    tx.run(f"MERGE (d:Document {{id: $id}}){_DOCUMENT_SET_CLAUSE}", **doc)
    _replace_owned_edges_tx(
        tx, "Document", "id", doc["id"],
        outgoing=_DOCUMENT_OWNED_OUTGOING, incoming=_DOCUMENT_OWNED_INCOMING,
    )
    _delete_legacy_unstamped_parent_of_tx(tx, doc["id"])
    _merge_edges(tx, "Document", "id", doc["id"], edges, extractor_version, generation)


def _merge_failure_bucket_tx(
    tx, bucket: dict, edges: list[Edge], extractor_version: str, generation: str,
) -> None:
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
    _replace_owned_edges_tx(
        tx, "FailureBucket", "id", bucket["id"],
        outgoing=_BUCKET_OWNED_OUTGOING, incoming=[],
    )
    _merge_edges(tx, "FailureBucket", "id", bucket["id"], edges, extractor_version, generation)
