"""Masking regexes: phone / address / IP. Synthetic values only."""

from app.masking import patterns as P


def _all(rx, s):
    return [m.group(0) for m in rx.finditer(s)]


def test_phone_dash_and_nodash():
    s = "031-123-4567 02-345-6789 01012345678 1234567890123"
    assert _all(P.PHONE, s) == ["031-123-4567", "02-345-6789", "01012345678"]


def test_address_full_road_address():
    s = "(12345) 경기도 용인시 처인구 포곡로 123 가나쉐르빌아파트 101동 202호 끝"
    assert _all(P.ADDR, s) == ["(12345) 경기도 용인시 처인구 포곡로 123 가나쉐르빌아파트 101동 202호"]


def test_address_variants_and_non_address():
    assert _all(P.ADDR, "서울특별시 강남구 테헤란로 152")
    assert _all(P.ADDR, "경기도 성남시 분당구 정자동 12-3")      # 지번
    assert _all(P.ADDR, "경남 창원시 마산합포구 월영동 55-1")      # 약칭 시도
    assert not _all(P.ADDR, "서울 서비스 센터 점검")


def test_ipv4_boundaries():
    s = "10.20.30.40 and 192.168.0.1: x 256.1.1.1 v1.2.3.4 1.2.3 10.1.1.1.5"
    assert _all(P.IPV4, s) == ["10.20.30.40", "192.168.0.1"]


def test_ipv6_requires_double_colon_or_full_form():
    s = "fe80::1 2001:db8:0:0:0:0:0:1 12:30:45 aa:bb:cc:dd:ee:ff ::1"
    assert _all(P.IPV6, s) == ["fe80::1", "2001:db8:0:0:0:0:0:1", "::1"]


def test_label_sabun_and_label_name():
    m = P.LABEL_SABUN.search("사번: 12345 입니다")
    assert m and m.group(2) == "12345"
    assert not P.LABEL_SABUN.search("사번: 123456")          # 6자리는 대상 아님
    m = P.LABEL_NAME.search("신청인 : 홍길동 프로")
    assert m and m.group(2) == "홍길동"


def test_label_name_allows_spaces_between_letters():
    for s in ["□ 신 청 인 : 홍길동 프로", "성 명 : 홍길동", "이 름: 홍길동", "수 령 인 : 홍길동", "받는 분 : 홍길동"]:
        m = P.LABEL_NAME.search(s)
        assert m and m.group(2) == "홍길동", s
    assert P.LABEL_NAME.search("신청인이 : 홍길동") is None


def test_label_name_not_glued_to_hangul():
    assert P.LABEL_NAME.search("파일이름: 보고서") is None
    assert P.LABEL_NAME.search("호스트이름 : 서버설정") is None
    assert P.LABEL_NAME.search("사용자 이름: 홍길동").group(2) == "홍길동"
    assert P.LABEL_NAME.search("□ 신 청 인 : 홍길동").group(2) == "홍길동"
    assert P.LABEL_NAME.search("성명: 김철수").group(2) == "김철수"
