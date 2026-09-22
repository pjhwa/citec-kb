"""Unit tests for app.confluence.map_sync — pure functions only, no I/O,
mirroring test_confluence_sync.py's style for the sibling confluence_docs/
tech_repo crawler.
"""

from __future__ import annotations

from app.confluence.map_sync import (
    _is_folder_title,
    build_frontmatter_confluence_map,
)
from app.ingest.adapters import iter_confluence_map


def test_is_folder_title_detects_numbered_menu_and_divider_pages():
    assert _is_folder_title("005. 이슈/문제/KDB/SOP") is True
    assert _is_folder_title("998. MSP인프라기술그룹 소통함") is True
    assert _is_folder_title("----------------------------------------------") is True
    assert _is_folder_title("") is True


def test_is_folder_title_false_for_real_kdb_article_titles():
    assert _is_folder_title("[HW] CPU Uncorrectable Machine Check Exception") is False
    assert (
        _is_folder_title(
            "[HV] VMware : ( VCENTER ) Alarm  vSphere UI Health Alarm  "
            "on Datacenters changed from Green to Yellow"
        )
        is False
    )


def test_frontmatter_round_trips_through_adapter(tmp_path):
    front = build_frontmatter_confluence_map(
        space_key="CLDENG",
        space_name="MSP인프라기술그룹",
        root_label="문제 해결 문서 (KDB)",
        page_id="285425354",
        title="[HA-Tip] Azure-pacemaker Fencing 이벤트 분석 방법 (Azure DC Hosting작업)",
        url="https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=285425354",
        path_breadcrumb="MSP인프라기술그룹 Home > 문제 해결 문서 (KDB) > [HA-Tip] Azure-pacemaker Fencing 이벤트 분석 방법 (Azure DC Hosting작업)",
        last_modified="2026-01-01",
        is_folder=False,
    )
    out_dir = tmp_path / "confluence_map"
    out_dir.mkdir()
    (out_dir / "confluence_map_285425354.md").write_text(
        front + "\n본문 대신 경로만\n", encoding="utf-8"
    )

    drafts = list(iter_confluence_map(tmp_path))
    assert len(drafts) == 1
    d = drafts[0]
    assert d.source_type == "confluence_map"
    assert d.external_id == "285425354"
    assert d.title.startswith("[HA-Tip] Azure-pacemaker")
    assert d.evidence_grade == "C"
    assert d.metadata["space_key"] == "CLDENG"
    assert d.metadata["공간명"] == "MSP인프라기술그룹"
    assert d.metadata["유형"] == "문서"
    assert d.path_l2 == "CLDENG"
    assert "문제 해결 문서 (KDB)" in d.path_l3
    assert d.source_uri == "https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=285425354"


def test_frontmatter_folder_type_round_trips():
    front = build_frontmatter_confluence_map(
        space_key="DevOps001",
        space_name="SCP인프라운영팀",
        root_label="005. 이슈/문제/KDB/SOP",
        page_id="601879661",
        title="005. 이슈/문제/KDB/SOP",
        url="https://devops.sdsdev.co.kr/confluence/pages/viewpage.action?pageId=601879661",
        path_breadcrumb="SCP인프라운영팀 Home > 005. 이슈/문제/KDB/SOP",
        last_modified="2026-01-01",
        is_folder=True,
    )
    assert "유형 : 폴더" in front
