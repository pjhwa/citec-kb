"""Frame extractor: label formats seen in real support_history tickets (synthetic bodies)."""
import pytest

from app.frames.extract import EXTRACTOR_VERSION, _clean_slot_text, extract_frame_from_markdown as ex


def _f(body):
    f = ex(body, title="[CITECTS-1] t")
    return f["root_cause"], f["resolution"]


@pytest.mark.parametrize("body,cause", [
    ("□ 장애원인 : 직접오류원인 : 중복 체크로직 미비\n□ 조치 : 로직 수정", "중복 체크로직 미비"),
    ("-> 원인 : 서버 다운 시 timeout 미설정으로 지연\n-> 해결 : 설정 추가", "timeout 미설정"),
    (" *=> 원인 : IPS 장비 탐지 룰에 의해 IP 차단 됨*\n *=> 조치 : 룰 예외 등록*", "IPS"),
    ("■ 이슈 원인 :\n  디스크 I/O 한계 초과\n■ 조치 : 디스크 증설", "디스크 I/O"),
    ("h2. *원인분석*\n* 세션 풀 고갈로 응답 지연\nh2. *조치*\n* 풀 크기 증설", "세션 풀"),
])
def test_cause_label_formats(body, cause):
    c, r = _f(body)
    assert c and cause in c
    assert r  # the paired action label is found too


def test_prose_mentioning_cause_is_not_a_cause():
    for body in (
        "이슈를 회피하기 위한 (또는 원인을 특정짓기 위한) 테스트를 수행했으나 실패",
        "정확한 원인 분석을 위해 덤프 설정 확인 필요",
    ):
        c, _ = _f(body)
        assert not c, body


def test_request_line_starting_with_cause_word_is_preexisting_behaviour():
    """Not fixed here: a line that *starts* with '원인 분석 …' is read as a section header
    by the old extractor too. Pin it so a future change is a conscious one."""
    c, _ = _f("# [CITECTS-1] SCP 장애 원인 분석\n원인 분석 지원 요청드립니다.")
    assert c == "원인 분석 지원 요청드립니다."


def test_placeholder_cause_still_dropped():
    assert _f("원인 : 원인 분석 중")[0] is None


def test_label_residue_is_trimmed_only_when_marked():
    assert _clean_slot_text("원인분석* * DB 특이 사항 없음") == "DB 특이 사항 없음"
    assert _clean_slot_text("조치 경과* - (02:58) Reboot 착수") == "(02:58) Reboot 착수"
    assert _clean_slot_text("원인 (Causes)* ---- 서비스 이름 중복") == "서비스 이름 중복"
    assert _clean_slot_text("해결방법은 재부팅입니다") == "해결방법은 재부팅입니다"  # no '*': untouched


def test_extractor_version_bumped():
    assert EXTRACTOR_VERSION == "2026-10-29.1"
