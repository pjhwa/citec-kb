"""Pure extractor functions: Document dict in -> list[Edge] out.

No DB, no Neo4j connection here — app.graph.pipeline supplies whatever
lookups each extractor needs (lexicon map, external_id index, …) as plain
arguments, so every function here stays independently unit-testable.
"""

from __future__ import annotations

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
