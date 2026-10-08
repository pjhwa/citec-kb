"""CRUD for graph_sync_state — tracks whether a document's Neo4j edges are
up to date with its current Postgres data (app.graph.hashing.compute_graph_hash)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.db.models import GraphSyncState
from app.db.session import session_scope


def get_state(document_id: str) -> Optional[dict[str, Any]]:
    with session_scope() as session:
        row = session.get(GraphSyncState, document_id)
        if row is None:
            return None
        return {
            "document_id": row.document_id,
            "input_hash": row.input_hash,
            "graph_extractor_version": row.graph_extractor_version,
            "synced_at": row.synced_at,
            "last_error": row.last_error,
        }


def mark_synced(document_id: str, *, input_hash: str, extractor_version: str) -> None:
    with session_scope() as session:
        row = session.get(GraphSyncState, document_id)
        if row is None:
            row = GraphSyncState(document_id=document_id)
            session.add(row)
        row.input_hash = input_hash
        row.graph_extractor_version = extractor_version
        row.synced_at = datetime.now(timezone.utc)
        row.last_error = None


def mark_failed(document_id: str, *, error: str) -> None:
    with session_scope() as session:
        row = session.get(GraphSyncState, document_id)
        if row is None:
            row = GraphSyncState(document_id=document_id)
            session.add(row)
        row.last_error = error[:2000]
