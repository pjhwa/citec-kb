"""Seed core entities and link documents by title/alias match (PR-15)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, func, or_, select

from app.db.models import Document, DocumentEntity, Entity
from app.db.session import session_scope

logger = logging.getLogger("citec.entities")

_EMBEDDED: dict[str, Any] = {
    "entities": [
        {
            "id": "sys:monimo",
            "canonical_name": "모니모",
            "type": "business_system",
            "aliases": ["모니모", "monimo", "Monimo", "MONIMO"],
            "host_patterns": ["*monimo*"],
            "env_hints": ["csp", "scp"],
        },
        {
            "id": "sys:scp",
            "canonical_name": "SCP",
            "type": "platform",
            "aliases": ["SCP", "삼성클라우드"],
            "host_patterns": [],
            "env_hints": ["csp"],
        },
        {
            "id": "sys:redis",
            "canonical_name": "Redis",
            "type": "component",
            "aliases": ["Redis", "레디스", "redis"],
            "host_patterns": [],
            "env_hints": [],
        },
        {
            "id": "sys:oracle",
            "canonical_name": "Oracle",
            "type": "component",
            "aliases": ["Oracle", "오라클"],
            "host_patterns": [],
            "env_hints": [],
        },
        {
            "id": "sys:gro",
            "canonical_name": "GRO",
            "type": "tech_term",
            "aliases": ["GRO", "rx-gro-hw", "generic receive offload"],
            "host_patterns": [],
            "env_hints": [],
        },
    ]
}


def _seed_paths() -> list[Path]:
    here = Path(__file__).resolve()
    paths = [
        Path("/app/data/seeds/entities/core.json"),
        Path("data/seeds/entities/core.json"),
    ]
    for i, parent in enumerate(here.parents):
        if i > 6:
            break
        paths.append(parent / "data" / "seeds" / "entities" / "core.json")
    return paths


def load_entity_seed() -> dict[str, Any]:
    for p in _seed_paths():
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8"))
                data["_loaded_from"] = str(p)
                return data
        except OSError:
            continue
    out = dict(_EMBEDDED)
    out["_loaded_from"] = "embedded"
    return out


def seed_entities(*, replace: bool = False) -> dict[str, Any]:
    """Upsert entity rows from seed JSON."""
    data = load_entity_seed()
    items = data.get("entities") or []
    upserted = 0
    with session_scope() as session:
        if replace:
            session.execute(delete(DocumentEntity))
            session.execute(delete(Entity))
        for raw in items:
            eid = str(raw["id"])
            ent = session.get(Entity, eid)
            if ent is None:
                ent = Entity(id=eid)
                session.add(ent)
            ent.canonical_name = str(raw.get("canonical_name") or eid)
            ent.type = str(raw.get("type") or "business_system")
            ent.customer = raw.get("customer")
            ent.aliases = list(raw.get("aliases") or [])
            ent.host_patterns = list(raw.get("host_patterns") or [])
            ent.env_hints = list(raw.get("env_hints") or [])
            ent.metadata_ = dict(raw.get("metadata") or {})
            upserted += 1
        session.flush()
        total = session.scalar(select(func.count()).select_from(Entity)) or 0
    return {
        "upserted": upserted,
        "entities_total": int(total),
        "loaded_from": data.get("_loaded_from"),
    }


def seed_document_links(
    *,
    entity_ids: Optional[list[str]] = None,
    source_type: Optional[str] = "support_history",
    limit_per_entity: int = 5000,
) -> dict[str, Any]:
    """Link documents whose title matches entity aliases (ILIKE)."""
    with session_scope() as session:
        stmt = select(Entity)
        if entity_ids:
            stmt = stmt.where(Entity.id.in_(entity_ids))
        entities = list(session.scalars(stmt).all())
        # materialize entity fields
        specs: list[tuple[str, list[str]]] = []
        for e in entities:
            aliases = list(e.aliases or [])
            if e.canonical_name and e.canonical_name not in aliases:
                aliases.insert(0, e.canonical_name)
            specs.append((e.id, [a for a in aliases if a and len(a) >= 2]))

        per_entity: dict[str, int] = {}
        links_added = 0
        for eid, aliases in specs:
            if not aliases:
                per_entity[eid] = 0
                continue
            clauses = []
            for a in aliases:
                like = f"%{a}%"
                clauses.append(Document.title.ilike(like))
                clauses.append(Document.external_id.ilike(like))
            q = (
                select(Document.id)
                .where(Document.status == "active")
                .where(or_(*clauses))
            )
            if source_type:
                q = q.where(Document.source_type == source_type)
            q = q.limit(limit_per_entity)
            doc_ids = list(session.scalars(q).all())
            n = 0
            for did in doc_ids:
                exists = session.scalar(
                    select(DocumentEntity.id)
                    .where(DocumentEntity.document_id == did)
                    .where(DocumentEntity.entity_id == eid)
                    .limit(1)
                )
                if exists:
                    continue
                session.add(
                    DocumentEntity(document_id=did, entity_id=eid, confidence=0.85)
                )
                n += 1
                links_added += 1
            per_entity[eid] = n

        session.flush()
        total_links = session.scalar(select(func.count()).select_from(DocumentEntity)) or 0

    return {
        "links_added": links_added,
        "links_total": int(total_links),
        "per_entity": per_entity,
        "source_type": source_type,
    }


def seed_all(*, replace: bool = False, link: bool = True) -> dict[str, Any]:
    ent = seed_entities(replace=replace)
    links = seed_document_links() if link else {"links_added": 0, "skipped": True}
    return {"entities": ent, "links": links}


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="Seed entities + document links")
    p.add_argument("--replace", action="store_true")
    p.add_argument("--no-link", action="store_true")
    args = p.parse_args()
    report = seed_all(replace=args.replace, link=not args.no_link)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
