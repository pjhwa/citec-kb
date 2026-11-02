import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "verify"))

import eval_retrieval_gate as g  # noqa: E402


def test_paired_bootstrap_known_cases():
    mean, lo, hi = g.paired_bootstrap([0.0] * 30, [1.0] * 30)
    assert mean == 1.0 and lo == 1.0 and hi == 1.0
    mean, lo, hi = g.paired_bootstrap([1.0, 0.0] * 20, [1.0, 0.0] * 20)
    assert mean == 0.0 and lo == 0.0 and hi == 0.0
    mean, lo, hi = g.paired_bootstrap([1, 0, 1, 0, 1, 0, 1, 0], [0, 1, 1, 0, 1, 0, 1, 1])
    assert lo < 0.0 < hi            # noisy flips: interval contains zero
    # deterministic
    assert g.paired_bootstrap([0, 1, 0, 1], [1, 1, 0, 0]) == g.paired_bootstrap([0, 1, 0, 1], [1, 1, 0, 0])


def test_paired_bootstrap_edge_cases():
    assert g.paired_bootstrap([0.0], [1.0]) == (1.0, 1.0, 1.0)
    assert g.paired_bootstrap([0.0] * 5, [0.0] * 5) == (0.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        g.paired_bootstrap([1.0, 0.0], [1.0])
    with pytest.raises(ValueError):
        g.paired_bootstrap([], [])


def _result(label, v2_ranks, plain_pass=True):
    cases = [{"id": f"v{i}", "group": "v2_real", "kind": "search", "rank": r, "pass": bool(r and r <= 10)}
             for i, r in enumerate(v2_ranks)]
    cases.append({"id": "plain-1", "group": "checklist_oom", "kind": "search", "rank": 1 if plain_pass else None, "pass": plain_pass})
    groups = {
        "v2_real": {"passed": sum(c["pass"] for c in cases if c["group"] == "v2_real"), "cases": len(v2_ranks),
                    "recall_at_10": None, "mrr": None, "hit_at_3": None, "p50_latency_ms": 1},
        "checklist_oom": {"passed": 1 if plain_pass else 0, "cases": 1, "hit_at_3": 1.0, "p50_latency_ms": 1},
    }
    return {"label": label, "git_sha": "x", "evalset": {"groups": groups, "cases": cases}}


def _compare(tmp_path, before, after):
    b, a = tmp_path / "b.json", tmp_path / "a.json"
    b.write_text(json.dumps(before)), a.write_text(json.dumps(after))
    import argparse
    return g.cmd_compare(argparse.Namespace(before=str(b), after=str(a)))


def test_v2_noise_flips_do_not_fail_the_gate(tmp_path, capsys):
    before = _result("b", [1, None, 2, None, 3, None, 4, None, 5, None] * 3)
    after = _result("a", [None, 1, 2, None, 3, None, 4, 1, 5, None] * 3)
    assert _compare(tmp_path, before, after) == 0
    assert "차이 불분명" in capsys.readouterr().out


def test_v2_significant_drop_fails_the_gate(tmp_path, capsys):
    before = _result("b", [1] * 30)
    after = _result("a", [None] * 25 + [1] * 5)
    assert _compare(tmp_path, before, after) == 1
    out = capsys.readouterr().out
    assert "유의한 하락" in out and "v2_real" in out


def test_v2_significant_gain_is_reported_and_passes(tmp_path, capsys):
    before = _result("b", [None] * 25 + [1] * 5)
    after = _result("a", [1] * 30)
    assert _compare(tmp_path, before, after) == 0
    assert "유의한 개선" in capsys.readouterr().out


def test_non_v2_regression_still_fails(tmp_path):
    before = _result("b", [1] * 12, plain_pass=True)
    after = _result("a", [1] * 12, plain_pass=False)
    assert _compare(tmp_path, before, after) == 1


def test_small_sample_gets_no_verdict(tmp_path, capsys):
    before = _result("b", [1, 1, 1])
    after = _result("a", [None, None, None])
    assert _compare(tmp_path, before, after) == 0
    assert "표본 부족" in capsys.readouterr().out


def test_v2_group_missing_in_before_is_skipped(tmp_path, capsys):
    before = _result("b", [])
    before["evalset"]["cases"] = [c for c in before["evalset"]["cases"] if c["group"] != "v2_real"]
    after = _result("a", [1] * 12)
    assert _compare(tmp_path, before, after) == 0
    assert "건너뜀" in capsys.readouterr().out
