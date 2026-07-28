import json

from app.ops.dashboard import (
    progress_row,
    read_raw_manifest,
    resource_snapshot,
    truncate_query_text,
)


def test_read_raw_manifest_missing_file(tmp_path):
    missing = tmp_path / "raw_manifest.json"
    assert read_raw_manifest(str(missing)) == {}


def test_read_raw_manifest_parses_source_files(tmp_path):
    manifest = tmp_path / "raw_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "sources": {
                    "support_history": {"files": 2280, "by_ext": {".md": 2280}},
                    "vendor_docs": {"files": 0, "by_ext": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    result = read_raw_manifest(str(manifest))
    assert result == {"support_history": 2280, "vendor_docs": 0}


def test_read_raw_manifest_malformed_json(tmp_path):
    manifest = tmp_path / "raw_manifest.json"
    manifest.write_text("not json", encoding="utf-8")
    assert read_raw_manifest(str(manifest)) == {}


def test_progress_row_computes_embed_pct():
    row = progress_row(
        raw_files=100,
        documents=100,
        chunks=500,
        chunks_active=500,
        embeddings=250,
    )
    assert row["raw_files"] == 100
    assert row["documents"] == 100
    assert row["chunks_active"] == 500
    assert row["embeddings"] == 250
    assert row["embed_pct"] == 50


def test_progress_row_zero_chunks_is_100_pct():
    row = progress_row(
        raw_files=0,
        documents=0,
        chunks=0,
        chunks_active=0,
        embeddings=0,
    )
    assert row["embed_pct"] == 100


def test_progress_row_raw_files_none_when_manifest_missing():
    row = progress_row(
        raw_files=None,
        documents=5,
        chunks=10,
        chunks_active=10,
        embeddings=10,
    )
    assert row["raw_files"] is None
    assert row["embed_pct"] == 100


def test_resource_snapshot_shape(tmp_path):
    snap = resource_snapshot(str(tmp_path))
    assert "process_rss_mb" in snap
    assert "load_avg" in snap
    assert "disk" in snap
    assert snap["disk"]["path"] == str(tmp_path)
    assert snap["disk"]["total_gb"] >= 0
    assert 0 <= snap["disk"]["pct"] <= 100


def test_resource_snapshot_bad_path_returns_disk_error():
    snap = resource_snapshot("/no/such/path/at/all")
    assert snap["disk"].get("error")


def test_truncate_query_text_short_unchanged():
    assert truncate_query_text("hello") == "hello"


def test_truncate_query_text_truncates_with_ellipsis():
    text = "a" * 200
    result = truncate_query_text(text, limit=120)
    assert len(result) == 121  # 120 chars + ellipsis
    assert result.endswith("…")


def test_truncate_query_text_none_becomes_empty():
    assert truncate_query_text(None) == ""
