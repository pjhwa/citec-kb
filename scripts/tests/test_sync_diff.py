import gzip
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sync_diff import compute_diff, read_manifest, write_diff_files


def _write_manifest(path: Path, rows: list[tuple[str, str, str]]) -> None:
    with gzip.open(path, "wt", newline="") as f:
        for tbl, doc_id, h in rows:
            f.write(f"{tbl}\t{doc_id}\t{h}\n")


def test_compute_diff_new_id_included():
    dev = {"documents": {}}
    ops = {"documents": {"doc-1": "hash-a"}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == ["doc-1"]


def test_compute_diff_changed_hash_included():
    dev = {"documents": {"doc-1": "hash-old"}}
    ops = {"documents": {"doc-1": "hash-new"}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == ["doc-1"]


def test_compute_diff_unchanged_excluded():
    dev = {"documents": {"doc-1": "hash-a"}}
    ops = {"documents": {"doc-1": "hash-a"}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == []


def test_compute_diff_empty_dev_manifest_is_full_export():
    dev = {"documents": {}}
    ops = {"documents": {"doc-1": "h1", "doc-2": "h2"}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == ["doc-1", "doc-2"]


def test_compute_diff_ignores_ids_missing_from_ops():
    # dev has doc-1 that ops no longer has (deleted on ops) — must be ignored, not reported.
    dev = {"documents": {"doc-1": "hash-a"}}
    ops = {"documents": {}}
    diff = compute_diff(dev, ops)
    assert diff["documents"] == []


def test_compute_diff_covers_raw_files_namespace():
    # raw_files is just another "table" to this module — same diff semantics.
    dev = {"raw_files": {"checkitems/a.json": "hash-a"}}
    ops = {"raw_files": {"checkitems/a.json": "hash-a", "checkitems/b.json": "hash-b"}}
    diff = compute_diff(dev, ops)
    assert diff["raw_files"] == ["checkitems/b.json"]


def test_read_manifest_roundtrip(tmp_path):
    path = tmp_path / "manifest.tsv.gz"
    _write_manifest(
        path,
        [
            ("documents", "doc-1", "hash-a"),
            ("documents", "doc-2", "hash-b"),
            ("chunks", "chunk-1", "hash-c"),
        ],
    )
    manifest = read_manifest(path)
    assert manifest["documents"] == {"doc-1": "hash-a", "doc-2": "hash-b"}
    assert manifest["chunks"] == {"chunk-1": "hash-c"}
    assert manifest["checkitems"] == {}


def test_write_diff_files_only_for_changed_tables(tmp_path):
    diff = {"documents": ["doc-1"], "chunks": []}
    counts = write_diff_files(diff, tmp_path)
    assert counts == {"documents": 1, "chunks": 0}
    assert (tmp_path / "documents.ids").read_text() == "doc-1\n"
    assert not (tmp_path / "chunks.ids").exists()


def test_cli_writes_counts_json(tmp_path):
    dev_path = tmp_path / "dev.tsv.gz"
    ops_path = tmp_path / "ops.tsv.gz"
    _write_manifest(dev_path, [("documents", "doc-1", "hash-a")])
    _write_manifest(ops_path, [("documents", "doc-1", "hash-a"), ("documents", "doc-2", "hash-b")])
    out_dir = tmp_path / "diff"

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parent.parent / "sync_diff.py"),
            "--dev-manifest", str(dev_path),
            "--ops-manifest", str(ops_path),
            "--out-dir", str(out_dir),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    counts = json.loads(result.stdout)
    assert counts["documents"] == 1
    assert (out_dir / "documents.ids").read_text().strip() == "doc-2"
