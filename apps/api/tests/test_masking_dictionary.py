import json

import app.masking.dictionary as D


def _write(tmp_path, obj):
    (tmp_path / "masking_dictionary.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _use(monkeypatch, tmp_path):
    class S:
        config_dir = str(tmp_path)

    monkeypatch.setattr(D, "get_settings", lambda: S())
    D.reset_cache()


def test_missing_file_disables_biz_only(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path)
    d = D.get_dictionary()
    assert d.customer_re is None and d.host_res == () and d.status == "missing"


def test_loads_customers_hosts_allowlist(monkeypatch, tmp_path):
    _write(tmp_path, {"customers": ["가나전자", "ACME"], "hostname_patterns": [r"\b[a-z]{3}\d{2}web\d{2}\b"],
                      "allowlist": ["8.8.8.8"]})
    _use(monkeypatch, tmp_path)
    d = D.get_dictionary()
    assert d.status == "ok"
    assert d.customer_re.search("가나전자는") and d.customer_re.search("acme corp")
    assert not d.customer_re.search("MACMEN")            # ASCII 항목은 단어 경계
    assert d.host_res[0].search("abc01web02")
    assert "8.8.8.8" in d.allow


def test_broken_json_reports_error_without_raising(monkeypatch, tmp_path):
    (tmp_path / "masking_dictionary.json").write_text("{not json", encoding="utf-8")
    _use(monkeypatch, tmp_path)
    d = D.get_dictionary()
    assert d.status.startswith("error") and d.customer_re is None


def test_reloads_when_file_changes(monkeypatch, tmp_path):
    _write(tmp_path, {"customers": ["가나전자"]})
    _use(monkeypatch, tmp_path)
    assert D.get_dictionary().customer_re.search("가나전자")
    _write(tmp_path, {"customers": ["다라물산"]})
    d = D.get_dictionary()
    assert d.customer_re.search("다라물산") and not d.customer_re.search("가나전자")
