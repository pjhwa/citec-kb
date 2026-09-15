"""Unit tests for app.confluence.sync — pure functions only, no I/O.

Fixtures under tests/fixtures/confluence_sync/ are the only real Confluence
data available in this environment (dev system has no Confluence access —
see citec-kb_confluence_incremental_sync_prompt.md for why).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.confluence.sync import (
    CONFLUENCE_DOCS_ROOTS,
    TECHREPO_ROOTS,
    build_frontmatter_confluence_docs,
    build_frontmatter_tech_repo,
    clean_body,
    directory_breadcrumb,
    format_cursor,
    page_url,
    storage_html_to_text,
    version_date,
)
from app.ingest.adapters import iter_confluence_docs, iter_tech_repo

FIXTURES = Path(__file__).parent / "fixtures" / "confluence_sync"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


# --- storage_html_to_text / clean_body on the real page excerpt ---


def test_storage_html_to_text_strips_tags_and_macros_to_readable_text():
    page = _load("sample_confluence_docs_page.json")
    text = clean_body(storage_html_to_text(page["body_storage_html"]))
    # headings/paragraphs/table cell content survive as plain text
    assert "핵심 결론" in text
    assert "205" in text
    assert "신규 진단항목(확정)" in text
    # nested ac:structured-macro expand + ac:rich-text-body content survives
    assert "강북삼성병원 진단 수행 내용" in text
    # no raw tags/macro markup left over
    assert "<" not in text
    assert "ac:structured-macro" not in text
    assert "ri:attachment" not in text


def test_storage_html_to_text_is_human_readable_not_wall_of_spaces():
    page = _load("sample_confluence_docs_page.json")
    text = storage_html_to_text(page["body_storage_html"])
    # block-level tags become newlines, not lost entirely
    assert "\n" in text
    lines = [l for l in text.splitlines() if l.strip()]
    assert len(lines) >= 3


def test_clean_body_removes_cdata_tail_artifact():
    case = _load("sample_cdata_and_css_artifacts.json")["case_cdata_tail"]
    result = clean_body(case["input_after_tag_strip"])
    assert result == case["expected_after_clean_body"]


def test_clean_body_removes_orphan_inline_css_rule():
    case = _load("sample_cdata_and_css_artifacts.json")["case_orphan_inline_css"]
    result = clean_body(case["input_after_tag_strip"])
    assert result == case["expected_after_clean_body"]


# --- cursor formatting (CQL date format + KST conversion) ---


def test_format_cursor_renders_confluence_cql_date_format():
    dt = datetime(2026, 9, 7, 6, 59, 11, tzinfo=timezone.utc)  # 15:59 KST
    assert format_cursor(dt, "Asia/Seoul") == "2026/09/07 15:59"


def test_format_cursor_converts_naive_datetime_as_utc():
    dt = datetime(2026, 9, 7, 6, 59, 11)  # naive, treated as UTC
    assert format_cursor(dt, "Asia/Seoul") == "2026/09/07 15:59"


def test_format_cursor_kst_offset_matters_across_midnight():
    # 2026-01-01 16:00 UTC = 2026-01-02 01:00 KST — a naive UTC cursor would
    # wrongly stay on 01-01 and re-scan a day's worth of edits every run.
    dt = datetime(2026, 1, 1, 16, 0, 0, tzinfo=timezone.utc)
    assert format_cursor(dt, "Asia/Seoul") == "2026/01/02 01:00"


# --- version.when → YYYY-MM-DD (최종수정일) ---


def test_version_date_extracts_calendar_date_in_source_tz():
    page = _load("sample_confluence_docs_page.json")
    assert version_date(page["version"]["when"]) == "2026-09-07"


# --- page_url ---


def test_page_url_matches_real_fixture_url_shape():
    assert (
        page_url("https://devops.sdsdev.co.kr/confluence", "2510261901")
        == "https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=2510261901"
    )


def test_page_url_strips_trailing_slash_on_base():
    assert (
        page_url("https://devops.sdsdev.co.kr/confluence/", "123")
        == "https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=123"
    )


# --- directory breadcrumb (tech_repo) ---


def test_directory_breadcrumb_joins_ancestors_and_own_title():
    ancestors = [
        {"id": "1", "title": "[CI-TEC] 테크리포(Tech-Repository) Home"},
        {"id": "2", "title": "클라우드 운영 기술"},
        {"id": "3", "title": "★★분야별 기술 자료★★"},
        {"id": "4", "title": "운영체제"},
        {"id": "5", "title": "1. OS - Linux"},
        {"id": "6", "title": "1-3. 메모리 관리"},
    ]
    assert directory_breadcrumb(ancestors, "메모리 관련 커널 파라미터") == (
        "[CI-TEC] 테크리포(Tech-Repository) Home > 클라우드 운영 기술 > "
        "★★분야별 기술 자료★★ > 운영체제 > 1. OS - Linux > 1-3. 메모리 관리 > "
        "메모리 관련 커널 파라미터"
    )


def test_directory_breadcrumb_trailing_separator_when_title_blank():
    # matches real corpus data: pages with an unresolved/blank title leave
    # the breadcrumb ending in "> " with nothing after it.
    ancestors = [{"id": "1", "title": "Home"}, {"id": "2", "title": "VMware 관련"}]
    assert directory_breadcrumb(ancestors, "") == "Home > VMware 관련 > "


# --- roots constants match the confirmed values from the request prompt ---


def test_confluence_docs_roots_match_confirmed_values():
    assert CONFLUENCE_DOCS_ROOTS == {
        "222532692": "CI-TEC 소개",
        "377920133": "CI-TEC 과제",
        "131290561": "CI-TEC 기술지원",
        "178797461": "오픈스택 역량강화",
    }


def test_techrepo_roots_match_confirmed_values():
    assert TECHREPO_ROOTS == {
        "1541715706": "CI-TEC의 구름다리",
        "133859925": "클라우드 장애 대응",
        "449603893": "클라우드 CSP별 상품서비스 비교",
        "289434485": "클라우드 테스트 시나리오 및 도구",
        "133859927": "교육 및 세미나",
    }


# --- frontmatter generation must round-trip through the existing adapters ---
# (adapters.py's parsing format is frozen — this is the contract we must not break)


def test_confluence_docs_frontmatter_round_trips_through_adapter(tmp_path):
    front = build_frontmatter_confluence_docs(
        space_key="LOOKIN",
        folder="CI-TEC 과제",
        page_id="2510261901",
        title="[KPI-04] [기술력] Lookin 진단 분야 확대 (CI-TEC) 과제 진행 현황",
        url="https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=2510261901",
        last_modified="2026-09-07",
    )
    out_dir = tmp_path / "confluence_docs"
    out_dir.mkdir()
    (out_dir / "confluence_2510261901.md").write_text(front + "\n본문 내용\n", encoding="utf-8")

    drafts = list(iter_confluence_docs(tmp_path))
    assert len(drafts) == 1
    d = drafts[0]
    assert d.external_id == "2510261901"
    assert d.title == "[KPI-04] [기술력] Lookin 진단 분야 확대 (CI-TEC) 과제 진행 현황"
    assert d.metadata["공간명"] == "LOOKIN"
    assert d.metadata["폴더분류"] == "CI-TEC 과제"
    assert d.metadata["Page ID"] == "2510261901"
    assert (
        d.source_uri
        == "https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=2510261901"
    )


def test_confluence_docs_frontmatter_round_trips_with_colon_in_title(tmp_path):
    """A colon inside the title must not corrupt frontmatter parsing —
    _FRONT_YAML's key/value split uses maxsplit=1 so only the *first*
    colon on the line is the separator; everything after survives as the
    title value. Pinning this so a future refactor can't silently break it."""
    title = "이슈: 진단 실패율 급증 — 원인: 캐시 미스"
    front = build_frontmatter_confluence_docs(
        space_key="LOOKIN",
        folder="CI-TEC 기술지원",
        page_id="999",
        title=title,
        url="https://x/pages/viewpage.action?pageId=999",
        last_modified="2026-01-01",
    )
    out_dir = tmp_path / "confluence_docs"
    out_dir.mkdir()
    (out_dir / "confluence_999.md").write_text(front + "\n본문\n", encoding="utf-8")

    drafts = list(iter_confluence_docs(tmp_path))
    assert drafts[0].title == title


def test_tech_repo_frontmatter_round_trips_through_adapter(tmp_path):
    directory = (
        "[CI-TEC] 테크리포(Tech-Repository) Home > 클라우드 운영 기술 > "
        "★★분야별 기술 자료★★ > 운영체제 > 1. OS - Linux > 1-3. 메모리 관리 > "
        "메모리 관련 커널 파라미터"
    )
    front = build_frontmatter_tech_repo(
        space_key="[CI-TEC] 테크리포(Tech-Repository) Home",
        directory=directory,
        page_id="148554390",
        title="메모리 관련 커널 파라미터",
        url="https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=148554390",
        last_modified="2020-04-13",
    )
    out_dir = tmp_path / "tech_repo"
    out_dir.mkdir()
    (out_dir / "confluence_148554390.md").write_text(front + "\n본문\n", encoding="utf-8")

    drafts = list(iter_tech_repo(tmp_path))
    assert len(drafts) == 1
    d = drafts[0]
    assert d.external_id == "148554390"
    assert d.title == "메모리 관련 커널 파라미터"
    assert d.metadata["디렉토리"] == directory
    assert d.path_l2 == "★★분야별 기술 자료★★"
