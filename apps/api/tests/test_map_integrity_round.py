"""Unit tests for the confluence_map integrity round: subtree exclusion,
copy folding, total_candidates surfacing, include_irrelevant_maps override.
No DB — DB-backed cases live in test_map_integrity_round_db.py."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import app.retrieval.multi_query as mq_module
from app.confluence.map_sync import _write_map_page
from app.db.models import Document
from app.ingest.adapters import DocumentDraft, iter_confluence_map
from app.retrieval.multi_query import multi_hybrid_search
from app.retrieval.search import (
    SearchFilters,
    SearchHit,
    SearchRequest,
    SearchResponse,
    _apply_doc_filters,
    _include_irrelevant_maps,
    _map_copy_key,
    _normalize_map_title,
    collapse_map_copies,
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


# ── 2. copy folding ──────────────────────────────────────────────────


def test_normalize_strips_copy_prefixes_and_case():
    assert _normalize_map_title("사본 Multi-AZ 가용성 테스트") == "multi-az 가용성 테스트"
    assert _normalize_map_title("Copy of Multi-AZ 가용성 테스트") == "multi-az 가용성 테스트"
    assert _normalize_map_title("백업-Multi-AZ 가용성 테스트") == "multi-az 가용성 테스트"
    assert _normalize_map_title("백업 Multi-AZ 가용성 테스트") == "multi-az 가용성 테스트"
    assert _normalize_map_title("사본 사본  Multi-AZ 가용성 테스트") == "multi-az 가용성 테스트"
    # a word that merely starts with the prefix is not a copy marker
    assert _normalize_map_title("백업정책 점검 절차 문서") == "백업정책 점검 절차 문서"


def test_short_titles_and_other_sources_are_never_grouped():
    assert _map_copy_key("confluence_map", "LOOKIN", "회의록") is None
    assert _map_copy_key("confluence_map", "LOOKIN", "사본 회의록") is None
    assert _map_copy_key("tech_repo", "LOOKIN", "Multi-AZ 가용성 테스트") is None
    assert _map_copy_key("confluence_map", "LOOKIN", "Multi-AZ 가용성 테스트") is not None


def test_collapse_keeps_best_and_counts_copies():
    items = [
        ("a", "[성능] Ceph 스토리지 RBD 성능 테스트", "S"),
        ("b", "사본 [성능] Ceph 스토리지 RBD 성능 테스트", "S"),
        ("c", "회의록", "S"),
        ("d", "회의록", "S"),
        ("e", "Copy of [성능] Ceph 스토리지 RBD 성능 테스트", "S"),
        ("f", "[성능] Ceph 스토리지 RBD 성능 테스트", "OTHER"),
    ]
    kept, folded = collapse_map_copies(
        items, lambda i: _map_copy_key("confluence_map", i[2], i[1])
    )
    assert [i[0] for i in kept] == ["a", "c", "d", "f"]
    assert folded[id(kept[0])] == 2
    assert folded[id(kept[1])] == 0  # short "회의록" pair stays separate


def _hit(eid: str, title: str, score: float, **kw) -> SearchHit:
    base = dict(
        rank=1, score=score, document_id=f"d-{eid}", chunk_id=f"c-{eid}", title=title,
        snippet="", source_type="confluence_map", external_id=eid, evidence_grade="C",
        domain=None, environment=None, work_type=None, path_l2="LOOKIN", source_uri=None,
        fts_rank=None, vec_rank=None,
    )
    base.update(kw)
    return SearchHit(**base)


def _multi(monkeypatch, hits_per_query, filters=None):
    calls = iter(hits_per_query)

    def fake(session, req, *, query_vector=None):
        hits = next(calls)
        return SearchResponse(
            query=req.q, exact_tokens=[], total=len(hits), gated=False,
            trust_retrieval="strong", results=hits,
        )

    monkeypatch.setattr(mq_module, "hybrid_search", fake)
    monkeypatch.setattr(
        mq_module, "expand_queries", lambda q, extra=None, max_queries=6: [q, q + " x"]
    )
    req = SearchRequest(q="테스트", top_k=5, filters=filters or SearchFilters())
    return multi_hybrid_search(None, req, multi_query=True)[0]


def test_multi_query_folds_copies_surfaced_by_different_queries(monkeypatch):
    title = "Multi-AZ SCP 가용성 테스트 결과"
    resp = _multi(
        monkeypatch,
        [
            [_hit("1", title, 0.5), _hit("9", "다른 문서 제목 입니다 정말", 0.1)],
            [_hit("2", "사본 " + title, 0.4, duplicate_count=1)],
        ],
    )
    assert [h.external_id for h in resp.results] == ["1", "9"]
    assert resp.results[0].duplicate_count == 2  # copy 2 plus the 1 it had folded
    assert resp.total_candidates == 2


def test_multi_query_diversify_off_keeps_all(monkeypatch):
    title = "Multi-AZ SCP 가용성 테스트 결과"
    resp = _multi(
        monkeypatch,
        [[_hit("1", title, 0.5)], [_hit("2", "사본 " + title, 0.4)]],
        SearchFilters(diversify_copies=False),
    )
    assert [h.external_id for h in resp.results] == ["1", "2"]
    assert all(h.duplicate_count == 0 for h in resp.results)


# ── 4. include_irrelevant_maps resolution ────────────────────────────


def test_include_irrelevant_maps_auto_and_explicit_override():
    assert _include_irrelevant_maps(SearchFilters()) is False
    assert _include_irrelevant_maps(SearchFilters(source_type="tech_repo")) is False
    assert _include_irrelevant_maps(SearchFilters(source_type="confluence_map")) is True
    assert (
        _include_irrelevant_maps(
            SearchFilters(source_type="confluence_map", include_irrelevant_maps=False)
        )
        is False
    )
    assert _include_irrelevant_maps(SearchFilters(include_irrelevant_maps=True)) is True


def test_irrelevant_filter_sql_follows_resolution_and_does_not_mutate():
    f = SearchFilters(source_type="confluence_map", include_irrelevant_maps=False)
    assert "irrelevant" in _sql(f)
    auto = SearchFilters(source_type="confluence_map")
    assert "irrelevant" not in _sql(auto)
    assert auto.include_irrelevant_maps is None
    assert "irrelevant" in _sql(SearchFilters())
