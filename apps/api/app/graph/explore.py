"""kb_graph_explore가 쓰는 순수 로직 — Neo4j/Postgres 연결 전혀 없음, 전부 dict 입출력.
app.graph.neo4j_client.Neo4jClient.explore()의 raw 결과를 받아 API 응답 모양으로
다듬는다(정렬/절단/evidence_grade 병합) — app.graph.extract의 "추출기는 순수 함수"
원칙을 조회 경로에도 그대로 적용한다."""

from __future__ import annotations

from typing import Any, Optional

from app.graph.identity import GRADE_RANK

MAX_RESULTS_PER_CATEGORY = 50

# G09: 과거 로컬 정의가 "A-"를 빠뜨려 C와 동일 취급했다 — app.graph.identity의
# 단일 소스로 통합.
_GRADE_ORDER = GRADE_RANK


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
    # G09(2차 리뷰): hops/grade만으로는 동률일 때 Neo4j collect()의 비결정적
    # 순서가 그대로 샌다("결정적 정렬" 요구 §3.2 위반) — id를 최종 tie-break로
    # 추가한다. 카테고리별 고유 키 필드가 다르므로(documents/failure_buckets는
    # "id", components는 "canonical_name") 둘 다 본다.
    tie_break = item.get("id") or item.get("canonical_name") or ""
    return (item.get("hops", 99), grade, tie_break)


def _paginate(items: list[dict], offset: int) -> tuple[list[dict], int, bool]:
    """G07 — 정렬된 전체 후보에서 offset부터 최대 MAX_RESULTS_PER_CATEGORY개를
    돌려준다. 과거엔 cap=50에서 끊긴 뒤로는 접근 수단이 전혀 없었다(정확히
    50개와 51개 이상이 MCP에서 똑같이 "50건+"로 보임) — total/truncated를
    카테고리별로 따로 돌려주고, offset으로 다음 페이지를 요청할 수 있게 한다."""
    total = len(items)
    offset = max(0, offset)
    page = items[offset:offset + MAX_RESULTS_PER_CATEGORY]
    truncated = offset + len(page) < total
    return page, total, truncated


def shape_explore_result(
    raw: dict[str, Any], *, as_of: Optional[str], anchor: dict[str, Any],
    offsets: dict[str, int] | None = None, status: Optional[str] = None,
) -> dict[str, Any]:
    """Neo4jClient.explore()의 raw 출력(found/documents/components/failure_buckets/
    excluded_hub_components) -> API 응답 모양(as_of/anchor/정렬/절단 포함).

    offsets: {"documents": int, "components": int, "failure_buckets": int} —
    카테고리별 독립 페이지 시작 위치(G07). 생략하면 전부 0(첫 페이지).

    status(G08 §2.6 — "found/not_found/no_match/filtered/isolated/partial/
    backend_error를 기계가 구분하도록"): 호출자가 명시하면 그대로 쓰고, 생략
    하면 found/neighbor 존재 여부로 자동 산출한다("not_found"/"isolated"/
    "found"만 — "no_match"/"filtered"/"backend_error"는 symptom_text 무매칭,
    G04 anchor 자체 제외, Neo4j 오류처럼 이 함수가 알 수 없는 맥락이 있어야
    호출자가 명시해야 한다. "partial"은 아직 그 상태를 만드는 경로가 없어
    정의하지 않는다 — 2차 리뷰 지적대로 쓰이지 않는 enum 값을 미리 넣지 않음)."""
    offsets = offsets or {}
    if not raw.get("found"):
        return {
            "as_of": as_of, "anchor": anchor, "found": False, "status": status or "not_found",
            "documents": [], "components": [], "failure_buckets": [],
            "excluded_hub_components": raw.get("excluded_hub_components") or [],
            "truncated": False,
            "documents_total": 0, "documents_truncated": False,
            "components_total": 0, "components_truncated": False,
            "failure_buckets_total": 0, "failure_buckets_truncated": False,
        }

    documents = sorted(raw.get("documents") or [], key=_sort_key)
    components = sorted(raw.get("components") or [], key=_sort_key)
    failure_buckets = sorted(raw.get("failure_buckets") or [], key=_sort_key)

    documents, doc_total, doc_trunc = _paginate(documents, offsets.get("documents", 0))
    components, comp_total, comp_trunc = _paginate(components, offsets.get("components", 0))
    failure_buckets, fb_total, fb_trunc = _paginate(failure_buckets, offsets.get("failure_buckets", 0))

    if status is None:
        status = "found" if (doc_total or comp_total or fb_total) else "isolated"

    return {
        "as_of": as_of, "anchor": anchor, "found": True, "status": status,
        "documents": documents, "components": components, "failure_buckets": failure_buckets,
        "excluded_hub_components": raw.get("excluded_hub_components") or [],
        # 하위 호환 — 셋 중 하나라도 더 있으면 True(기존 소비자는 이 필드만 봐도 된다).
        "truncated": doc_trunc or comp_trunc or fb_trunc,
        "documents_total": doc_total, "documents_truncated": doc_trunc,
        "components_total": comp_total, "components_truncated": comp_trunc,
        "failure_buckets_total": fb_total, "failure_buckets_truncated": fb_trunc,
    }


def _dedup_tie_break_key(item: dict) -> tuple:
    # G08(2차 리뷰) 잔여 — hops가 같을 때 과거엔 "먼저 본 쪽"이 이겨서, 입력
    # 순서(여러 앵커를 어떤 순서로 순회했는지)에 따라 같은 요청의 결과가
    # 달라질 수 있었다. 항목 내용만으로 결정되는 키로 비교해 입력 순서와
    # 무관하게 항상 같은 승자를 고른다.
    return (item.get("hops", 99), item.get("relation") or "", item.get("tag") or "")


def dedup_by_key(items: list[dict], key: str) -> list[dict]:
    """복수 앵커(symptom_text가 여러 component에 매칭된 경우)의 explore() 결과를
    합칠 때 중복 제거 — 설계 스펙의 "복수 앵커를 합쳐 중복 제거" 요구. 중복이면
    hops가 더 작은(더 가까운) 쪽을 유지하고, 동률이면 _dedup_tie_break_key로
    결정적으로 고른다. app.routers.graph(Neo4j 의존)가 아니라 여기(Neo4j
    비의존)에 둬야 app/graph/explore.py 전체가 CI에서 매번 실제로 실행된다는
    이 모듈의 설계 원칙(파일 상단 docstring)이 깨지지 않는다."""
    best: dict[str, dict] = {}
    for item in items:
        k = item[key]
        if k not in best or _dedup_tie_break_key(item) < _dedup_tie_break_key(best[k]):
            best[k] = item
    return list(best.values())


def filter_excluded_documents(
    documents: list[dict],
    *,
    exclude_page_ids: set[str] | None = None,
    exclude_subtree_ids: set[str] | None = None,
    exclude_source_types: set[str] | None = None,
    ancestor_ids_by_doc_id: dict[str, list[str]] | None = None,
) -> list[dict]:
    """G04 — 평가 보고서/제외 트리를 candidate 단계에서 걷어낸다(정렬·cap 이전).
    app.retrieval.search의 exclude_page_ids/exclude_subtree_ids/exclude_source_types와
    같은 의미: page_ids/source_types는 문서 자신을, subtree_ids는 Postgres의
    ancestor_ids 메타데이터(ancestor_ids_by_doc_id로 주입)로 판정한다."""
    exclude_page_ids = exclude_page_ids or set()
    exclude_subtree_ids = exclude_subtree_ids or set()
    exclude_source_types = exclude_source_types or set()
    ancestor_ids_by_doc_id = ancestor_ids_by_doc_id or {}
    if not (exclude_page_ids or exclude_subtree_ids or exclude_source_types):
        return documents

    kept = []
    for doc in documents:
        if doc.get("external_id") in exclude_page_ids:
            continue
        if doc.get("source_type") in exclude_source_types:
            continue
        ancestors = set(ancestor_ids_by_doc_id.get(doc["id"]) or [])
        if doc.get("external_id") in exclude_subtree_ids:
            continue
        if ancestors & exclude_subtree_ids:
            continue
        kept.append(doc)
    return kept


def enrich_with_evidence_grade(documents: list[dict], grade_by_id: dict[str, str]) -> list[dict]:
    """Document 노드는 evidence_grade를 안 들고 있다(Postgres만 source of truth,
    1단계 설계 §0 원칙) — 그래서 호출자가 Neo4j 결과의 document id들로 Postgres를
    배치 조회한 뒤 이 함수로 병합한다. 못 찾으면 None(삭제된 문서 등 엣지 케이스)."""
    return [{**doc, "evidence_grade": grade_by_id.get(doc["id"])} for doc in documents]
