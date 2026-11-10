import importlib.util
import io
import json
from pathlib import Path

from app.masking import get_dictionary

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "masking_probe.py"
_spec = importlib.util.spec_from_file_location("masking_probe", _SCRIPT)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

PHONE = "010-1234-5678"
ADDR = "서울특별시 강남구 테헤란로 123"
IP = "10.20.30.40"

DOCS = [
    {"external_id": "d1", "title": "연락처", "body": f"연락처 {PHONE}\n주소 {ADDR}\n"},
    {"external_id": "d2", "title": "서버", "body": f"접속 서버 {IP} 입니다\n"},
    {"external_id": "d3", "title": "깨끗", "body": "아무 개인정보 없는 문장입니다.\n"},
]


def _scan(residual=True):
    return probe.scan_docs(DOCS, get_dictionary(), residual)


def test_counts_and_residual():
    r = _scan()
    assert r["docs_scanned"] == 3
    assert r["with_hits"] == 2
    assert r["totals"]["pii"] >= 2
    assert r["totals"]["net"] == 1
    per = {d["external_id"]: d for d in r["docs"]}
    assert per["d3"]["counts"] == {}
    assert r["residual_count"] == 0 and r["residual_ids"] == []


def test_no_raw_values_in_output(capsys):
    r = _scan()
    probe.print_report(r, summary_only=False)
    out = capsys.readouterr().out + json.dumps(r, ensure_ascii=False)
    for raw in (PHONE, ADDR, IP, "1234-5678", "테헤란로"):
        assert raw not in out


def test_summary_only_hides_doc_lines(capsys):
    probe.print_report(_scan(), summary_only=True)
    out = capsys.readouterr().out
    assert "d1" not in out and "docs scanned=3" in out


def test_stdin_jsonl_parse():
    lines = "\n".join(json.dumps(d, ensure_ascii=False) for d in DOCS) + "\n\n"
    assert probe.read_jsonl(io.StringIO(lines)) == DOCS


def test_title_hit_is_masked():
    r = probe.scan_docs([{"external_id": "t", "title": f"문의 {PHONE}", "body": "x"}], get_dictionary(), False)
    assert PHONE not in r["docs"][0]["title"]
