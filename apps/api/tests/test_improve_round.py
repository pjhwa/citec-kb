"""Regression tests for the 2026-09-23 comparison-verification fixes."""

from datetime import date
from pathlib import Path

from app.confluence.map_sync import (
    _write_map_page,
    classify_map_tech,
    excerpt_from_storage,
)
from app.query.planner import plan_query, route_query
from app.query.time_range import parse_absolute_range, parse_relative_range


def test_absolute_iso_range_is_inclusive_and_beats_year_analytics():
    text = "2026-09-07부터 2026-09-13까지 생성된 CITECTS 지원건"
    dr = parse_absolute_range(text)
    assert dr is not None
    assert dr.date_from == date(2026, 9, 7)
    assert dr.date_to == date(2026, 9, 13)
    plan = plan_query(text)
    assert plan["intent"] == "time_scoped_list"
    assert plan["date_from"] == "2026-09-07"
    assert plan["date_to"] == "2026-09-13"
    assert plan["source_type"] == "support_history"


def test_absolute_range_variants():
    a = parse_relative_range("2026-09-07~2026-09-13 지원건")
    b = parse_absolute_range("2026.09.07 - 2026.09.13")
    c = parse_absolute_range("2026년 9월 7일부터 2026년 9월 13일까지 지원건")
    assert a is not None and a.date_from == date(2026, 9, 7) and a.date_to == date(2026, 9, 13)
    assert b is not None and b.date_from == date(2026, 9, 7)
    assert c is not None and c.date_to == date(2026, 9, 13)


def test_unparsed_date_is_flagged_not_silently_unfiltered():
    out = route_query("2026-09-07부터 어제까지 생성된 지원건", execute=False)
    assert out["intent"] != "time_scoped_list"
    assert out.get("date_parse") == "unrecognized"
    assert "날짜" in (out.get("note") or "")


def test_excerpt_is_tag_stripped_and_capped():
    html = "<p>" + ("가" * 800) + "</p><script>x</script>"
    text = excerpt_from_storage(html)
    assert "<" not in text
    assert len(text) <= 401
    assert text.endswith("…")


def test_map_page_writes_excerpt_and_does_not_call_pnl_technical(tmp_path: Path):
    meta = {
        "id": "2525893483",
        "title": "2026 팀 손익 KPI",
        "version": {"when": "2026-09-07T15:59:11.000+09:00"},
        "ancestors": [],
        "body": {"storage": {"value": "<p>매출액과 근태만 있는 페이지입니다.</p>"}},
    }
    written = _write_map_page(
        meta=meta,
        root_label="root",
        space_key="LOOKIN",
        space_name="LOOKIN",
        base_url="https://c.example.com",
        tz_name="Asia/Seoul",
        raw_dir=tmp_path,
    )
    body = written.path.read_text(encoding="utf-8")
    assert "tech_relevant : irrelevant" in body
    assert "매출액과 근태" in body
    assert len(body) < 2000
    label, domains = classify_map_tech("2026 팀 손익 KPI", "매출액과 근태만 있는 페이지입니다.")
    assert label == "irrelevant"
    assert domains == []


def test_empty_citec_tags_stay_unknown_not_irrelevant():
    label, domains = classify_map_tech(
        "Optimizing Network I/O Virtualization",
        "Xen interrupt coalescing and virtual receive side scaling.",
    )
    assert label == "unknown"
    assert domains == []


def test_openshift_title_is_not_auto_irrelevant():
    label, _domains = classify_map_tech("OpenShift Virtualization", "KubeVirt notes")
    assert label != "irrelevant"
