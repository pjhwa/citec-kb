"""Pure-logic and mocked-HTTP tests for app.confluence.docs_inventory
(no DB, no live Confluence)."""

from __future__ import annotations

import asyncio

import httpx

from app.confluence.client import ConfluenceClient
from app.confluence.docs_inventory import (
    ARCHIVE_SUBDIR,
    DbPage,
    ListedPage,
    _list_roots,
    _move_raw_aside,
    listed_ancestor_ids,
    plan_reconcile,
)
from app.settings import Settings


def _lp(pid, anc, title="T", root="r1"):
    return ListedPage(page_id=pid, title=title, ancestor_ids=anc, root_id=root, root_label="L")


def _db(pid, anc, title="T"):
    return DbPage(external_id=pid, title=title, ancestor_ids=anc)


def test_listed_ancestor_ids_is_self_inclusive_and_none_without_key():
    assert listed_ancestor_ids({"ancestors": [{"id": "1"}, {"id": 2}]}, "9") == ["1", "2", "9"]
    assert listed_ancestor_ids({"ancestors": []}, "9") == ["9"]
    assert listed_ancestor_ids({}, "9") is None


def test_plan_detects_moved_renamed_new_gone_and_unchanged():
    listed = {
        "same": _lp("same", ["a", "same"]),
        "moved": _lp("moved", ["b", "moved"]),
        "renamed": _lp("renamed", ["a", "renamed"], title="New"),
        "new": _lp("new", ["a", "new"]),
    }
    db = {
        "same": _db("same", ["a", "same"]),
        "moved": _db("moved", ["a", "moved"]),
        "renamed": _db("renamed", ["a", "renamed"], title="Old"),
        "gone": _db("gone", ["a", "gone"]),
    }
    plan = plan_reconcile(listed, db)
    assert plan.fetch == {"moved": "moved", "renamed": "renamed", "new": "new"}
    assert plan.gone == ["gone"]
    assert plan.unchanged == 1


def test_plan_db_row_without_ancestors_is_refetched():
    plan = plan_reconcile({"p": _lp("p", ["a", "p"])}, {"p": _db("p", None)})
    assert plan.fetch == {"p": "moved"}


def test_plan_listing_without_ancestors_skips_unless_full_fetch_allowed():
    listed = {"p": _lp("p", None)}
    db = {"p": _db("p", ["a", "p"])}
    assert plan_reconcile(listed, db).fetch == {}
    assert plan_reconcile(listed, db).skipped_no_ancestors == ["p"]
    assert plan_reconcile(listed, db, allow_full_fetch=True).fetch == {"p": "no_ancestors_in_listing"}


def _client() -> ConfluenceClient:
    return ConfluenceClient(
        Settings(CONFLUENCE_BASE_URL="https://c.example.com", CONFLUENCE_USERNAME="u", CONFLUENCE_PASSWORD="p")
    )


def _patch(client, handler):
    client.bulk_client = lambda timeout=30.0: httpx.AsyncClient(  # type: ignore[method-assign]
        base_url=client._base_url, transport=httpx.MockTransport(handler)
    )


def test_list_roots_requests_ancestors_and_parses_them():
    client = _client()
    seen = []

    def handler(request):
        params = dict(request.url.params)
        seen.append(params)
        if int(params["start"]) > 0:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200, json={"results": [{"id": "10", "title": "A", "ancestors": [{"id": "r1"}]}]}
        )

    _patch(client, handler)
    listed, errors = asyncio.run(_list_roots(client, {"r1": "Root"}, rps=0))
    assert not errors
    assert seen[0]["expand"] == "ancestors"
    assert "lastmodified" not in seen[0]["cql"]  # full listing, not incremental
    assert listed["10"].ancestor_ids == ["r1", "10"]
    assert listed["10"].root_label == "Root"


def test_list_roots_search_failure_is_collected_not_raised():
    client = _client()
    _patch(client, lambda request: httpx.Response(401, json={"message": "no"}))
    listed, errors = asyncio.run(_list_roots(client, {"r1": "Root"}, rps=0))
    assert listed == {}
    assert errors and errors[0]["root_id"] == "r1" and errors[0]["page_id"] is None


def test_move_raw_aside_keeps_file_but_hides_it_from_the_adapter_glob(tmp_path):
    d = tmp_path / "confluence_docs"
    d.mkdir()
    (d / "confluence_1.md").write_text("x")
    (d / "confluence_2.md").write_text("y")
    assert _move_raw_aside(tmp_path, "confluence_docs", ["1", "404"]) == 1
    assert not (d / "confluence_1.md").exists()
    assert (d / ARCHIVE_SUBDIR / "confluence_1.md").read_text() == "x"
    assert sorted(p.name for p in d.glob("*.md")) == ["confluence_2.md"]
