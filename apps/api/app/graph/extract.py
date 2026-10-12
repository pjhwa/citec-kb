"""Pure extractor functions: Document dict in -> list[Edge] out.

No DB, no Neo4j connection here — app.graph.pipeline supplies whatever
lookups each extractor needs (lexicon map, external_id index, …) as plain
arguments, so every function here stays independently unit-testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    rel_type: str
    target_label: str
    target_key: str
    target_value: str
    tag: str  # "EXTRACTED" | "INFERRED"


def extract_hierarchy(doc: dict, *, page_identity_index: dict[str, list[dict]] | None = None) -> list[Edge]:
    """metadata["ancestor_ids"] -> 직계 부모 1개만 PARENT_OF로 낸다(G02 — 조상
    전체가 아니라 바로 위 부모만; Neo4jClient._merge_edges가 PARENT_OF를 특수
    취급해 실제로는 parent->child 방향으로 쓴다).

    adapters.py builds ancestor_ids as [root, ..., parent, self] (see
    apps/api/app/confluence/map_sync.py build_frontmatter_confluence_map and
    apps/api/app/ingest/adapters.py:233-237/294-298/351-355) — ancestor_ids는
    raw Confluence page id(접두사 없음)라, doc["id"](예: "confluence_map:604365046")
    와는 절대 같아질 수 없다 — self 제거는 doc["external_id"](raw id)로 해야
    한다(G01). doc["id"] 비교도 안전망으로 남겨 다른 source 패턴을 보호한다.

    page_identity_index(app.graph.pipeline.build_page_identity_index()의 출력,
    raw id 소문자 -> candidate 목록)로 부모를 Postgres의 대표 Document로
    해석한다. 코퍼스에 없으면 UnresolvedPage로 낸다 — Document처럼 보이는 빈
    노드를 만들지 않는다(G01 금지사항).
    """
    from app.graph.identity import best_candidate_id

    ancestor_ids = (doc.get("metadata") or {}).get("ancestor_ids") or []
    self_raw = (doc.get("external_id") or "").strip().lower()
    chain = [
        a for a in ancestor_ids
        if a != doc["id"] and str(a).strip().lower() != self_raw
    ]
    if not chain:
        return []
    parent_raw = chain[-1]
    index = page_identity_index or {}
    resolved_id = best_candidate_id(index.get(str(parent_raw).strip().lower(), []))
    if resolved_id:
        return [
            Edge(
                rel_type="PARENT_OF",
                target_label="Document",
                target_key="id",
                target_value=resolved_id,
                tag="EXTRACTED",
            )
        ]
    return [
        Edge(
            rel_type="PARENT_OF",
            target_label="UnresolvedPage",
            target_key="external_id",
            target_value=str(parent_raw),
            tag="EXTRACTED",
        )
    ]


def extract_structured_components(
    doc: dict, *, issue_frame: dict | None, checkitem: dict | None
) -> list[Edge]:
    """issue_frames.components[] 또는 checkitems.area 중 해당하는 쪽만 본다.
    checkitems.category/category_1/subcategory는 PISA 평가축(구성/운영/가용성/
    결함 및 오류/성능 및 용량)이라 컴포넌트가 아니다 — area만 쓴다."""
    values: list[str] = []
    if issue_frame:
        values.extend(issue_frame.get("components") or [])
    if checkitem and checkitem.get("area"):
        values.append(checkitem["area"])

    seen: set[str] = set()
    edges: list[Edge] = []
    for v in values:
        v = (v or "").strip()
        if not v or v in seen:
            continue
        seen.add(v)
        edges.append(
            Edge(
                rel_type="HAS_COMPONENT",
                target_label="Component",
                target_key="canonical_name",
                target_value=v,
                tag="EXTRACTED",
            )
        )
    return edges


_BUSINESS_ENTITY_TYPES = {"business_system", "platform"}


def extract_business_entities(doc: dict, *, document_entities: list[dict]) -> list[Edge]:
    """document_entities 미러. entities.type이 business_system/platform인 행만
    BusinessEntity로 — component/tech_term은 Component 쪽(Task 8)에서 다룬다."""
    edges: list[Edge] = []
    for row in document_entities:
        if row.get("entity_type") not in _BUSINESS_ENTITY_TYPES:
            continue
        edges.append(
            Edge(
                rel_type="MENTIONS_ENTITY",
                target_label="BusinessEntity",
                target_key="id",
                target_value=row["entity_id"],
                tag="EXTRACTED",
            )
        )
    return edges


_HANGUL_RE = re.compile(r"[가-힣]")


def extract_lexicon_components(doc: dict, *, lexicon_map: dict[str, list[str]]) -> list[Edge]:
    """app.lexicon.seed.load_lexicon_map()의 출력(소문자 변형어 -> [canonical,...])을
    그대로 입력받아 본문에서 매칭된 canonical들을 HAS_COMPONENT(INFERRED)로 낸다.

    G06: 과거엔 공백 없는 단일 토큰만 집합으로 비교해 "NetApp Cluster"/"SQL
    Server"처럼 여러 단어로 된 등록 구문이 절대 안 매칭됐다 — 이제 변형어 문자열
    자체로 본문을 검사한다. 한글 변형어는 조사가 붙어도("넷앱에서") 찾을 수 있게
    단순 포함 검사를 쓴다. ASCII 변형어는 \\b가 아니라 커스텀 경계를 쓴다 — \\b는
    `/`,`-`,`.`을 전부 경계로 보기 때문에, 과거 `_TOKEN_RE`(이 문자들을 토큰
    안에 포함시켜 비교)로는 절대 안 걸리던 "/sys/fs/cgroup"의 fs, "ceph-mds"의
    mds, "scp-pbupa02"의 scp가 전부 오탐으로 걸렸다(2차 리뷰에서 실제 코퍼스로
    재현). 새 경계는 좌우로 "영문/숫자/밑줄/슬래시/하이픈이 직접 붙어 있지 않고,
    점(.) 뒤에 영문/숫자/밑줄이 와서 식별자의 일부가 되는 경우도 아님"을
    요구한다 — 그 결과 과거 정밀도(경로/호스트명/점구분 키 안의 부분 문자열
    차단)는 되살리면서, 문장 끝의 "Redis." 나 조사 붙은 "Redis가"/"NetApp에서"는
    오른쪽에 한글이 와도 안 막히므로 여전히 매칭된다. 긴 등록 구문을 먼저 봐
    짧은 변형어가 같은 자리에서 중복 집계되는 것을 줄인다(완전한 상호배타는
    아님 — "NetApp"과 "NetApp Cluster"는 같은 본문에서 둘 다 나올 수 있고, 둘
    다 매칭되는 것이 맞는 동작이다)."""
    body = doc.get("body_md") or ""
    if not body:
        return []
    body_lower = body.lower()

    canonicals: set[str] = set()
    variants_sorted = sorted(
        ((variant, names[0]) for variant, names in lexicon_map.items() if names and variant),
        key=lambda item: -len(item[0]),
    )
    for variant, canonical in variants_sorted:
        if len(variant) == 1 and _HANGUL_RE.fullmatch(variant):
            # 한글 한 글자짜리 변형어("행")는 그래프 매칭에서 완전히 제외한다 —
            # 왼쪽 경계만 강제하는 걸로는 "행정"/"행사"/"행동"/"1행"(표의 행)처럼
            # 흔한 단어·단위 표현을 전혀 못 막는다(1차 리뷰에서 재현). seed에는
            # 그대로 두어 검색 FTS 확장에는 계속 쓰이고, hang 자체는 hung/soft
            # lockup/시스템 멈춤/"hang"(영문)으로도 여전히 매칭된다.
            continue
        if _HANGUL_RE.search(variant):
            # 왼쪽 경계만 강제: 바로 앞 글자가 한글이면 안 됨(예: "넷앱"이 다른
            # 한글 단어 안에 섞여 나오는 걸 막음) — 오른쪽은 열어 둬서 조사가
            # 붙은 꼴("넷앱에서")은 그대로 매칭되게 한다. 두 글자 이상이면 이
            # 정도 경계로 충분하다(한 글자짜리 오탐은 위에서 이미 걸러짐).
            if re.search(r"(?<![가-힣])" + re.escape(variant), body_lower):
                canonicals.add(canonical)
        else:
            pattern = (
                r"(?<![a-z0-9_/\-])(?<![a-z0-9_]\.)"
                + re.escape(variant)
                + r"(?![a-z0-9_/\-])(?!\.[a-z0-9_])"
            )
            if re.search(pattern, body_lower):
                canonicals.add(canonical)
    return [
        Edge(
            rel_type="HAS_COMPONENT",
            target_label="Component",
            target_key="canonical_name",
            target_value=c,
            tag="INFERRED",
        )
        for c in sorted(canonicals)
    ]


_TICKET_ID_RE = re.compile(r"CITECTS-\d+", re.IGNORECASE)


def extract_references(doc: dict, *, external_id_index: dict[str, str]) -> list[Edge]:
    """본문 내 CITECTS-#### 언급을 external_id_index로 해석해 REFERENCES로.
    해석 안 되는 ID(코퍼스 밖이거나 아직 동기화 안 됨)는 조용히 건너뛴다.
    문서 자신의 external_id(예: support_history 티켓이 본문에 자기 Issue Key를
    메타데이터로 포함하는 경우)는 EXCLUDE — extract_hierarchy가 ancestor_ids에서
    자기 id를 빼는 것과 같은 이유(apps/api/app/graph/extract.py:extract_hierarchy)."""
    body = doc.get("body_md") or ""
    if not body:
        return []
    self_key = (doc.get("external_id") or "").strip().lower()
    found = {m.group(0).lower() for m in _TICKET_ID_RE.finditer(body)}
    found.discard(self_key)
    target_ids = sorted({external_id_index[f] for f in found if f in external_id_index})
    return [
        Edge(
            rel_type="REFERENCES",
            target_label="Document",
            target_key="id",
            target_value=t,
            tag="EXTRACTED",
        )
        for t in target_ids
    ]
