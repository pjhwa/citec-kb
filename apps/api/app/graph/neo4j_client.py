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


# G08(2차 리뷰): explore()의 경로 선택에서 동점 처리 — 구조적 관계(계층·명시
# 참조·근거)가 본문 추론 컴포넌트보다 "경로 설명"으로서 우선해야 한다.
# 알파벳 순(HAS_COMPONENT < PARENT_OF)으로 정렬하면 형제/조손 관계가 계층
# 경로 대신 공유 컴포넌트 경로로 뒤바뀌어 설명력이 떨어진다.
_REL_TYPE_PRIORITY = {
    "PARENT_OF": 0,
    "REFERENCES": 1,
    "HAS_EVIDENCE": 2,
    "MENTIONS_ENTITY": 3,
    "HAS_COMPONENT": 4,
    "SIMILAR_TO": 5,
}


def _node_identity(node) -> dict:
    """노드 하나를 레이블에 맞는 최소 식별 정보로 축약한다(G08 — 중간 노드의
    "via" 식별에 쓴다. 전체 Neo4j 노드 객체를 그대로 직렬화하지 않는다)."""
    labels = set(node.labels)
    if "Document" in labels:
        return {"label": "Document", "id": node["id"], "title": node.get("title")}
    if "Component" in labels:
        return {"label": "Component", "canonical_name": node["canonical_name"]}
    if "FailureBucket" in labels:
        return {"label": "FailureBucket", "id": node["id"], "bucket_name": node.get("bucket_name")}
    if "BusinessEntity" in labels:
        return {"label": "BusinessEntity", "id": node.get("id")}
    if "UnresolvedPage" in labels:
        return {"label": "UnresolvedPage", "external_id": node.get("external_id")}
    return {"label": next(iter(labels), None)}


def _build_path(entry: dict) -> list[dict]:
    """explore()의 raw neighbor entry(hop1 또는 hop2) -> G08이 요구하는
    "전체 경로"(node/edge 순서, 방향, EXTRACTED/INFERRED 구분) 리스트.
    hop1은 길이 1(anchor-neighbor 직접 edge), hop2는 길이 2(anchor-중간노드-
    neighbor)다. direction은 edge 방향 자체를 뜻한다 — "out"=가까운 쪽(anchor
    또는 중간노드)이 relationship의 source, "in"=target. "up"/"down"(계층
    의미)은 PARENT_OF에서만 뜻이 있고 REFERENCES/HAS_COMPONENT 등에는 의미가
    없어 API 값으로 쓰지 않는다(2차 리뷰 지적) — 렌더링(MCP)에서만 PARENT_OF를
    ↑/↓로, 나머지를 →/←로 표시한다."""
    if entry.get("via_node") is not None:
        return [
            {
                "relation": entry["via_relation"], "tag": entry.get("via_tag"),
                "direction": "out" if entry.get("via_down") else "in",
                "via": _node_identity(entry["via_node"]),
            },
            {
                "relation": entry["relation"], "tag": entry.get("tag"),
                "direction": "out" if entry.get("down") else "in",
            },
        ]
    return [
        {
            "relation": entry["relation"], "tag": entry.get("tag"),
            "direction": "out" if entry.get("down") else "in",
        },
    ]


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
        max_hops는 현재 항상 2로 고정이며 파라미터 값은 무시된다(향후 가변 깊이 지원을 위한 자리 — 1단계는 쓰지 않음).

        G08(2차 리뷰): 과거엔 `collect(DISTINCT {...})`가 같은 노드로 가는 서로
        다른 경로를 전부 별도 항목으로 남겨(NetApp처럼 1hop 문서가 7천 건대면
        2hop 컴포넌트 하나가 그만큼 중복 생성될 뻔함), 그중 어느 게 "표시"될지는
        Neo4j 쪽 순서에 맡겨져 비결정적이었다(G09 잔여와 같은 원인). 이제
        노드별로 Cypher 안에서 먼저 정렬한 뒤 `head(collect(...))`로 결정적으로
        하나만 고르고, 2hop은 중간 노드(p)와 두 relation의 방향까지 함께 가져와
        "마지막 relation 하나로 전체 경로를 설명"하던 문제(G08)도 해결한다."""
        # 동점 처리 키는 elementId가 아니라 도메인 키(id/canonical_name/
        # external_id)를 쓴다 — elementId는 DB 인스턴스 안에서만 안정적이라,
        # dev/운영/매 scratch 재구축마다 동일 데이터에 다른 경로가 선택되면
        # 증거가 환경 간 재현되지 않는다(2차 리뷰 지적).
        query = f"""
        MATCH (a:{anchor_label} {{{anchor_key}: $value}})
        OPTIONAL MATCH (a)-[r1]-(n1)
        WHERE n1 <> a
        WITH a, n1, r1
        ORDER BY coalesce($rel_priority[type(r1)], 99), coalesce(n1.id, n1.canonical_name, n1.external_id)
        WITH a, n1, head(collect(r1)) AS best_r1
        WITH a, collect(DISTINCT {{
            node: n1, relation: type(best_r1), tag: best_r1.tag, hops: 1,
            down: startNode(best_r1) = a
        }}) AS hop1
        CALL (a) {{
            OPTIONAL MATCH (a)-[r1b]-(p)-[r2]-(n2)
            WHERE n2 <> a AND NOT coalesce(p.is_hub, false)
            WITH a, n2, p, r1b, r2
            ORDER BY
                coalesce($rel_priority[type(r1b)], 99) + coalesce($rel_priority[type(r2)], 99),
                coalesce($rel_priority[type(r1b)], 99),
                coalesce(p.id, p.canonical_name, p.external_id)
            WITH a, n2, head(collect({{p: p, r1b: r1b, r2: r2}})) AS best
            RETURN collect(DISTINCT {{
                node: n2, relation: type(best.r2), tag: best.r2.tag, hops: 2,
                down: startNode(best.r2) = best.p,
                via_node: best.p, via_relation: type(best.r1b), via_tag: best.r1b.tag,
                via_down: startNode(best.r1b) = a
            }}) AS hop2
        }}
        RETURN a AS anchor, hop1 + hop2 AS neighbors
        """
        with self._driver.session() as session:
            record = session.run(query, value=anchor_value, rel_priority=_REL_TYPE_PRIORITY).single()
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
                best_by_id[elem_id] = entry

        documents: list[dict] = []
        components: list[dict] = []
        failure_buckets: list[dict] = []
        excluded_hub_components: list[str] = []
        for entry in best_by_id.values():
            node = entry["node"]
            labels = set(node.labels)
            path = _build_path(entry)
            if "Document" in labels:
                documents.append({
                    "id": node["id"], "title": node.get("title"), "source_type": node.get("source_type"),
                    "external_id": node.get("external_id"),
                    "relation": entry["relation"], "hops": entry["hops"], "route": path,
                })
            elif "Component" in labels:
                if node.get("is_hub"):
                    excluded_hub_components.append(node["canonical_name"])
                    continue
                components.append({
                    "canonical_name": node["canonical_name"],
                    "relation": entry["relation"], "hops": entry["hops"], "route": path,
                })
            elif "FailureBucket" in labels:
                failure_buckets.append({
                    "id": node["id"], "bucket_name": node.get("bucket_name"),
                    "relation": entry["relation"], "hops": entry["hops"], "route": path,
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
