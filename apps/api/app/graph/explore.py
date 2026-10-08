"""kb_graph_explore가 쓰는 순수 로직 — Neo4j/Postgres 연결 전혀 없음, 전부 dict 입출력.
app.graph.neo4j_client.Neo4jClient.explore()의 raw 결과를 받아 API 응답 모양으로
다듬는다(정렬/절단/evidence_grade 병합) — app.graph.extract의 "추출기는 순수 함수"
원칙을 조회 경로에도 그대로 적용한다."""

from __future__ import annotations

from typing import Any, Optional

MAX_RESULTS_PER_CATEGORY = 50

_GRADE_ORDER = {"A": 0, "B": 1, "C": 2}


def resolve_component_anchor(anchor_value: str, lexicon_map: dict[str, list[str]]) -> str:
    """"넷앱"/"netapp"처럼 lexicon 변형어로 들어와도 canonical_name으로 정규화한다.
    lexicon_map은 app.lexicon.seed.load_lexicon_map()의 출력(토큰 소문자 -> [canonical, ...])과
    동일한 형태 — variants[0]이 canonical이라는 계약도 그대로 재사용한다. 매칭이 없으면
    원문 그대로 반환해, 호출자(Neo4jClient.explore)가 대소문자 무시 매칭을 한 번 더 시도할
    여지를 남긴다."""
    variants = lexicon_map.get(anchor_value.strip().lower())
    if variants:
        return variants[0]
    return anchor_value


def _sort_key(item: dict) -> tuple:
    grade = _GRADE_ORDER.get(item.get("evidence_grade") or "", 3)
    return (item.get("hops", 99), grade)


def _cap(items: list[dict]) -> tuple[list[dict], bool]:
    if len(items) <= MAX_RESULTS_PER_CATEGORY:
        return items, False
    return items[:MAX_RESULTS_PER_CATEGORY], True


def shape_explore_result(raw: dict[str, Any], *, as_of: Optional[str], anchor: dict[str, Any]) -> dict[str, Any]:
    """Neo4jClient.explore()의 raw 출력(found/documents/components/failure_buckets/
    excluded_hub_components) -> API 응답 모양(as_of/anchor/정렬/절단 포함)."""
    if not raw.get("found"):
        return {
            "as_of": as_of, "anchor": anchor, "found": False,
            "documents": [], "components": [], "failure_buckets": [],
            "excluded_hub_components": raw.get("excluded_hub_components") or [],
            "truncated": False,
        }

    documents = sorted(raw.get("documents") or [], key=_sort_key)
    components = sorted(raw.get("components") or [], key=_sort_key)
    failure_buckets = sorted(raw.get("failure_buckets") or [], key=_sort_key)

    documents, doc_trunc = _cap(documents)
    components, comp_trunc = _cap(components)
    failure_buckets, fb_trunc = _cap(failure_buckets)

    return {
        "as_of": as_of, "anchor": anchor, "found": True,
        "documents": documents, "components": components, "failure_buckets": failure_buckets,
        "excluded_hub_components": raw.get("excluded_hub_components") or [],
        "truncated": doc_trunc or comp_trunc or fb_trunc,
    }


def dedup_by_key(items: list[dict], key: str) -> list[dict]:
    """복수 앵커(symptom_text가 여러 component에 매칭된 경우)의 explore() 결과를
    합칠 때 중복 제거 — 설계 스펙의 "복수 앵커를 합쳐 중복 제거" 요구. 중복이면
    hops가 더 작은(더 가까운) 쪽을 유지한다. app.routers.graph(Neo4j 의존)가 아니라
    여기(Neo4j 비의존)에 둬야 app/graph/explore.py 전체가 CI에서 매번 실제로
    실행된다는 이 모듈의 설계 원칙(파일 상단 docstring)이 깨지지 않는다."""
    best: dict[str, dict] = {}
    for item in items:
        k = item[key]
        if k not in best or item.get("hops", 99) < best[k].get("hops", 99):
            best[k] = item
    return list(best.values())


def enrich_with_evidence_grade(documents: list[dict], grade_by_id: dict[str, str]) -> list[dict]:
    """Document 노드는 evidence_grade를 안 들고 있다(Postgres만 source of truth,
    1단계 설계 §0 원칙) — 그래서 호출자가 Neo4j 결과의 document id들로 Postgres를
    배치 조회한 뒤 이 함수로 병합한다. 못 찾으면 None(삭제된 문서 등 엣지 케이스)."""
    return [{**doc, "evidence_grade": grade_by_id.get(doc["id"])} for doc in documents]
