"""kb_graph_explore가 쓰는 순수 로직 — Neo4j/Postgres 연결 전혀 없음, 전부 dict 입출력.
app.graph.neo4j_client.Neo4jClient.explore()의 raw 결과를 받아 API 응답 모양으로
다듬는다(정렬/절단/evidence_grade 병합) — app.graph.extract의 "추출기는 순수 함수"
원칙을 조회 경로에도 그대로 적용한다."""

from __future__ import annotations

from typing import Any, Optional

from app.graph.identity import GRADE_RANK, resolve_representative

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
    freshness: dict[str, Any] | None = None,
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
            "freshness": freshness,
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
        "freshness": freshness,
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


def is_document_excluded(
    identity: dict,
    *,
    exclude_page_ids: set[str],
    exclude_subtree_ids: set[str],
    exclude_source_types: set[str],
    ancestors: set[str] | None = None,
) -> bool:
    """G04/G08 공용 판정 — identity는 최소 external_id/source_type을 담은 dict
    (raw Neo4j document dict든 _fetch_document_enrichment() 출력이든 둘 다 맞음).
    filter_excluded_documents(후보 목록)와 라우터의 anchor 자체 제외 판정
    (G08 "filtered" status)이 이 함수 하나로 같은 정책을 공유한다."""
    external_id = identity.get("external_id")
    if external_id in exclude_page_ids or external_id in exclude_subtree_ids:
        return True
    if identity.get("source_type") in exclude_source_types:
        return True
    if ancestors and ancestors & exclude_subtree_ids:
        return True
    return False


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

    return [
        doc for doc in documents
        if not is_document_excluded(
            doc,
            exclude_page_ids=exclude_page_ids,
            exclude_subtree_ids=exclude_subtree_ids,
            exclude_source_types=exclude_source_types,
            ancestors=set(ancestor_ids_by_doc_id.get(doc["id"]) or []),
        )
    ]


def enrich_with_evidence_grade(documents: list[dict], grade_by_id: dict[str, str]) -> list[dict]:
    """Document 노드는 evidence_grade를 안 들고 있다(Postgres만 source of truth,
    1단계 설계 §0 원칙) — 그래서 호출자가 Neo4j 결과의 document id들로 Postgres를
    배치 조회한 뒤 이 함수로 병합한다. 못 찾으면 None(삭제된 문서 등 엣지 케이스)."""
    return [{**doc, "evidence_grade": grade_by_id.get(doc["id"])} for doc in documents]


def enrich_with_document_metadata(documents: list[dict], enrichment_by_id: dict[str, dict]) -> list[dict]:
    """evidence_grade뿐 아니라 archived 여부까지 한 번에 병합한다(G08 2차 리뷰
    — "비활성 문서가 표시돼야 한다"). 문서별 필드 이름을 "status"로 하면
    응답 최상위의 "status"(found/not_found/...)와 이름이 겹쳐 혼동을 주므로
    "archived" bool로 명확히 분리한다. 호출자는 PG를 한 번만 조회해 이 dict와
    collapse_duplicate_copies에 같이 넘긴다."""
    out = []
    for doc in documents:
        meta = enrichment_by_id.get(doc["id"])
        # R04(2026-10-13 2차 핸드오프) §4 — PG에 행이 없으면(삭제됨 등) meta가
        # 없다. 과거엔 이 경우도 meta.get("status")=="archived" 비교가
        # False를 내서 "활성 상태의 정상 문서"처럼 보였다 — 누락과 active를
        # 구분해야 한다.
        out.append({
            **doc, "evidence_grade": (meta or {}).get("evidence_grade"),
            "archived": (meta or {}).get("status") == "archived",
            "pg_missing": meta is None,
        })
    return out


def apply_exposure_policy(documents: list[dict], *, include_archived: bool = False) -> list[dict]:
    """F05(2026-10-13 최종 핸드오프) §2.5.5 — 비활성/archived 문서의 기본 노출
    정책. 호출자가 명시적으로 include_archived=True를 주지 않는 한, archived
    문서는 "연관 문서" 목록에서 기본 제외한다 — enrich_with_document_metadata가
    이미 archived bool을 채운 뒤에만 호출할 수 있다.

    pg_missing(Postgres에 행이 없는 노드)은 이 정책의 대상이 아니다 — 의도적
    결정: pg_missing은 "비활성 문서를 숨길지"와는 다른 축의 문제(유령 노드
    데이터 정합성)라, 전용 도구(scripts/verify/ghost_node_audit.py)가 다룬다.
    여기서 기본 제외해버리면 "archived가 아닌데 PG 조회가 일시적으로 늦은"
    정상 케이스까지 숨겨 혼동을 줄 수 있다 — 계속 응답에 pg_missing bool로
    노출만 하고 필터링은 하지 않는다.

    알려진 한계(의도적으로 범위 밖, 정직하게 문서화): merge_document가 Neo4j
    Document 노드에 status를 전혀 안 쓴다(Postgres만 source of truth,
    1단계 설계 원칙) — 그래서 이 필터는 Cypher 순회 "이후", 응답을 만드는
    단계에서만 적용된다. archived 문서가 다른 두 문서 사이의 중간 경로
    (bridge)로 쓰이는 경로 자체를 끊는 건 Neo4j 쓰기 경로 변경(재백필 필요)
    이라 이번 라운드에서 다루지 않는다 — 이 함수는 "결과 목록에 보여줄지"만
    결정하고, 순회 자체는 그대로다."""
    if include_archived:
        return documents
    return [doc for doc in documents if not doc.get("archived")]


_COLLAPSIBLE_SOURCE_TYPES = {"confluence_map", "confluence_docs", "tech_repo"}


def collapse_duplicate_copies(documents: list[dict], *, enrichment_by_id: dict[str, dict]) -> list[dict]:
    """같은 Confluence 페이지를 confluence_map/confluence_docs/tech_repo가 각각
    따로 수집한 "사본"을 하나의 논리 후보로 묶는다(G08 §2.6 "같은 Confluence
    page의 수집본은 하나의 논리 후보로 묶고 실제 수집본 목록을 보존"). 이 세
    source_type 밖으로는 교차 소스 external_id 충돌이 실제 코퍼스에 없음을
    확인했으므로(docs/features/graph-evidence.md) 이 세 소스로만 묶는다 —
    "_EN" 접미사(한영 체크항목) 등 다른 source_type은 절대 묶지 않는다(§2.6
    금지사항: "_EN 접미사만으로 일반 문서를 합치지 않는다")."""
    groups: dict[str, list[dict]] = {}
    passthrough: list[dict] = []
    for doc in documents:
        meta = enrichment_by_id.get(doc["id"]) or {}
        source_type = meta.get("source_type")
        external_id = meta.get("external_id")
        if source_type in _COLLAPSIBLE_SOURCE_TYPES and external_id:
            groups.setdefault(external_id, []).append(doc)
        else:
            passthrough.append(doc)

    collapsed: list[dict] = []
    for external_id in sorted(groups):
        group = groups[external_id]
        if len(group) == 1:
            collapsed.append(group[0])
            continue
        candidates = [{**(enrichment_by_id.get(d["id"]) or {}), "document_id": d["id"]} for d in group]
        best = resolve_representative(candidates)
        rep_id = best["document_id"] if best else group[0]["id"]
        rep_doc = dict(next(d for d in group if d["id"] == rep_id))
        rep_doc["copies"] = sorted(d["id"] for d in group if d["id"] != rep_id)
        # G08 잔여(graph-evidence.md) — 대표가 아닌 수집본이 더 적은 hop으로
        # 도달했어도 과거엔 대표 자신의 hops/route만 보여줘 그 정보가 사라졌다.
        # 묶인 그룹 전체에서 가장 가까운(hops 최소) 쪽의 hops/route를 쓴다 —
        # "같은 논리 문서가 실제로 얼마나 가까운지"를 대표 선택과 별개로 보여준다.
        closest = min(group, key=lambda d: d.get("hops", 99))
        rep_doc["hops"] = closest.get("hops", rep_doc.get("hops"))
        rep_doc["route"] = closest.get("route", rep_doc.get("route"))
        collapsed.append(rep_doc)
    return collapsed + passthrough
