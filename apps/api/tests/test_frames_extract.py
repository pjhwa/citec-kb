"""Unit tests for rule-based issue frame extraction."""

from app.frames.extract import extract_frame_from_markdown, quality_score


SAMPLE = """
# [CITECTS-2502] [기술지원] 모니모 Redis TimeOut 이슈

## LLM 요약

- **배경** :
  - 모니모 서비스 내 Redis 클러스터 POD 접속 불가
- **지원내용** :
  - **요청이슈** : 전체 POD에서 Redis 접속 불가·응답 지연
  - **분석결과(원인)** :
    - peak 트래픽이 Redis TimeOut 근본 원인
  - **조치내용** :
    - GPU 서버를 PB2 Fabric으로 연결 변경

## 원본 내용
h3. 이슈 증상
 * Redis 접속 불가

h3. 이슈 원인
 * peak 트래픽

h3. 해결 방안
 * Fabric 구성 변경
"""


def test_extract_slots_from_sample():
    fr = extract_frame_from_markdown(SAMPLE, title="[CITECTS-2502] 모니모 Redis TimeOut")
    assert fr["symptom"]
    assert fr["root_cause"]
    assert fr["resolution"]
    assert "Redis" in fr["components"] or "monimo" in fr["components"]
    assert fr["quality"] >= 0.5
    assert fr["raw_extract"]["method"] == "rules_v2"


def test_quality_both_slots():
    assert quality_score("증상 " * 10, "원인 " * 10, "조치 " * 10) >= 0.7
    assert quality_score(None, None, None) == 0.0


def test_inline_cause_resolution_labels():
    md = """
# [CITECTS-9] test
## 원본 내용
4. 원인 : peak traffic으로 인한 타임아웃
* 조치내용
  - GPU 서버 fabric 재배치로 해소
"""
    fr = extract_frame_from_markdown(md, title="[CITECTS-9] test")
    assert fr["root_cause"] and "peak" in fr["root_cause"].lower()
    assert fr["resolution"] and ("fabric" in fr["resolution"].lower() or "GPU" in fr["resolution"])


def test_wiki_heading_장애원인_and_조치내역():
    md = """
# [CITECTS-1024] sample
## 원본 내용
h1. 2. 장애원인
물리 스위치 포트 flapping으로 세션 단절
h1. 3. 조치 내역
포트 교체 후 서비스 정상화
"""
    fr = extract_frame_from_markdown(md, title="[CITECTS-1024] sample")
    assert fr["root_cause"] and "flapping" in fr["root_cause"].lower()
    assert fr["resolution"] and ("포트" in fr["resolution"] or "정상" in fr["resolution"])
    assert fr["quality"] >= 0.5


_SWIM_MD = """[26090761356] [삼성전자] 이라크 사무소(SELV-Iraq) 지역정전으로 사내시스템 및 인터넷 접속 불가
발생일시(한국): 2026-09-07 03:10 | 고객사: 삼성전자 | 진행상태: 조치완료 | 예상등급_SDS: X등급 | 장애유형: NW | 운영부서: 삼성SDS-SDSI | 신고자: Mirza Aziz | 기록구분: 실장애
■ 장애상황: 사내시스템 및 인터넷 접속 불가
■ 장애원인: 전원문제로 확인되며 고객사 건물 또는 지역 이슈인지 파악중
지역정전
■ 장애조치: 전원복구 후 정상화
지역정전 해소 후 서비스 정상
"""


def test_swim_bullet_recognized():
    """Phase 3 extension: SWIM (incident_reports) uses "■" bullets instead of
    -/*/•/○ — the inline patterns must recognize it as both a label prefix and
    a section-boundary stop token, or every ■ 장애원인/■ 장애조치 line is missed.
    """
    fr = extract_frame_from_markdown(
        _SWIM_MD,
        title="[삼성전자] 이라크 사무소(SELV-Iraq) 지역정전으로 사내시스템 및 인터넷 접속 불가",
    )
    assert fr["symptom"] == "사내시스템 및 인터넷 접속 불가"
    assert fr["root_cause"] and "지역정전" in fr["root_cause"]
    assert fr["resolution"] and "전원복구" in fr["resolution"]
    assert fr["quality"] >= 0.8
