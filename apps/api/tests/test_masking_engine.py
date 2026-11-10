from app.masking.dictionary import Dictionary
from app.masking.engine import Masker, mask_json
from app.masking.patterns import BUILTIN_ALLOW
import re

ALL = frozenset({"pii", "net", "biz"})
NO_DIC = Dictionary.empty("missing")


def m(groups=ALL, dic=NO_DIC):
    return Masker(groups, dic)


def test_same_value_same_number_within_one_masker():
    mk = m()
    out = mk.mask_text("a 10.0.0.1 b 10.0.0.2 c 10.0.0.1")
    assert out == "a [IP-1] b [IP-2] c [IP-1]"
    assert mk.counts["net"] == 3


def test_numbering_restarts_for_a_new_masker():
    assert m().mask_text("10.0.0.2") == "[IP-1]"


def test_phone_and_address_line_masks_name_too():
    line = " 1 홍길동 2 (12345) 경기도 용인시 처인구 포곡로 123 가나아파트 101동 202호 031-123-4567"
    out = m().mask_text(line)
    assert "[ADDR-1]" in out and "[PHONE-1]" in out and "[NAME-1]" in out
    assert "홍길동" not in out and "용인시" not in out and "031-" not in out


def test_name_not_masked_on_plain_line():
    assert m().mask_text("담당자 홍길동 님이 처리함") == "담당자 홍길동 님이 처리함"


def test_label_sabun_and_label_name():
    out = m().mask_text("신청인 : 홍길동 프로\n사번: 12345")
    assert out == "신청인 : [NAME-1] 프로\n사번: [ID-1]"


def test_sabun_whitespace_table_column():
    text = "\n".join([
        "No 구분 이름 사번 조편성",
        " 1 1차 홍길동 12345 A조",
        " 2 1차 김철수 12346 B조",
        "다른 문단 99999",
    ])
    out = m().mask_text(text).split("\n")
    assert out[1].split()[3] == "[ID-1]" and out[2].split()[3] == "[ID-2]"
    assert "홍길동" not in out[1] and "김철수" not in out[2]       # 사번 행 → 이름도 마스킹
    assert out[3] == "다른 문단 99999"                              # 문맥 밖 5자리 숫자는 그대로


def test_sabun_pipe_table_column():
    text = "| 이름 | 사번 | 비고 |\n|---|---|---|\n| 홍길동 | 12345 | x |\n"
    out = m().mask_text(text)
    assert "| [ID-1] |" in out and "홍길동" not in out


def test_plain_five_digit_numbers_untouched():
    assert m().mask_text("합계 12345 원, 문서 10000") == "합계 12345 원, 문서 10000"


def test_groups_gate_each_category():
    s = "10.0.0.1 031-123-4567"
    assert Masker(frozenset({"net"}), NO_DIC).mask_text(s) == "[IP-1] 031-123-4567"
    assert Masker(frozenset({"pii"}), NO_DIC).mask_text(s) == "10.0.0.1 [PHONE-1]"
    assert Masker(frozenset(), NO_DIC).mask_text(s) == s


def test_allowlist_and_loopback():
    dic = Dictionary(None, (), BUILTIN_ALLOW | {"8.8.8.8"}, "ok")
    assert Masker(ALL, dic).mask_text("127.0.0.1 8.8.8.8 10.0.0.1") == "127.0.0.1 8.8.8.8 [IP-1]"


def test_biz_customers_and_hosts():
    dic = Dictionary(re.compile(r"(?<![A-Za-z0-9])(?:가나전자)(?![A-Za-z0-9])"),
                     (re.compile(r"\b[a-z]{3}\d{2}web\d{2}\b"),), BUILTIN_ALLOW, "ok")
    out = Masker(ALL, dic).mask_text("가나전자의 abc01web02 서버, 다시 가나전자")
    assert out == "[CUST-1]의 [HOST-1] 서버, 다시 [CUST-1]"
    assert Masker(frozenset({"pii", "net"}), dic).mask_text("가나전자") == "가나전자"


def test_mask_json_walks_values_not_keys():
    mk = m()
    obj = {"10.0.0.1": ["a 10.0.0.1", {"x": "031-123-4567"}], "n": 3, "ok": True, "none": None}
    out = mask_json(obj, mk)
    assert out == {"10.0.0.1": ["a [IP-1]", {"x": "[PHONE-1]"}], "n": 3, "ok": True, "none": None}


def test_spaced_label_name_masked():
    assert m().mask_text("□ 신 청 인 : 홍길동 프로") == "□ 신 청 인 : [NAME-1] 프로"


def test_label_glued_to_hangul_not_masked():
    assert m().mask_text("파일이름: 보고서 작성") == "파일이름: 보고서 작성"


def test_ordinary_sentence_on_phone_line_only_phone_masked():
    s = "문의는 010-1234-5678 로 주시면 서버 상태 이상 여부를 확인해 드립니다"
    assert m().mask_text(s) == "문의는 [PHONE-1] 로 주시면 서버 상태 이상 여부를 확인해 드립니다"
    assert m().mask_text("고객 요청 사항 031-123-4567 로 연락 바랍니다") == "고객 요청 사항 [PHONE-1] 로 연락 바랍니다"


def test_name_rules_on_phone_line():
    assert m().mask_text("담당 이수 프로 010-1234-5678") == "담당 [NAME-1] 프로 [PHONE-1]"
    assert m().mask_text("남궁민수 010-1234-5678") == "[NAME-1] [PHONE-1]"
    assert m().mask_text("홍길동 010-1234-5678") == "[NAME-1] [PHONE-1]"


def test_name_column_whitespace_table():
    text = "\n".join([
        "No 이름 사번 비고",
        " 1 이수 12345 x",
        " 2 박온 12346 y",
        "",
        " 3 최우 12347 z",
    ])
    out = m().mask_text(text).split("\n")
    assert out[0] == "No 이름 사번 비고"
    assert out[1].split()[1] == "[NAME-1]" and out[1].split()[2] == "[ID-1]"
    assert out[2].split()[1] == "[NAME-2]" and out[2].split()[2] == "[ID-2]"
    assert out[4] == " 3 최우 12347 z"


def test_name_and_sabun_pipe_columns():
    out = m().mask_text("| 이름 | 사번 |\n|---|---|\n| 이수 | 12345 |\n")
    assert out.split("\n")[0] == "| 이름 | 사번 |"
    assert "| [NAME-1] | [ID-1] |" in out and "이수" not in out


def test_given_name_ending_eun_is_masked():
    assert m().mask_text("박지은 010-1234-5678") == "[NAME-1] [PHONE-1]"
    assert m().mask_text("김서은 010-1234-5678") == "[NAME-1] [PHONE-1]"


def _idem(s):
    out = m().mask_text(s)
    assert m().mask_text(out) == out
    return out


def test_intl_phone_formats():
    for p in ["+82-10-1234-5678", "+82-2-123-4567", "+82 10 1234 5678", "+82-31-123-4567"]:
        assert _idem(f"연락처 {p} 입니다") == "연락처 [PHONE-1] 입니다", p
    assert _idem("version +82-10 released") == "version +82-10 released"


def test_address_split_across_newline_and_beonji():
    out = _idem("서울시 마포구 서교동 \n123-4번지 가나빌딩 9층")
    assert out == "[ADDR-1]"
    out = _idem("경기도 성남시 분당구 정자동 12-3번지 가나빌딩 9층")
    assert out == "[ADDR-1]"
    mk = m()
    mk.mask_text("서울시 마포구 서교동 \n123-4번지 가나빌딩 9층")
    assert mk.counts["pii"] == 1


def test_more_titles():
    assert _idem("홍길동 그룹장(공통네트워크그룹) 010-1111-2222") == "[NAME-1] 그룹장(공통네트워크그룹) [PHONE-1]"
    assert _idem("김철수 상무(통합인프라팀) 010-3333-4444") == "[NAME-1] 상무(통합인프라팀) [PHONE-1]"
    assert _idem("이수 상무(통합인프라팀) 010-1111-2222") == "[NAME-1] 상무(통합인프라팀) [PHONE-1]"


def test_more_stopwords():
    assert _idem("운영부서(금융) : 홍길동 그룹장(통합인프라팀) 010-1111-2222") == \
        "운영부서(금융) : [NAME-1] 그룹장(통합인프라팀) [PHONE-1]"
    assert _idem("서비스 인프라 담당 홍길동 010-1111-2222") == "서비스 인프라 담당 [NAME-1] [PHONE-1]"


def test_netmask_not_masked():
    assert _idem("mask 255.255.255.0 gw 10.0.0.1") == "mask 255.255.255.0 gw [IP-1]"


def test_ip_adjacent_to_hangul():
    cases = {
        "서버 10.1.2.3에서 접속": "서버 [IP-1]에서 접속", "10.1.2.3로 접속": "[IP-1]로 접속",
        "IP:10.0.0.1에": "IP:[IP-1]에", "서버10.0.0.1": "서버[IP-1]",
        "2001:db8::1에서": "[IP-1]에서", "fe80::1로": "[IP-1]로",
    }
    for src, exp in cases.items():
        assert _idem(src) == exp, src
    for s in ["v1.2.3.4", "256.1.1.1", "1.2.3", "10.1.1.1.5", "12:30:45", "aa:bb:cc:dd:ee:ff"]:
        assert _idem(s) == s, s


def test_phone_variants():
    for p in ["010 1234 5678", "010.1234.5678", "(010)1234-5678", "+821012345678",
              "０１０-１２３４-５６７８", "010‑1234‑5678", "010​-1234-5678", "02 123 4567"]:
        assert _idem(f"전화 {p} 끝") == "전화 [PHONE-1] 끝", p
    for s in ["문서 2021 04 08", "금액 12 345 678원", "v1.2.3", "1234 5678", "10.1.2.3"]:
        out = _idem(s)
        assert "PHONE" not in out, s


def test_sabun_label_variants():
    assert _idem("사번(12345)") == "사번([ID-1])"
    assert _idem("사번: 12345") == "사번: [ID-1]"
    assert _idem("사원번호: 12345") == "사원번호: [ID-1]"
    assert _idem("사원 번호 12345") == "사원 번호 [ID-1]"
    assert _idem("사번: 123456") == "사번: 123456"


def test_spaced_name_value():
    assert _idem("이름 : 홍 길 동") == "이름 : [NAME-1]"
    assert _idem("이름 : 홍길동") == "이름 : [NAME-1]"
    assert _idem("신청인 : 홍길동 프로") == "신청인 : [NAME-1] 프로"
    assert _idem("파일이름: 보고서") == "파일이름: 보고서"


def test_ipv4_inside_identifiers():
    cases = {"ON=M1_192.168.1.20_00001": "ON=M1_[IP-1]_00001", "node_10.20.30.40_8080": "node_[IP-1]_8080",
             "host-10.0.0.5": "host-[IP-1]", "ip10.1.2.3x": "ip[IP-1]x"}
    for src, exp in cases.items():
        assert _idem(src) == exp, src
    for s in ["v1.2.3.4", "V10.0.0.1", "256.1.1.1", "10.1.1.1.5", "1.2.3", "192.168.001.010", "mask 255.255.255.0"]:
        assert _idem(s) == s, s


def test_email_masked_as_pii_only():
    t = "문의 hong.gd+kb@samsung.com 또는 a_b@sub.example.co.kr 로"
    out = m(frozenset({"pii"})).mask_text(t)
    assert out == "문의 [EMAIL-1] 또는 [EMAIL-2] 로"
    assert m(frozenset({"net", "biz"})).mask_text(t) == t


def test_email_same_value_same_number_and_idempotent():
    mk = m()
    out = mk.mask_text("A@x.com B@x.com a@X.com")
    assert out == "[EMAIL-1] [EMAIL-2] [EMAIL-1]"
    assert m().mask_text(out) == out


def test_email_does_not_trigger_name_masking_on_same_line():
    out = m().mask_text("김치찌개 담당 mail a@x.com")
    assert out == "김치찌개 담당 mail [EMAIL-1]"


def test_email_with_digits_not_taken_as_phone_or_ip():
    out = m().mask_text("01012345678@x.com 10.1.2.3@y.org")
    assert "PHONE" not in out and "IP" not in out and out.count("[EMAIL-") == 2
