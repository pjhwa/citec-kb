"""Weekly inventory rotation: picks a slice of active sources, wraps around,
advances persisted state only on a real (non-dry-run, non --source-ids) run."""

import json
from pathlib import Path

from app.confluence.map_inventory_cli import _pick_rotation, main


def test_pick_rotation_wraps_around():
    ids = ["a", "b", "c", "d", "e"]
    picked, next_index = _pick_rotation(ids, index=3, count=3)
    assert picked == ["d", "e", "a"]
    assert next_index == 1


def test_pick_rotation_count_exceeds_total():
    ids = ["a", "b"]
    picked, next_index = _pick_rotation(ids, index=0, count=5)
    assert picked == ["a", "b"]
    assert next_index == 0


def test_pick_rotation_empty():
    assert _pick_rotation([], index=0, count=3) == ([], 0)


def test_main_rotates_and_persists_state(tmp_path: Path, monkeypatch, capsys):
    raw_dir = tmp_path
    calls: list[str] = []

    monkeypatch.setattr(
        "app.confluence.map_sync.get_source_defs",
        lambda active_only=True: {
            "confluence_map_a": {}, "confluence_map_b": {}, "confluence_map_c": {},
            "confluence_map_d": {},
        },
    )

    def fake_inventory(source_id, raw_dir_arg, dry_run=False):
        calls.append(source_id)
        return {"source_id": source_id, "errors": [], "would_archive": [], "archived": []}

    monkeypatch.setattr("app.confluence.map_sync.run_map_inventory", fake_inventory)

    rc = main(["--raw-dir", str(raw_dir), "--count", "2"])
    assert rc == 0
    # sorted active ids: a, b, c, d — first run starts at index 0
    assert calls == ["confluence_map_a", "confluence_map_b"]

    state_path = raw_dir / "confluence_map" / ".inventory_rotation_state.json"
    state = json.loads(state_path.read_text())
    assert state["index"] == 2
    assert state["last_picked"] == ["confluence_map_a", "confluence_map_b"]

    # second run resumes where the first left off
    calls.clear()
    rc = main(["--raw-dir", str(raw_dir), "--count", "2"])
    assert rc == 0
    assert calls == ["confluence_map_c", "confluence_map_d"]
    state2 = json.loads(state_path.read_text())
    assert state2["index"] == 0  # wrapped back to the start


def test_main_explicit_source_ids_does_not_touch_rotation_state(tmp_path: Path, monkeypatch):
    raw_dir = tmp_path
    calls: list[str] = []

    monkeypatch.setattr(
        "app.confluence.map_sync.get_source_defs",
        lambda active_only=True: {"confluence_map_a": {}, "confluence_map_b": {}},
    )

    def fake_inventory(source_id, raw_dir_arg, dry_run=False):
        calls.append(source_id)
        return {"source_id": source_id, "errors": []}

    monkeypatch.setattr("app.confluence.map_sync.run_map_inventory", fake_inventory)

    rc = main(["--raw-dir", str(raw_dir), "--source-ids", "confluence_map_b"])
    assert rc == 0
    assert calls == ["confluence_map_b"]
    assert not (raw_dir / "confluence_map" / ".inventory_rotation_state.json").exists()


def test_main_dry_run_does_not_advance_state(tmp_path: Path, monkeypatch):
    raw_dir = tmp_path

    monkeypatch.setattr(
        "app.confluence.map_sync.get_source_defs",
        lambda active_only=True: {"confluence_map_a": {}, "confluence_map_b": {}},
    )
    monkeypatch.setattr(
        "app.confluence.map_sync.run_map_inventory",
        lambda source_id, raw_dir_arg, dry_run=False: {"source_id": source_id, "errors": [], "dry_run": dry_run},
    )

    rc = main(["--raw-dir", str(raw_dir), "--count", "1", "--dry-run"])
    assert rc == 0
    assert not (raw_dir / "confluence_map" / ".inventory_rotation_state.json").exists()


def test_main_unknown_source_id_errors(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "app.confluence.map_sync.get_source_defs",
        lambda active_only=True: {"confluence_map_a": {}},
    )
    rc = main(["--raw-dir", str(tmp_path), "--source-ids", "confluence_map_nope"])
    assert rc == 64


def test_main_counts_errors_for_exit_code(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "app.confluence.map_sync.get_source_defs",
        lambda active_only=True: {"confluence_map_a": {}},
    )
    monkeypatch.setattr(
        "app.confluence.map_sync.run_map_inventory",
        lambda source_id, raw_dir_arg, dry_run=False: {
            "source_id": source_id, "errors": [{"page_id": "1", "error": "401"}],
        },
    )
    rc = main(["--raw-dir", str(tmp_path), "--count", "1"])
    assert rc == 1
