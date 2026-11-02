import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.evalv2 import sheet


def _judged():
    return {"run_id": "r", "model": "m", "queries": [{
        "id": "q1", "q": "질의 </script><b>x</b>", "slice": "real", "source": "real",
        "candidates": [{
            "external_id": "DOC-1", "title": "<img src=x onerror=alert(1)>", "source_type": "support_history",
            "evidence_eligible": False, "arms": {"mq_on": 1},
            "llm": {"grade": 2, "evidence": "근거 </script> 문장", "status": "ok"},
        }],
    }]}


def _data(html):
    m = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S)
    return json.loads(m.group(1).replace("<\\/", "</"))


def test_html_embeds_data_safely_and_has_no_external_resources():
    html = sheet.render(_judged(), doc_base="")
    assert html.count("</script>") == html.count("<script")          # data cannot close the script tag
    assert "http://" not in html.replace("http://localhost", "") and "https://" not in html
    data = _data(html)
    assert data["queries"][0]["id"] == "q1"
    assert "innerHTML" not in html                                     # all values go through textContent


def test_html_contains_export_and_marks():
    html = sheet.render(_judged(), doc_base="http://localhost:8573")
    for token in ("O", "△", "X", "review-labels.json", "reviewer", "reviewed_on"):
        assert token in html


def test_doc_base_scheme_is_restricted_to_http_https():
    assert _data(sheet.render(_judged(), doc_base="http://localhost:8573"))["doc_base"] == "http://localhost:8573"
    assert _data(sheet.render(_judged(), doc_base="https://kb.example"))["doc_base"] == "https://kb.example"
    for bad in ("javascript:alert(1)", "data:text/html,x", "  javascript:alert(1)", "//evil.example", "ftp://x"):
        assert _data(sheet.render(_judged(), doc_base=bad))["doc_base"] == ""
    assert "encodeURIComponent" in sheet.render(_judged())            # JS re-checks the scheme too
    assert "^https?:" in sheet.render(_judged())


def test_triangle_css_escape_matches_class_name():
    html = sheet.render(_judged())
    assert "△" == "△" and "on-\\25B3" in html                      # U+25B3 WHITE UP-POINTING TRIANGLE


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_script_is_valid_javascript():
    html = sheet.render(_judged(), doc_base="")
    scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", html, re.S)
    js = [s for s in scripts if "application/json" not in s and s.strip() and not s.lstrip().startswith("{")]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.js"
        p.write_text("\n".join(js), encoding="utf-8")
        r = subprocess.run(["node", "--check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
