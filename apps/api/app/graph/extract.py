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


def extract_hierarchy(doc: dict) -> list[Edge]:
    """metadata["ancestor_ids"] -> PARENT_OF(ancestor -> doc) for each ancestor.

    adapters.py builds ancestor_ids as [root, ..., parent, self] (see
    apps/api/app/confluence/map_sync.py build_frontmatter_confluence_map and
    apps/api/app/ingest/adapters.py:233-237/294-298/351-355) — the last
    element is the page's own id, which this function drops.
    """
    ancestor_ids = (doc.get("metadata") or {}).get("ancestor_ids") or []
    chain = [a for a in ancestor_ids if a != doc["id"]]
    return [
        Edge(
            rel_type="PARENT_OF",
            target_label="Document",
            target_key="id",
            target_value=ancestor_id,
            tag="EXTRACTED",
        )
        for ancestor_id in chain
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
_TOKEN_RE = re.compile(r"[A-Za-z가-힣0-9_/\-\.]+")


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


def extract_lexicon_components(doc: dict, *, lexicon_map: dict[str, list[str]]) -> list[Edge]:
    """app.lexicon.seed.load_lexicon_map()의 출력(토큰 소문자 -> [canonical,...])을
    그대로 입력받아 본문에서 매칭된 canonical들을 HAS_COMPONENT(INFERRED)로 낸다."""
    body = doc.get("body_md") or ""
    if not body:
        return []
    tokens = {t.lower() for t in _TOKEN_RE.findall(body)}
    canonicals: set[str] = set()
    for tok in tokens:
        variants = lexicon_map.get(tok)
        if variants:
            canonicals.add(variants[0])  # load_lexicon_map()의 variants[0] == canonical
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
