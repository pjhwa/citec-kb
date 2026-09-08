"""Unit tests for single-file parse helpers used by /api/upload (no DB)."""

from pathlib import Path

from app.ingest.adapters import (
    parse_incident_report_file,
    parse_support_history_file,
    parse_tech_repo_file,
    parse_tuning_ai_file,
)

_SUPPORT_HISTORY_MD = """# CITECTS-9999 테스트 이슈

- **Issue Key**: CITECTS-9999
- **Status**: 닫힘
- **Component**: Redis

Redis 커넥션 타임아웃 관련 지원이력 본문입니다.
"""

_TECH_REPO_MD = """---
제목 : 커널 파라미터 튜닝
Page ID : 148554390
URL : https://confluence.example/pages/148554390
디렉토리 : 루트 > 기술문서 > 운영체제 > 커널
---
# 커널 파라미터 튜닝

sysctl 파라미터 튜닝 본문입니다.
"""

_TUNING_AI_MD = """---
issue_id: ISS-1234
domain: oracle
---
# OOM 이슈 분석

Oracle OOM 이슈 분석 본문입니다.
"""


def test_parse_support_history_file(tmp_path: Path):
    p = tmp_path / "CITECTS-9999.md"
    p.write_text(_SUPPORT_HISTORY_MD, encoding="utf-8")

    draft = parse_support_history_file(p)

    assert draft.source_type == "support_history"
    assert draft.external_id == "CITECTS-9999"
    assert draft.title == "CITECTS-9999 테스트 이슈"
    assert draft.evidence_grade == "A"  # Status: 닫힘
    assert draft.work_type == "Redis"
    assert "Redis 커넥션 타임아웃" in draft.body_md
    assert draft.content_hash  # finalize() was called


_INCIDENT_REPORT_MD_NEW = """[26090761356] [삼성전자] 이라크 사무소(SELV-Iraq) 지역정전으로 사내시스템 및 인터넷 접속 불가
발생일시(한국): 2026-09-07 03:10 | 고객사: 삼성전자 | 진행상태: 조치완료 | 예상등급_SDS: X등급 | 장애유형: NW | 운영부서: 삼성SDS-SDSI | 신고자: Mirza Aziz | 기록구분: 실장애
■ 장애상황: 사내시스템 및 인터넷 접속 불가
■ 장애원인: 전원문제로 확인되며 고객사 건물 또는 지역 이슈인지 파악중
지역정전
■ 장애조치: 전원복구 후 정상화
지역정전 해소 후 서비스 정상
"""

_INCIDENT_REPORT_MD_LEGACY = """[26082261323] [대외고객사 S-OIL] 자동배차시스템 로그인 불가
발생일시(한국): 2026-08-22 06:40 | 고객사: 대외고객사 | 진행상태: 원인분석중 | 예상등급_SDS: 4등급 | 장애유형: Infra | 운영부서: MSP인프라기술그룹(MSP인프라운영) | 신고자: 이서영 | 서비스 중단시간: 281분 | GDC 여부: N | 기록구분: 실장애 | 경영진 보고: N
■ 장애상황: 정유차량 자동 배차 불가 (장애 발생 시간에 수동배차로 진행)
※ 대외사 장비로 SWIM 조회 불가
자동배차시스템(ATSS) 로그인 및 배차 불가 (수동 배차로 우회하여 서비스 영향 최소화)
■ 장애원인: Oracle DB 블럭 손상
Oracle DB 블럭 손상 추정, 상세원인 파악 중
Oracle DB 내부 힙메모리 오류로 추정, 상세원인 파악 중
DB의 손상된 블록 복구 실패로 SMON 비정상 종료로 발생, 손상 원인은 파악 중
■ 장애조치: MSP인프라기술그룹에서 백업 파일로 DB 복구하여 정상화
넷백업 DB 파일 및 아카이브 리스토어, DB Recovery 및 재기동하여 정상화
■ 반복항목: 조치자(분류): Infra / 조치자(팀): MSP인프라운영팀 / 조치자(부서): MSP인프라기술그룹(MSP인프라운영) / 조치자: 이서영
"""


def test_parse_incident_report_file_new_format(tmp_path: Path):
    p = tmp_path / "swim_26090761356.md"
    p.write_text(_INCIDENT_REPORT_MD_NEW, encoding="utf-8")

    draft = parse_incident_report_file(p)

    assert draft.source_type == "incident_reports"
    assert draft.external_id == "26090761356"
    assert draft.title == "[삼성전자] 이라크 사무소(SELV-Iraq) 지역정전으로 사내시스템 및 인터넷 접속 불가"
    assert draft.evidence_grade == "A"  # 진행상태: 조치완료
    assert draft.domain == "network"  # 장애유형: NW
    assert draft.metadata["장애상황"] == "사내시스템 및 인터넷 접속 불가"
    assert "지역정전" in draft.metadata["장애원인"]
    assert draft.content_hash


def test_parse_incident_report_file_legacy_format_with_repeat_section(tmp_path: Path):
    p = tmp_path / "swim_26082261323.md"
    p.write_text(_INCIDENT_REPORT_MD_LEGACY, encoding="utf-8")

    draft = parse_incident_report_file(p)

    assert draft.source_type == "incident_reports"
    assert draft.external_id == "26082261323"
    assert draft.title == "[대외고객사 S-OIL] 자동배차시스템 로그인 불가"
    assert draft.evidence_grade == "B"  # 진행상태: 원인분석중
    assert draft.domain is None  # 장애유형: Infra는 매핑 대상 아님
    assert draft.metadata["서비스 중단시간"] == "281분"
    assert "반복항목" in draft.metadata
    assert "이서영" in draft.metadata["반복항목"]
    assert draft.content_hash


def test_parse_tech_repo_file(tmp_path: Path):
    p = tmp_path / "148554390_kernel.md"
    p.write_text(_TECH_REPO_MD, encoding="utf-8")

    draft = parse_tech_repo_file(p)

    assert draft.source_type == "tech_repo"
    assert draft.external_id == "148554390"
    assert draft.title == "커널 파라미터 튜닝"
    assert draft.domain == "os"
    assert draft.path_l2 == "운영체제"
    assert draft.path_l3 == "운영체제 > 커널"
    assert draft.source_uri == "https://confluence.example/pages/148554390"
    assert draft.content_hash


def test_parse_tuning_ai_file(tmp_path: Path):
    p = tmp_path / "ISS-1234.md"
    p.write_text(_TUNING_AI_MD, encoding="utf-8")

    draft = parse_tuning_ai_file(p)

    assert draft.source_type == "tuning_ai"
    assert draft.external_id == "ISS-1234"
    assert draft.title == "OOM 이슈 분석"
    assert draft.domain == "oracle"
    assert draft.content_hash


def test_iter_support_history_matches_single_file_parse(tmp_path: Path):
    """Regression: directory-scan path must still work after the refactor."""
    d = tmp_path / "support_history"
    d.mkdir()
    (d / "CITECTS-9999.md").write_text(_SUPPORT_HISTORY_MD, encoding="utf-8")

    from app.ingest.adapters import iter_support_history

    drafts = list(iter_support_history(tmp_path))
    assert len(drafts) == 1
    assert drafts[0].external_id == "CITECTS-9999"
    assert drafts[0].content_hash == parse_support_history_file(d / "CITECTS-9999.md").content_hash
