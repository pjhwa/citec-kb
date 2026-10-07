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
