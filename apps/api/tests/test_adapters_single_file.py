"""Unit tests for single-file parse helpers used by /api/upload (no DB)."""

from pathlib import Path

from app.ingest.adapters import (
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
