"""Unit tests for the confluence_map integrity round: subtree exclusion,
copy folding, total_candidates surfacing, include_irrelevant_maps override.
No DB — DB-backed cases live in test_map_integrity_round_db.py."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.confluence.map_sync import _write_map_page
from app.db.models import Document
from app.ingest.adapters import DocumentDraft, iter_confluence_map
from app.retrieval.search import (
    SearchFilters,
    _apply_doc_filters,
)


def _sql(filters: SearchFilters) -> str:
    stmt = _apply_doc_filters(select(Document.id), filters)
    return str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


# ── 1. subtree exclusion ─────────────────────────────────────────────


def _page_meta(page_id: str, ancestors: list[dict]) -> dict:
    return {
        "id": page_id,
        "title": "OVE vs. Tanzu vs. OpenStack-Helm",
        "version": {"when": "2026-09-07T15:59:11.000+09:00"},
        "ancestors": ancestors,
        "body": {"storage": {"value": "<p>본문</p>"}},
    }


def test_write_map_page_stores_every_ancestor_id_and_own_id(tmp_path: Path):
    ancestors = [
        {"id": "2412781500", "title": "Home"},
        {"id": "2454694826", "title": "Reports"},
        {"id": "2525893483", "title": "Parent"},
    ]
    written = _write_map_page(
        meta=_page_meta("2553464543", ancestors),
        root_label="root",
        space_key="LOOKIN",
        space_name="LOOKIN",
        base_url="https://c.example.com",
        tz_name="Asia/Seoul",
        raw_dir=tmp_path,
    )
    assert "조상ID목록 : 2412781500,2454694826,2525893483,2553464543" in written.path.read_text(
        encoding="utf-8"
    )

    (draft,) = list(iter_confluence_map(tmp_path))
    assert draft.metadata["ancestor_ids"] == [
        "2412781500",
        "2454694826",
        "2525893483",
        "2553464543",
    ]
    assert "조상ID목록" not in draft.metadata


def test_ancestor_ids_do_not_change_content_hash():
    def draft(meta):
        return DocumentDraft(
            source_type="confluence_map", external_id="1", title="t", body_md="b", metadata=meta
        ).finalize()

    assert draft({"a": "1"}).content_hash == draft({"a": "1", "ancestor_ids": ["9", "1"]}).content_hash


def test_page_without_ancestors_still_gets_own_id(tmp_path: Path):
    _write_map_page(
        meta=_page_meta("77", []),
        root_label="r",
        space_key="S",
        space_name="S",
        base_url="https://c.example.com",
        tz_name="Asia/Seoul",
        raw_dir=tmp_path,
    )
    (draft,) = list(iter_confluence_map(tmp_path))
    assert draft.metadata["ancestor_ids"] == ["77"]


def test_exclude_subtree_ids_sql_matches_self_and_descendants_and_keeps_null_rows():
    sql = _sql(SearchFilters(exclude_subtree_ids=["2525893483"]))
    assert "documents.external_id NOT IN ('2525893483')" in sql
    assert "?|" in sql and "ancestor_ids" in sql
    # rows without ancestor_ids yield NULL for ?| — must not be dropped
    assert "coalesce" in sql.lower()


def test_exclude_page_ids_behaviour_unchanged():
    sql = _sql(SearchFilters(exclude_page_ids=["1"]))
    assert "documents.external_id NOT IN ('1')" in sql
    assert "ancestor_ids" not in sql
    assert "ancestor_ids" not in _sql(SearchFilters())
