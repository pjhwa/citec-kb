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
