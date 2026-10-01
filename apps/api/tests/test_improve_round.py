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


# --- P1-A / D07: a single incidental domain-keyword hit must not outweigh
# explicit non-tech markers (docs/CITEC_KB_RELIABILITY_PERFORMANCE_CLAUDE_PROMPT_20260930.md
# §8 "기술 무관 분류", REVIEW.md item 7).


def test_single_domain_hit_in_a_finance_doc_is_irrelevant():
    """The exact D07 shape: an HR/finance doc that only mentions '네트워크'
    once as an aside used to be tagged relevant purely on that one word."""
    label, domains = classify_map_tech(
        "팀 손익 관리 방안",
        "조직변경 및 손익 기준 변경. 기존 시스템 국내 + 네트워크 국/내외. 해외법인 재무관리.",
    )
    assert label == "irrelevant"
    assert domains == []


def test_two_or_more_domain_hits_still_win_over_a_non_tech_title():
    """A doc whose title itself has a non-tech marker, but whose excerpt has
    multiple concrete technical domain hits (e.g. a real network/K8s cost
    analysis), must not be suppressed — only a single weak hit is."""
    label, domains = classify_map_tech(
        "팀 손익 관리 방안",
        "Kubernetes POD와 Network 스위치 관련 기술 검토 결과 및 손익 영향도.",
    )
    assert label == "relevant"
    assert len(domains) >= 2


def test_non_tech_marker_only_in_excerpt_does_not_demote():
    """Narrowed on purpose (see classify_map_tech's docstring): a
    technically-titled page that only footnotes '매출액' etc. in the
    excerpt must not flip to irrelevant on a single domain hit — only an
    explicit non-tech *title* does that."""
    label, domains = classify_map_tech(
        "네트워크 장비 성능 분석",
        "Network 스위치 관련 기술 문서. 매출액 손실 방지를 위한 참고자료.",
    )
    assert label == "relevant"
    assert domains == ["Network"]


# --- Real-corpus validation (2026-10-01): the fix's blast radius could not
# be measured during the original implementation session — the locally
# available data/raw/confluence_map/ snapshot has zero rows with excerpt/
# tech_relevant populated (classification only ever runs against live
# Confluence body_storage). After the PR deployed, 5,000 real production
# confluence_map rows (title+excerpt+recorded tech_relevant) were pulled via
# scripts/collect_prod_diagnostics.sh and replayed through classify_map_tech
# old vs new. Result on the 4,934-row verified-reproducible subset: only 2
# flips (0.04%), both relevant→irrelevant, zero false negatives — both real
# titles were genuine org-process documents ("KPI 수립과정 ...", "... KPI
# 항목별 정리"), confirming the title-only narrowing is both safe and
# effective on the actual production distribution. See REPORT.md §1-E.
# These two pin that real-world finding as a permanent regression test
# (title only; the real excerpt content wasn't retained — a single generic
# domain-word mention is enough to reproduce the shape that mattered).


def test_real_prod_kpi_process_doc_is_irrelevant():
    label, _ = classify_map_tech(
        "【History】KPI 수립과정 (팀-사업부 혁신그룹-평가사무국)",
        "회의록: 금년도 KPI 수립 일정 및 담당자 안내. 관련 시스템은 Network 공유 드라이브에 게시.",
    )
    assert label == "irrelevant"


def test_real_prod_kpi_item_summary_is_irrelevant():
    label, _ = classify_map_tech(
        "3. 24년 KPI 항목별 정리",
        "부서별 KPI 항목 정리본. 공유 자료는 Network 드라이브 참고.",
    )
    assert label == "irrelevant"
