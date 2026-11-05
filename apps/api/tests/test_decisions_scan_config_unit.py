import json
from types import SimpleNamespace

import pytest

from app.decisions import scan, scan_config


@pytest.fixture()
def dirs(tmp_path, monkeypatch):
    cfg, data = tmp_path / "config", tmp_path / "data"
    cfg.mkdir(); data.mkdir()
    monkeypatch.setattr(scan_config, "get_settings", lambda: SimpleNamespace(config_dir=cfg, data_dir=data))
    return cfg, data


def test_defaults_then_config_then_override(dirs):
    cfg, data = dirs
    assert scan_config.effective(scan.DEFAULTS)["max_new_per_run"] == 20
    (cfg / "decision_scan.json").write_text(json.dumps({"max_new_per_run": 5}))
    assert scan_config.effective(scan.DEFAULTS)["max_new_per_run"] == 5
    snap = scan_config.update({"llm_refine": True, "llm_max_per_run": 3}, scan.DEFAULTS, "admin1")
    assert snap["source"] == "override" and snap["updated_by"] == "admin1"
    eff = scan_config.effective(scan.DEFAULTS)
    assert eff["llm_refine"] is True and eff["llm_max_per_run"] == 3 and eff["max_new_per_run"] == 5
    assert "updated_by" not in eff  # metadata never leaks into the scan config


@pytest.mark.parametrize("patch", [{"nope": 1}, {"max_new_per_run": -1}, {"max_new_per_run": True},
                                   {"llm_refine": "yes"}, {"detectors": ["x"]}, {"detectors": "deadline"}])
def test_rejects_bad_values(dirs, patch):
    with pytest.raises(scan_config.ScanConfigError):
        scan_config.update(patch, scan.DEFAULTS, "a")


def test_detectors_normalised_and_broken_override_ignored(dirs):
    _, data = dirs
    scan_config.update({"detectors": ["deadline", "fb_confirm"]}, scan.DEFAULTS, "a")
    assert scan_config.effective(scan.DEFAULTS)["detectors"] == ["fb_confirm", "deadline"]
    (data / "decision_scan.json").write_text("{broken")
    assert scan_config.effective(scan.DEFAULTS)["max_new_per_run"] == 20
