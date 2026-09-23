"""Backfill orchestration: one lock, ingest once, embed until pending is 0."""

from pathlib import Path

from app.confluence.map_backfill import (
    _source_done,
    load_state,
    ordered_source_ids,
    run_backfill,
    save_state,
)


def test_small_spaces_go_first():
    defs = {
        "confluence_map_lookin": {"space_key": "LOOKIN"},
        "confluence_map_spc": {"space_key": "SPC"},
    }
    order = ordered_source_ids(defs, {"LOOKIN": 15000, "SPC": 2})
    assert order == ["confluence_map_spc", "confluence_map_lookin"]


def test_resume_skips_a_clean_source(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    def fake_locked(source_id, raw_root, **kwargs):
        calls.append(source_id)
        assert kwargs["ingest"] is False
        return {"written": 1, "errors": [], "archived": [], "archive_skipped_due_to_errors": False}

    monkeypatch.setattr("app.confluence.map_backfill.get_source_defs", lambda active_only=True: {
        "confluence_map_spc": {"space_key": "SPC"},
        "confluence_map_guid": {"space_key": "GUID"},
    })
    monkeypatch.setattr("app.confluence.map_backfill._doc_counts", lambda: {"SPC": 2, "GUID": 1})
    monkeypatch.setattr("app.confluence.map_backfill._run_map_inventory_locked", fake_locked)
    monkeypatch.setattr("app.confluence.map_backfill._sync_run_lock", _acquired)
    monkeypatch.setattr("app.confluence.map_backfill._set_progress", lambda **kwargs: None)
    monkeypatch.setattr("app.confluence.map_backfill.count_pending_chunks", lambda **kwargs: 0)

    class _Settings:
        confluence_rate_limit_rps = 0.3

    monkeypatch.setattr("app.settings.get_settings", lambda: _Settings())
    monkeypatch.setattr(
        "app.confluence.client.ConfluenceClient",
        lambda settings: object(),
    )

    ingest_calls: list[list[str]] = []

    def fake_ingest(raw, sources):
        ingest_calls.append(list(sources))
        return {"inserted": 0, "updated": 1, "skipped": 0, "errors": 0}

    monkeypatch.setattr("app.ingest.pipeline.run_ingest", fake_ingest)
    monkeypatch.setattr(
        "app.embed.job.embed_pending_chunks",
        lambda **kwargs: {"embedded": 0, "errors": 0, "elapsed_sec": 0},
    )

    save_state(tmp_path, {"sources": {"confluence_map_spc": {"status": "crawled", "error_count": 0}}, "ingest": None})
    report = run_backfill(tmp_path, resume=True)
    assert calls == ["confluence_map_guid"]
    assert report["sources"]["confluence_map_spc"]["skipped"] is True
    assert ingest_calls == [["confluence_map"]]
    assert report["ok"] is True
    assert _source_done(load_state(tmp_path), "confluence_map_guid")


def test_embed_retries_until_pending_is_zero(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.confluence.map_backfill.get_source_defs", lambda active_only=True: {
        "confluence_map_spc": {"space_key": "SPC"},
    })
    monkeypatch.setattr("app.confluence.map_backfill._doc_counts", lambda: {"SPC": 1})
    monkeypatch.setattr(
        "app.confluence.map_backfill._run_map_inventory_locked",
        lambda *a, **k: {"written": 1, "errors": [], "archived": [], "archive_skipped_due_to_errors": False},
    )
    monkeypatch.setattr("app.confluence.map_backfill._sync_run_lock", _acquired)
    monkeypatch.setattr("app.confluence.map_backfill._set_progress", lambda **kwargs: None)
    pending = {"n": 2}

    def fake_count(**kwargs):
        return pending["n"]

    def fake_embed(**kwargs):
        assert kwargs["source_type"] == "confluence_map"
        pending["n"] = 0
        return {"embedded": 2, "errors": 0, "elapsed_sec": 1}

    monkeypatch.setattr("app.confluence.map_backfill.count_pending_chunks", fake_count)
    monkeypatch.setattr("app.ingest.pipeline.run_ingest", lambda raw, sources: {"inserted": 0, "updated": 1, "skipped": 0, "errors": 0})
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", fake_embed)
    monkeypatch.setattr("app.settings.get_settings", lambda: type("S", (), {"confluence_rate_limit_rps": 0.3})())
    monkeypatch.setattr("app.confluence.client.ConfluenceClient", lambda settings: object())

    report = run_backfill(tmp_path, resume=False)
    assert report["embed"]["pending"] == 0
    assert report["embed"]["status"] == "done"
    assert report["ok"] is True


def test_a_failed_source_does_not_stop_the_batch_and_pages_are_retried_after(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    def fake_locked(source_id, raw_root, **kwargs):
        calls.append(source_id)
        if source_id == "confluence_map_spc":
            raise RuntimeError("boom")
        return {
            "written": 1,
            "errors": [{"page_id": "9", "root_id": "1", "error": "timeout"}],
            "archived": [],
            "archive_skipped_due_to_errors": False,
        }

    retries: list[int] = []

    def fake_retry(client, failures, raw_dir, settings):
        retries.append(len(failures))
        return []

    monkeypatch.setattr("app.confluence.map_backfill.get_source_defs", lambda active_only=True: {
        "confluence_map_spc": {"space_key": "SPC", "roots": {"1": "root"}},
        "confluence_map_guid": {"space_key": "GUID", "roots": {}},
    })
    monkeypatch.setattr("app.confluence.map_backfill._doc_counts", lambda: {"SPC": 1, "GUID": 2})
    monkeypatch.setattr("app.confluence.map_backfill._run_map_inventory_locked", fake_locked)
    monkeypatch.setattr("app.confluence.map_backfill.retry_failures", fake_retry)
    monkeypatch.setattr("app.confluence.map_backfill._sync_run_lock", _acquired)
    monkeypatch.setattr("app.confluence.map_backfill._set_progress", lambda **kwargs: None)
    monkeypatch.setattr("app.confluence.map_backfill.count_pending_chunks", lambda **kwargs: 0)
    monkeypatch.setattr("app.ingest.pipeline.run_ingest", lambda raw, sources: {"inserted": 1, "updated": 0, "skipped": 0, "errors": 0})
    monkeypatch.setattr("app.embed.job.embed_pending_chunks", lambda **kwargs: {"embedded": 0, "errors": 0, "elapsed_sec": 0})
    monkeypatch.setattr("app.settings.get_settings", lambda: type("S", (), {"confluence_rate_limit_rps": 0.3, "confluence_timezone": "Asia/Seoul"})())
    monkeypatch.setattr("app.confluence.client.ConfluenceClient", lambda settings: object())

    report = run_backfill(tmp_path, resume=False)
    assert calls.count("confluence_map_guid") == 1
    assert calls.count("confluence_map_spc") == 4  # first pass + 3 retries
    assert retries == [1]
    assert report["embed"]["status"] == "done"
    assert report["sources"]["confluence_map_spc"]["attempts"] == 3
    assert load_state(tmp_path)["phase"] == "done"


def test_dry_run_does_not_crawl(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("app.confluence.map_backfill.get_source_defs", lambda active_only=True: {
        "confluence_map_spc": {"space_key": "SPC"},
    })
    monkeypatch.setattr("app.confluence.map_backfill._doc_counts", lambda: {"SPC": 2})
    monkeypatch.setattr("app.settings.get_settings", lambda: type("S", (), {"confluence_rate_limit_rps": 0.3})())

    def boom(*args, **kwargs):
        raise AssertionError("crawl should not run")

    monkeypatch.setattr("app.confluence.map_backfill._sync_run_lock", boom)
    report = run_backfill(tmp_path, dry_run=True)
    assert report["dry_run"] is True
    assert report["active_documents"] == 2
    assert report["plan"][0]["source_id"] == "confluence_map_spc"


class _acquired:
    def __enter__(self):
        return True

    def __exit__(self, *exc):
        return False

    def __call__(self):
        return self
