"""DOgether 내용 매칭 - DB 없는 단위 테스트(날짜 추출, 점수·판정, 그래프 연결)."""

from datetime import date

from app.graph.extract import Edge, extract_swim_links
from app.matching.dates import extract_incident_dates
from app.matching.dogether_swim import DEFAULTS, Group, date_score, judge, total_score

POSTED = date(2026, 11, 5)


def test_date_formats():
    for text, want in [
        ("- 시간 : 2026-09-16 08:00 ~ 2026-09-16 08:40", date(2026, 9, 16)),
        ("- 일시 : 2026.09.16 08:00", date(2026, 9, 16)),
        ("- 일시 : 2026년 9월 16일 08:00", date(2026, 9, 16)),
        ("- 발생 : ‘26. 9. 14", date(2026, 9, 14)),
        ("- 시간 : ‘26.9.14(일)", date(2026, 9, 14)),
        ("- 시간 : '26.9.14", date(2026, 9, 14)),
    ]:
        assert extract_incident_dates(text, POSTED) == ([want], "overview"), text


def test_overview_line_wins_and_post_date_line_is_ignored():
    body = (
        "[장애사례] 제목\n< ‘26. 11. 05 BCS그룹, No : 26091661376 >\n"
        "참고로 2026-08-01 점검이 있었다\n- 시간 : 2026-09-16 08:00\n"
    )
    assert extract_incident_dates(body, POSTED) == ([date(2026, 9, 16)], "overview")


def test_falls_back_to_body_without_post_date_line():
    body = "< ‘26. 11. 05 BCS >\n본문에 2026년 9월 14일 새벽 장애가 있었다"
    assert extract_incident_dates(body, POSTED) == ([date(2026, 9, 14)], "body")


def test_no_date_and_future_dates_dropped():
    assert extract_incident_dates("날짜 없음", POSTED) == ([], "none")
    assert extract_incident_dates("- 시간 : 2026-12-25", POSTED) == ([], "none")  # 게시일 이후
    assert extract_incident_dates("- 시간 : 2026-02-30", POSTED) == ([], "none")  # 없는 날짜


def test_multiple_dates_are_all_returned_sorted():
    ds, src = extract_incident_dates("- 일시 : 6월 16일\n- 일시 : 2026-07-03\n- 일시 : ‘26. 6. 16", POSTED)
    assert ds == [date(2026, 6, 16), date(2026, 7, 3)] and src == "overview"


def test_date_score_and_total():
    assert date_score(date(2026, 9, 16), date(2026, 9, 16), 1) == 1.0
    assert date_score(date(2026, 9, 16), date(2026, 9, 15), 1) == 0.7
    assert date_score(date(2026, 9, 16), date(2026, 9, 20), 1) == 0.0
    w = DEFAULTS["weights"]
    assert total_score({"date": 1, "similarity": 1, "components": 1, "domain": 1}, w) == 1.0
    assert total_score({}, w) == 0.0


def _g(scores, dated=True):
    return Group("2026-09-16", dated, [
        {"incident_document_id": f"i{n}", "score": s, "rank": n + 1} for n, s in enumerate(scores)
    ])


def test_judge_never_auto_by_default():
    cfg = dict(DEFAULTS)  # auto_enabled False, T1/M None
    assert judge(_g([0.9, 0.1]), cfg) == {"i0": "review", "i1": "review"}


def test_judge_auto_needs_enabled_thresholds_date_and_margin():
    cfg = dict(DEFAULTS, auto_enabled=True, T1=0.6, M=0.2)
    assert judge(_g([0.8, 0.5]), cfg) == {"i0": "auto"}
    assert set(judge(_g([0.8, 0.7]), cfg).values()) == {"review"}        # 2위와 차이 부족
    assert set(judge(_g([0.5, 0.1]), cfg).values()) == {"review"}        # T1 미달
    assert set(judge(_g([0.9], dated=False), cfg).values()) == {"review"}  # 날짜 없으면 auto 금지
    assert judge(_g([0.9]), dict(cfg, auto_enabled=False)) == {"i0": "review"}  # 스위치 꺼짐


def test_judge_t2_filters_and_empty():
    cfg = dict(DEFAULTS, T2=0.5)
    assert judge(_g([0.4]), cfg) == {}
    assert judge(_g([]), cfg) == {}


def _post(**meta):
    return {"id": "dogether_preventive:p", "external_id": "dogether-1", "source_type": "dogether_preventive", "metadata": meta}


def _idx(*pairs):
    return {ext: [{"document_id": did, "source_type": "incident_reports", "status": "active", "evidence_grade": "A"}]
            for ext, did in pairs}


def test_content_matches_tags_and_number_priority():
    idx = _idx(("111", "i1"), ("222", "i2"), ("333", "i3"))
    edges = extract_swim_links(
        _post(swim_ids=["111"]), incident_index=idx,
        content_matches=[{"external_id": "111", "status": "auto"},      # 번호 연결이 이긴다
                         {"external_id": "222", "status": "auto"},
                         {"external_id": "333", "status": "confirmed"},
                         {"external_id": "222", "status": "review"}],  # review는 무시
    )
    assert edges == [
        Edge("REFERENCES", "Document", "id", "i1", "EXTRACTED"),
        Edge("REFERENCES", "Document", "id", "i3", "EXTRACTED"),
        Edge("REFERENCES", "Document", "id", "i2", "INFERRED"),
    ]


def test_content_matches_resolve_only_incident_reports_and_skip_unknown():
    idx = {"222": [{"document_id": "s2", "source_type": "support_history", "status": "active"}]}
    assert extract_swim_links(_post(), incident_index=idx,
                              content_matches=[{"external_id": "222", "status": "auto"},
                                               {"external_id": "999", "status": "auto"}]) == []


def test_markdown_escaped_and_unicode_hyphen_dates():
    posted = date(2026, 7, 8)
    body = "\\- 시간 : 2026\\-04\\-30 19:17 \\~ 2026‑05‑01 01:42 (6시간)\n"
    assert extract_incident_dates(body, posted) == ([date(2026, 4, 30), date(2026, 5, 1)], "overview")
    assert extract_incident_dates("  - 시간 : 2026‑05‑26 10:50 \\~ 12:10", date(2026, 6, 23))[0] == [date(2026, 5, 26)]


def test_two_digit_year_only_on_overview_lines():
    posted = date(2026, 5, 13)
    assert extract_incident_dates("- 시간 : 26/04/14(수) 16:32\\~ 46", posted) == ([date(2026, 4, 14)], "overview")
    assert extract_incident_dates("- 시간 : 26.6.17(화) 14:51", date(2026, 7, 23))[0] == [date(2026, 6, 17)]
    # 개요 줄이 아닌 본문의 `26.6.17` 같은 버전 번호는 날짜로 보지 않는다
    assert extract_incident_dates("커널 버전 26.6.17 적용", posted) == ([], "none")


def test_dates_more_than_a_year_before_posting_are_dropped():
    posted = date(2026, 6, 26)
    assert extract_incident_dates("- 시간 : 2024-05-28 09:54", posted) == ([], "none")
    assert extract_incident_dates("- 시간 : 2025-07-01 09:54", posted)[0] == [date(2025, 7, 1)]
