"""scripts/issue_system_token.py — issue/revoke without touching other entries."""

from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "issue_system_token.py"
_spec = importlib.util.spec_from_file_location("issue_system_token", _SCRIPT)
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def test_issue_revoke_roundtrip_preserves_existing(tmp_path, capsys):
    out = tmp_path / "auth.json"
    out.write_text(json.dumps({"note": "keep", "tokens": {"old": {"sub": "x", "name": "x", "roles": ["admin"]}}}))
    assert tool.main(["--name", "insight-ops@v1.4", "--out", str(out)]) == 0
    token = capsys.readouterr().out.strip()
    data = json.loads(out.read_text())
    assert data["note"] == "keep" and "old" in data["tokens"]
    assert data["tokens"][token] == {"sub": "insight-ops@v1.4", "name": "insight-ops@v1.4", "roles": ["author"]}
    assert len(token) >= 40

    assert tool.main(["--revoke", "insight-ops@v1.4", "--out", str(out)]) == 0
    data = json.loads(out.read_text())
    assert token not in data["tokens"] and "old" in data["tokens"]


def test_duplicate_name_needs_rotate_and_rotate_replaces(tmp_path, capsys):
    out = tmp_path / "auth.json"
    tool.main(["--name", "agent-room@v0.9", "--out", str(out)])
    first = capsys.readouterr().out.strip()
    with pytest.raises(SystemExit):
        tool.main(["--name", "agent-room@v0.9", "--out", str(out)])
    tool.main(["--name", "agent-room@v0.9", "--out", str(out), "--rotate"])
    second = capsys.readouterr().out.strip()
    tokens = json.loads(out.read_text())["tokens"]
    assert first not in tokens and second in tokens and len(tokens) == 1


def test_privileged_roles_blocked_by_default(tmp_path):
    out = tmp_path / "auth.json"
    with pytest.raises(SystemExit):
        tool.main(["--name", "someone", "--roles", "senior", "--out", str(out)])
    assert tool.main(["--name", "someone", "--roles", "senior", "--allow-privileged", "--out", str(out)]) == 0


def test_new_file_is_owner_only_and_loose_perm_warns(tmp_path, capsys):
    out = tmp_path / "auth.json"
    tool.main(["--name", "insight-ops@v1", "--out", str(out)])
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    out.chmod(0o644)
    capsys.readouterr()
    tool.main(["--name", "agent-room@v1", "--out", str(out)])
    assert "권한" in capsys.readouterr().err


def test_revoke_unknown_returns_1(tmp_path):
    out = tmp_path / "auth.json"
    out.write_text(json.dumps({"tokens": {}}))
    assert tool.main(["--revoke", "nobody@v1", "--out", str(out)]) == 1
