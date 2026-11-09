# PII·고객 민감정보 출력 시점 마스킹 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/v1`·`/api` JSON 응답에서 개인정보(`pii`)·IP(`net`)·고객사명/호스트명(`biz`)을 롤별로 마스킹하고, `senior`/`admin`이 토글로 원본을 볼 수 있게 하며 원본 열람을 동기 감사 로그로 남긴다.

**Architecture:** 순수 함수 엔진(`app/masking/`)이 JSON 문자열 값을 줄 단위로 마스킹하고, `MaskingMiddleware`가 응답을 가로채 요청자 롤(`policy.decide`)에 따라 엔진을 호출한다. 라우터는 수정하지 않는다. 원본 응답은 `unmask_audit_log` 동기 INSERT가 성공한 경우에만 나간다. 마스킹 예외는 500(fail-closed).

**Tech Stack:** FastAPI/Starlette `BaseHTTPMiddleware`, SQLAlchemy 2 + Alembic, pytest, 바닐라 JS(웹), FastMCP(`mcp-server/server.py`).

**Spec:** `docs/superpowers/specs/2026-11-09-pii-masking-design.md`

**환경 메모**
- 테스트 실행: `cd apps/api && ../../.venv/bin/python -m pytest tests/<file> -q` (루트 `.venv`에 의존성이 있다. 시스템 python3에는 `redis`가 없다).
- `apps/api/tests/conftest.py`가 synonym DB 접근을 막아 둔다. 마스킹 단위 테스트는 DB 없이 돈다.
- 모든 예시 값(전화번호·주소·사번·이름)은 **합성 값**이다. 실데이터를 테스트·커밋에 넣지 않는다.

**스펙 대비 의도적 조정 (구현 중 발견)**
1. `masking` 응답 필드는 *마스킹이 실제로 일어났거나 원본 요청이 있었을 때만* dict 응답의 최상위에 추가한다(항상 추가하면 기존 클라이언트 스키마를 깰 수 있고, 최상위가 배열인 응답에는 넣을 수 없다). 대신 `X-Citec-Masking` 응답 헤더를 같은 조건에서 항상 붙인다.
2. 원본 허용 시 감사 로그는 **실제로 노출되는 `pii`/`net` 항목이 1건 이상일 때** 기록한다(토글을 켠 채 페이지를 열 때마다 0건짜리 로그가 쌓이는 것을 막는다). 거부(`denied`)는 항상 기록한다.
3. 루프백(`127.0.0.1`, `0.0.0.0`, `::1`)은 내장 allowlist로 마스킹하지 않는다.

---

## File Structure

| 파일 | 역할 |
|---|---|
| `apps/api/app/masking/__init__.py` | 공개 API 재노출 |
| `apps/api/app/masking/patterns.py` | 정규식·성씨·불용어 상수 (로직 없음) |
| `apps/api/app/masking/dictionary.py` | `config/masking_dictionary.json` 로더 (mtime 재로딩, 상태 보고) |
| `apps/api/app/masking/engine.py` | `Masker`(줄 단위 마스킹, 번호 부여), `mask_json` |
| `apps/api/app/masking/policy.py` | `decide(principal, auth_mode, unmask_requested)` → `Decision` |
| `apps/api/app/masking/audit.py` | `write_unmask_audit(...)` 동기 INSERT |
| `apps/api/app/masking/middleware.py` | `MaskingMiddleware` |
| `apps/api/app/db/models.py` | `UnmaskAuditLog` 모델 추가 |
| `apps/api/alembic/versions/20261109_0024_unmask_audit_log.py` | 마이그레이션 |
| `apps/api/app/main.py` | 미들웨어 등록, `/v1/health`에 `masking` 상태 |
| `config/masking_dictionary.json.example`, `.gitignore` | 사전 예시, 실파일 추적 제외 |
| `apps/web/public/js/auth.js`, `nav.js`, `css/theme.css` | 토글·붉은 띠·배지 |
| `mcp-server/server.py`, `docs/AI_AGENT_GUIDE.md` | 마스킹 안내 |
| `scripts/masking_probe.py` | 실데이터 점검(건수만 출력) |
| `apps/api/tests/test_masking_*.py` | 단위·미들웨어·회귀 테스트 |

---

### Task 1: 패턴 상수와 개별 탐지 정규식

**Files:**
- Create: `apps/api/app/masking/__init__.py` (빈 파일로 시작)
- Create: `apps/api/app/masking/patterns.py`
- Test: `apps/api/tests/test_masking_patterns.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_patterns.py -q`
Expected: FAIL — `ImportError`/`AttributeError` (`app.masking.patterns` 없음)

- [ ] **Step 3: Write minimal implementation** — `apps/api/app/masking/patterns.py`

```python
"""Compiled regexes and word lists for the masking engine (no logic here).

All patterns were exercised against synthetic samples shaped like the real Confluence 설문/수요조사 pages
(phone: 0xx-xxxx-xxxx, full road address, 5-digit 사번 next to a 사번 label/column).
"""

from __future__ import annotations

import re

_REGION = (
    r"(?:서울특별시|서울시|서울|부산광역시|부산|대구광역시|대구|인천광역시|인천|광주광역시|광주|대전광역시|대전|"
    r"울산광역시|울산|세종특별자치시|세종|경기도|경기|강원특별자치도|강원도|강원|충청북도|충북|충청남도|충남|"
    r"전북특별자치도|전라북도|전북|전라남도|전남|경상북도|경북|경상남도|경남|제주특별자치도|제주도|제주)"
)

# 시·도 + (시|군|구) 1~2개 + (도로명[+번지] | 읍면동리 + 지번) + 건물/동/호/층 꼬리표 반복
ADDR = re.compile(
    r"(?:\(\d{5}\)\s*)?"
    + _REGION
    + r"\s+(?:[가-힣]+(?:시|군|구)\s+){1,2}"
    + r"(?:[가-힣0-9]+(?:로|길)(?:\d+번길)?(?:\s+\d+(?:-\d+)?)?|[가-힣0-9]+(?:동|읍|면|리)\s+\d+(?:-\d+)?)"
    + r"(?:\s+[가-힣A-Za-z0-9\-]+(?:아파트|빌라|타워|빌딩|오피스텔|APT|동|호|층))*"
)

PHONE = re.compile(r"(?<!\d)(?:0\d{1,2}-\d{3,4}-\d{4}|01\d{8,9})(?!\d)")

_V4 = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
IPV4 = re.compile(r"(?<![\w.])(?:" + _V4 + r"\.){3}" + _V4 + r"(?![\w]|\.\d)")

_H = r"[0-9A-Fa-f]{1,4}"
IPV6 = re.compile(
    r"(?<![\w:])(?:(?:" + _H + r":){7}" + _H
    + r"|(?:" + _H + r":){1,7}:(?:" + _H + r"(?::" + _H + r"){0,6})?"
    + r"|::(?:" + _H + r"(?::" + _H + r"){0,6}))(?![\w:])"
)

# 사번은 문맥(라벨/열)에서만 잡는다. 5자리 숫자 단독은 대상 아님.
LABEL_SABUN = re.compile(r"(사번\s*[:：=]?\s*)(\d{5})(?!\d)")
LABEL_NAME = re.compile(r"((?:이름|성명|신청인|수령인|받는\s*분)\s*[:：=]\s*)([가-힣]{2,4})")

# 개인정보가 걸린 줄에서만 쓰는 이름 후보: 성씨로 시작하는 2~4자 한글 토큰.
_SURNAMES = (
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민나진지엄채원천방"
    "공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉사부"
)
NAME_TOKEN = re.compile(r"(?<![가-힣])([" + _SURNAMES + r"][가-힣]{1,3})(?![가-힣])")
NAME_STOPWORDS = frozenset(
    "사번 이름 성명 주소 전화 연락처 핸드폰 휴대폰 배송지 신청인 수량 금액 비고 구분 조편성 시작일 종료일 "
    "프로 책임 수석 선임 사원 매니저 담당 부서 참석 참여 신청 확인 변경 업데이트".split()
)

BUILTIN_ALLOW = frozenset({"127.0.0.1", "0.0.0.0", "::1"})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_patterns.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/masking/__init__.py apps/api/app/masking/patterns.py apps/api/tests/test_masking_patterns.py
git commit -m "feat(masking): 전화·주소·IP·사번 라벨 정규식 상수

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 사전 로더 (`biz`)

**Files:**
- Create: `apps/api/app/masking/dictionary.py`
- Create: `config/masking_dictionary.json.example`
- Modify: `.gitignore` (line ~77 `config/auth.json` 아래)
- Test: `apps/api/tests/test_masking_dictionary.py`

- [ ] **Step 1: Write the failing test**

```python
import json

import app.masking.dictionary as D


def _write(tmp_path, obj):
    (tmp_path / "masking_dictionary.json").write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _use(monkeypatch, tmp_path):
    class S:
        config_dir = str(tmp_path)

    monkeypatch.setattr(D, "get_settings", lambda: S())
    D.reset_cache()


def test_missing_file_disables_biz_only(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path)
    d = D.get_dictionary()
    assert d.customer_re is None and d.host_res == () and d.status == "missing"


def test_loads_customers_hosts_allowlist(monkeypatch, tmp_path):
    _write(tmp_path, {"customers": ["가나전자", "ACME"], "hostname_patterns": [r"\b[a-z]{3}\d{2}web\d{2}\b"],
                      "allowlist": ["8.8.8.8"]})
    _use(monkeypatch, tmp_path)
    d = D.get_dictionary()
    assert d.status == "ok"
    assert d.customer_re.search("가나전자는") and d.customer_re.search("acme corp")
    assert not d.customer_re.search("MACMEN")            # ASCII 항목은 단어 경계
    assert d.host_res[0].search("abc01web02")
    assert "8.8.8.8" in d.allow


def test_broken_json_reports_error_without_raising(monkeypatch, tmp_path):
    (tmp_path / "masking_dictionary.json").write_text("{not json", encoding="utf-8")
    _use(monkeypatch, tmp_path)
    d = D.get_dictionary()
    assert d.status.startswith("error") and d.customer_re is None


def test_reloads_when_file_changes(monkeypatch, tmp_path):
    _write(tmp_path, {"customers": ["가나전자"]})
    _use(monkeypatch, tmp_path)
    assert D.get_dictionary().customer_re.search("가나전자")
    _write(tmp_path, {"customers": ["다라물산"]})
    d = D.get_dictionary()
    assert d.customer_re.search("다라물산") and not d.customer_re.search("가나전자")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_dictionary.py -q`
Expected: FAIL — `ModuleNotFoundError: app.masking.dictionary`

- [ ] **Step 3: Write minimal implementation** — `apps/api/app/masking/dictionary.py`

```python
"""config/masking_dictionary.json loader (customers, hostname patterns, allowlist).

Reloads on the next call after the file's (mtime, size, inode) changes, like config/auth.json.
A missing/broken file disables `biz` masking only; `pii`/`net` are unaffected.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.masking.patterns import BUILTIN_ALLOW
from app.settings import get_settings

logger = logging.getLogger("citec.masking")


@dataclass(frozen=True)
class Dictionary:
    customer_re: Optional[re.Pattern]
    host_res: tuple
    allow: frozenset
    status: str  # ok | missing | error:<msg>

    @staticmethod
    def empty(status: str) -> "Dictionary":
        return Dictionary(None, (), BUILTIN_ALLOW, status)


_lock = threading.Lock()
_cache: dict = {"key": "unset", "dic": Dictionary.empty("missing")}


def reset_cache() -> None:
    with _lock:
        _cache["key"] = "unset"


def _path() -> Path:
    return Path(get_settings().config_dir) / "masking_dictionary.json"


def _build(data: dict) -> Dictionary:
    customers = sorted({str(c).strip() for c in (data.get("customers") or []) if str(c).strip()}, key=len, reverse=True)
    customer_re = None
    if customers:
        body = "|".join(re.escape(c) for c in customers)
        # ASCII 이름은 단어 경계, 한글 이름은 조사가 붙어도 잡히도록 경계를 ASCII 영숫자로만 건다.
        customer_re = re.compile(r"(?<![A-Za-z0-9])(?:" + body + r")(?![A-Za-z0-9])", re.IGNORECASE)
    hosts = tuple(re.compile(p) for p in (data.get("hostname_patterns") or []))
    allow = BUILTIN_ALLOW | frozenset(str(a) for a in (data.get("allowlist") or []))
    return Dictionary(customer_re, hosts, allow, "ok")


def get_dictionary() -> Dictionary:
    path = _path()
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
    except FileNotFoundError:
        key = None
    with _lock:
        if key == _cache["key"]:
            return _cache["dic"]
        if key is None:
            dic = Dictionary.empty("missing")
        else:
            try:
                dic = _build(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # noqa: BLE001 — 사전이 깨져도 pii/net 마스킹은 계속
                logger.exception("failed to load %s", path)
                dic = Dictionary.empty(f"error:{type(exc).__name__}")
        _cache["key"], _cache["dic"] = key, dic
        return dic
```

`config/masking_dictionary.json.example`:

```json
{
  "customers": ["예시고객사A", "ExampleCorp"],
  "hostname_patterns": ["\\b[a-z]{3}\\d{2}(?:web|was|db)\\d{2}\\b"],
  "allowlist": ["8.8.8.8", "1.1.1.1"]
}
```

`.gitignore`에 `config/auth.json` 줄 아래 추가:

```
# 고객사 목록은 민감하다 — 형식 예시는 config/masking_dictionary.json.example
config/masking_dictionary.json
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_dictionary.py -q`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/masking/dictionary.py apps/api/tests/test_masking_dictionary.py config/masking_dictionary.json.example .gitignore
git commit -m "feat(masking): 고객사·호스트명 사전 로더(mtime 재로딩, 깨지면 biz만 비활성)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 마스킹 엔진 (`Masker`, `mask_json`)

**Files:**
- Create: `apps/api/app/masking/engine.py`
- Test: `apps/api/tests/test_masking_engine.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_engine.py -q`
Expected: FAIL — `ModuleNotFoundError: app.masking.engine`

- [ ] **Step 3: Write minimal implementation** — `apps/api/app/masking/engine.py`

```python
"""Line-oriented masking engine. Pure: no request, no DB.

Groups: pii (phone/addr/sabun/name), net (ip), biz (customer/host).
Placeholders are numbered per Masker instance (= per response): same value → same number.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.masking import patterns as P
from app.masking.dictionary import Dictionary

GROUP = {"phone": "pii", "addr": "pii", "sabun": "pii", "name": "pii", "ip": "net", "cust": "biz", "host": "biz"}
TAG = {"phone": "PHONE", "addr": "ADDR", "sabun": "ID", "name": "NAME", "ip": "IP", "cust": "CUST", "host": "HOST"}
_TOKEN = re.compile(r"\S+")
_SABUN_CELL = re.compile(r"\d{5}")


class Masker:
    def __init__(self, groups: frozenset, dictionary: Dictionary):
        self.groups = frozenset(groups)
        self.dic = dictionary
        self.counts: Counter = Counter()          # group -> occurrences masked
        self._ids: dict = {}
        self._n: Counter = Counter()

    # -- placeholders --------------------------------------------------------------------------
    def _sub(self, kind: str, value: str) -> str:
        key = (kind, value.strip().lower())
        if key not in self._ids:
            self._n[kind] += 1
            self._ids[key] = f"[{TAG[kind]}-{self._n[kind]}]"
        self.counts[GROUP[kind]] += 1
        return self._ids[key]

    # -- public --------------------------------------------------------------------------------
    def mask_text(self, text: str) -> str:
        if not text or not self.groups:
            return text
        state = {"col": None, "miss": 0}
        return "\n".join(self._mask_line(line, state) for line in text.split("\n"))

    # -- internals -----------------------------------------------------------------------------
    def _mask_line(self, line: str, state: dict) -> str:
        if "net" in self.groups:
            line = self._mask_ip(line)
        if "pii" in self.groups:
            line = self._mask_pii(line, state)
        if "biz" in self.groups:
            line = self._mask_biz(line)
        return line

    def _mask_ip(self, line: str) -> str:
        def sub(m: re.Match) -> str:
            return m.group(0) if m.group(0) in self.dic.allow else self._sub("ip", m.group(0))

        return P.IPV6.sub(sub, P.IPV4.sub(sub, line))

    def _mask_biz(self, line: str) -> str:
        if self.dic.customer_re is not None:
            line = self.dic.customer_re.sub(lambda m: self._sub("cust", m.group(0)), line)
        for rx in self.dic.host_res:
            line = rx.sub(lambda m: self._sub("host", m.group(0)), line)
        return line

    def _mask_pii(self, line: str, state: dict) -> str:
        before = self.counts["pii"]
        line = P.ADDR.sub(lambda m: self._sub("addr", m.group(0)), line)
        line = P.PHONE.sub(lambda m: self._sub("phone", m.group(0)), line)
        labelled = bool(P.LABEL_SABUN.search(line))
        line = P.LABEL_SABUN.sub(lambda m: m.group(1) + self._sub("sabun", m.group(2)), line)
        line = P.LABEL_NAME.sub(lambda m: m.group(1) + self._sub("name", m.group(2)), line)
        line = self._sabun_column(line, state, labelled)
        if self.counts["pii"] > before:           # 이 줄에 개인정보가 있으면 같은 줄의 이름 후보도 가린다
            line = P.NAME_TOKEN.sub(
                lambda m: m.group(0) if m.group(1) in P.NAME_STOPWORDS else self._sub("name", m.group(1)), line
            )
        return line

    @staticmethod
    def _cells(line: str) -> list:
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            return parts[1:] if line.lstrip().startswith("|") else parts
        return line.split()

    def _sabun_column(self, line: str, state: dict, labelled: bool) -> str:
        if not line.strip():
            state["col"], state["miss"] = None, 0
            return line
        if not labelled:
            cells = self._cells(line)
            if "사번" in cells:                      # 헤더 줄: 열 위치 기억
                state["col"], state["miss"] = cells.index("사번"), 0
                return line
        if state["col"] is None:
            return line
        new, hit = self._replace_cell(line, state["col"])
        if hit:
            state["miss"] = 0
        else:
            state["miss"] += 1
            if state["miss"] >= 2:
                state["col"], state["miss"] = None, 0
        return new

    def _replace_cell(self, line: str, col: int) -> tuple:
        if "|" in line:
            parts = line.split("|")
            i = col + (1 if line.lstrip().startswith("|") else 0)
            if i < len(parts) and re.fullmatch(r"\s*\d{5}\s*", parts[i]):
                parts[i] = _SABUN_CELL.sub(lambda m: self._sub("sabun", m.group(0)), parts[i])
                return "|".join(parts), True
            return line, False
        toks = list(_TOKEN.finditer(line))
        if col < len(toks) and _SABUN_CELL.fullmatch(toks[col].group(0)):
            t = toks[col]
            return line[: t.start()] + self._sub("sabun", t.group(0)) + line[t.end():], True
        return line, False


def mask_json(obj: Any, masker: Masker) -> Any:
    """Mask every string *value* (not keys) in a decoded JSON structure."""
    if isinstance(obj, str):
        return masker.mask_text(obj)
    if isinstance(obj, list):
        return [mask_json(x, masker) for x in obj]
    if isinstance(obj, dict):
        return {k: mask_json(v, masker) for k, v in obj.items()}
    return obj
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_engine.py tests/test_masking_patterns.py -q`
Expected: all pass. 실패하면 **테스트가 아니라 엔진/정규식을 고친다**(예: `test_sabun_whitespace_table_column`의 토큰 인덱스는 헤더 `No 구분 이름 사번` → col 3).

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/masking/engine.py apps/api/tests/test_masking_engine.py
git commit -m "feat(masking): 줄 단위 마스킹 엔진(번호 부여, 사번 열·라벨, 레코드 내 이름)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 롤 정책 (`decide`)

**Files:**
- Create: `apps/api/app/masking/policy.py`
- Modify: `apps/api/app/masking/__init__.py`
- Test: `apps/api/tests/test_masking_policy.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest

from app.auth.principal import ANON_OPEN, ANON_RESTRICTED, Principal
from app.masking.policy import decide

P = lambda *roles: Principal(sub="u", name="u", roles=frozenset(roles), auth_via="apikey")  # noqa: E731


@pytest.mark.parametrize(
    "principal,mode,want,groups,granted,denied",
    [
        # 인증 꺼짐: 익명(전 롤 보유)도 author 수준, 원본 불가
        (ANON_OPEN, "off", False, {"pii", "net"}, False, False),
        (ANON_OPEN, "off", True, {"pii", "net"}, False, True),
        # 인증 켜짐
        (ANON_RESTRICTED, "apikey", False, {"pii", "net", "biz"}, False, False),
        (P("viewer"), "apikey", True, {"pii", "net", "biz"}, False, True),
        (P("author"), "apikey", False, {"pii", "net"}, False, False),
        (P("author"), "apikey", True, {"pii", "net"}, False, True),
        (P("senior"), "apikey", False, {"pii", "net"}, False, False),
        (P("senior"), "apikey", True, set(), True, False),
        (P("admin"), "oidc", True, set(), True, False),
        (P("viewer", "author"), "apikey", False, {"pii", "net"}, False, False),   # 높은 롤 우선
    ],
)
def test_decide(principal, mode, want, groups, granted, denied):
    d = decide(principal, mode, want)
    assert set(d.groups) == groups
    assert d.unmask_granted is granted and d.unmask_denied is denied


def test_levels():
    assert decide(P("viewer"), "apikey", False).level == "viewer"
    assert decide(P("admin"), "apikey", False).level == "senior"
    assert decide(ANON_OPEN, "off", False).level == "author"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_policy.py -q`
Expected: FAIL — `ModuleNotFoundError: app.masking.policy`

- [ ] **Step 3: Write minimal implementation** — `apps/api/app/masking/policy.py`

```python
"""Who sees what. See docs/superpowers/specs/2026-11-09-pii-masking-design.md §2."""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.principal import ROLE_ADMIN, ROLE_AUTHOR, ROLE_SENIOR, Principal

_OFF_MODES = frozenset({"", "off", "disabled", "none", "false", "0"})


@dataclass(frozen=True)
class Decision:
    level: str                 # viewer | author | senior
    groups: frozenset          # groups to mask (empty when original is granted)
    unmask_requested: bool
    unmask_granted: bool
    unmask_denied: bool


def decide(principal: Principal, auth_mode: str, unmask_requested: bool) -> Decision:
    enforced = (auth_mode or "off").lower().strip() not in _OFF_MODES
    if not enforced:
        # AUTH_MODE=off: everyone is anonymous-with-all-roles → treat as author (pii/net masked,
        # biz visible) and nobody can unmask.
        level = "author"
    elif principal.has_any(ROLE_ADMIN, ROLE_SENIOR):
        level = "senior"
    elif principal.has_any(ROLE_AUTHOR):
        level = "author"
    else:
        level = "viewer"

    groups = frozenset({"pii", "net", "biz"}) if level == "viewer" else frozenset({"pii", "net"})
    can_unmask = level == "senior"
    granted = unmask_requested and can_unmask
    if granted:
        groups = frozenset()
    return Decision(level, groups, unmask_requested, granted, unmask_requested and not can_unmask)
```

`apps/api/app/masking/__init__.py`:

```python
from app.masking.dictionary import get_dictionary  # noqa: F401
from app.masking.engine import Masker, mask_json  # noqa: F401
from app.masking.policy import Decision, decide  # noqa: F401
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_policy.py -q`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/masking/policy.py apps/api/app/masking/__init__.py apps/api/tests/test_masking_policy.py
git commit -m "feat(masking): 롤별 가시성 정책(AUTH_MODE=off는 author 수준, 원본은 senior/admin 토글)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 감사 로그 모델·마이그레이션·동기 기록

**Files:**
- Modify: `apps/api/app/db/models.py` (`ApiAccessLog` 클래스 뒤, ~line 532)
- Create: `apps/api/alembic/versions/20261109_0024_unmask_audit_log.py`
- Create: `apps/api/app/masking/audit.py`
- Test: `apps/api/tests/test_masking_audit.py`

- [ ] **Step 1: Write the failing test**

```python
import os

import pytest

from app.auth.principal import Principal
import app.masking.audit as A

_TEST_DSN = os.environ.get("CONFLUENCE_SYNC_TEST_DATABASE_URL")


def test_row_values_never_include_masked_text():
    row = A.build_row(
        principal=Principal(sub="s1", name="n1", roles=frozenset({"senior", "viewer"}), auth_via="oidc"),
        level="senior", method="GET", path="/v1/tickets/X-1",
        doc_refs=["X-1"], counts={"pii": 2, "net": 1}, client_ip="10.1.1.1", outcome="granted",
    )
    assert row["sub"] == "s1" and row["roles"] == "senior,viewer"
    assert row["counts"] == {"pii": 2, "net": 1} and row["outcome"] == "granted"
    assert set(row) == {"sub", "name", "roles", "level", "method", "path", "doc_refs", "counts", "client_ip", "outcome"}


def test_path_and_refs_are_truncated():
    row = A.build_row(
        principal=Principal(sub="s", name="n", roles=frozenset({"admin"}), auth_via="apikey"),
        level="senior", method="GET", path="/v1/" + "x" * 500, doc_refs=[str(i) for i in range(100)],
        counts={}, client_ip=None, outcome="denied",
    )
    assert len(row["path"]) <= 256 and len(row["doc_refs"]) <= 50


@pytest.mark.skipif(not _TEST_DSN, reason="needs scratch DB (CONFLUENCE_SYNC_TEST_DATABASE_URL)")
def test_insert_roundtrip():
    from sqlalchemy import select
    from app.db.models import UnmaskAuditLog
    from app.db.session import session_scope

    A.write_unmask_audit(
        principal=Principal(sub="t", name="t", roles=frozenset({"admin"}), auth_via="apikey"),
        level="senior", method="GET", path="/v1/x", doc_refs=["1"], counts={"pii": 1}, client_ip="127.0.0.1",
        outcome="granted",
    )
    with session_scope() as s:
        assert s.scalar(select(UnmaskAuditLog).order_by(UnmaskAuditLog.id.desc()).limit(1)).outcome == "granted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_audit.py -q`
Expected: FAIL — `ModuleNotFoundError: app.masking.audit`

- [ ] **Step 3: Write minimal implementation**

`apps/api/app/db/models.py` — `ApiAccessLog` 바로 뒤에 추가 (기존 import의 `BigInteger, DateTime, Index, Integer, String, func, JSONB`는 이미 있다):

```python
class UnmaskAuditLog(Base):
    """2026-11-09 — who revealed pii/net originals (or tried and was denied). Written synchronously
    before an original response leaves the API; never pruned. Holds counts and ids, never the values."""

    __tablename__ = "unmask_audit_log"
    __table_args__ = (Index("ix_unmask_audit_log_created_at", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    sub: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(128))
    roles: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(8), nullable=False)
    path: Mapped[str] = mapped_column(String(256), nullable=False)
    doc_refs: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    counts: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)
    client_ip: Mapped[Optional[str]] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # granted | denied | audit_failed
```

`apps/api/alembic/versions/20261109_0024_unmask_audit_log.py`:

```python
"""unmask_audit_log — permanent record of pii/net original views

Revision ID: 20261109_0024
Revises: 20261105_0023
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "20261109_0024"
down_revision: Union[str, None] = "20261105_0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "unmask_audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sub", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128)),
        sa.Column("roles", sa.String(128), nullable=False, server_default=""),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("path", sa.String(256), nullable=False),
        sa.Column("doc_refs", JSONB, nullable=False, server_default="[]"),
        sa.Column("counts", JSONB, nullable=False, server_default="{}"),
        sa.Column("client_ip", sa.String(64)),
        sa.Column("outcome", sa.String(16), nullable=False),
    )
    op.create_index("ix_unmask_audit_log_created_at", "unmask_audit_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_unmask_audit_log_created_at", table_name="unmask_audit_log")
    op.drop_table("unmask_audit_log")
```

`apps/api/app/masking/audit.py`:

```python
"""Synchronous unmask audit insert. Raises on failure — the caller must then NOT send the original."""

from __future__ import annotations

from typing import Any, Optional

from app.auth.principal import Principal
from app.db.models import UnmaskAuditLog
from app.db.session import session_scope


def build_row(
    *, principal: Principal, level: str, method: str, path: str, doc_refs: list, counts: dict,
    client_ip: Optional[str], outcome: str,
) -> dict[str, Any]:
    return {
        "sub": principal.sub[:128],
        "name": (principal.name or "")[:128],
        "roles": ",".join(sorted(principal.roles))[:128],
        "level": level,
        "method": method[:8],
        "path": path[:256],
        "doc_refs": [str(r)[:128] for r in doc_refs[:50]],
        "counts": {k: int(v) for k, v in counts.items()},
        "client_ip": client_ip[:64] if client_ip else None,
        "outcome": outcome,
    }


def write_unmask_audit(**kw: Any) -> None:
    row = build_row(**kw)
    with session_scope() as session:
        session.add(UnmaskAuditLog(**row))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_audit.py -q`
Expected: `2 passed, 1 skipped` (DB 테스트는 scratch DB 환경변수가 있을 때만)

마이그레이션 문법 확인: `cd apps/api && ../../.venv/bin/python -c "import importlib.util as u;s=u.spec_from_file_location('m','alembic/versions/20261109_0024_unmask_audit_log.py');m=u.module_from_spec(s);s.loader.exec_module(m);print(m.revision, m.down_revision)"` → `20261109_0024 20261105_0023`

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/db/models.py apps/api/alembic/versions/20261109_0024_unmask_audit_log.py apps/api/app/masking/audit.py apps/api/tests/test_masking_audit.py
git commit -m "feat(masking): unmask_audit_log 테이블과 동기 기록(값 없이 건수·ID만, 보관기간 정리 없음)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `MaskingMiddleware`

**Files:**
- Create: `apps/api/app/masking/middleware.py`
- Test: `apps/api/tests/test_masking_middleware.py`

- [ ] **Step 1: Write the failing test**

```python
"""MaskingMiddleware on a mini app. Principal/settings/audit are patched; no DB."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.masking.middleware as MW
from app.auth.principal import ANON_OPEN, ANON_RESTRICTED, Principal
from app.masking.dictionary import Dictionary

BODY = "문의 031-123-4567 서버 10.0.0.1 이상"


def _p(*roles):
    return Principal(sub="u1", name="u1", roles=frozenset(roles), auth_via="apikey")


KEYS = {"viewer": _p("viewer"), "author": _p("author"), "senior": _p("senior"), "admin": _p("admin")}


@pytest.fixture
def env(monkeypatch):
    audit: list = []
    state = {"mode": "apikey", "audit_fail": False}

    class S:
        @property
        def auth_mode(self):
            return state["mode"]

    monkeypatch.setattr(MW, "get_settings", lambda: S())
    monkeypatch.setattr(MW, "get_dictionary", lambda: Dictionary.empty("missing"))
    monkeypatch.setattr(
        MW, "resolve_principal",
        lambda authorization=None, api_key=None: KEYS.get(api_key or "", ANON_RESTRICTED if state["mode"] != "off" else ANON_OPEN),
    )

    def fake_audit(**kw):
        if state["audit_fail"]:
            raise RuntimeError("db down")
        audit.append(kw)

    monkeypatch.setattr(MW, "write_unmask_audit", fake_audit)

    app = FastAPI()
    app.add_middleware(MW.MaskingMiddleware)

    @app.get("/v1/doc")
    def doc():
        return {"external_id": "T-1", "body_md": BODY}

    @app.get("/v1/list")
    def lst():
        return [{"body_md": BODY}]

    @app.get("/v1/health")
    def health():
        return {"note": BODY}

    @app.get("/other")
    def other():
        return {"note": BODY}

    @app.get("/v1/boom")
    def boom():
        return {"body_md": BODY}

    return TestClient(app), audit, state


def test_viewer_masked_with_meta_and_header(env):
    c, audit, _ = env
    r = c.get("/v1/doc", headers={"X-API-Key": "viewer"})
    assert r.json()["body_md"] == "문의 [PHONE-1] 서버 [IP-1] 이상"
    assert r.json()["masking"]["counts"] == {"pii": 1, "net": 1}
    assert r.headers["x-citec-masking"] == "net=1,pii=1" and not audit


def test_array_response_masked_header_only(env):
    c, _, _ = env
    r = c.get("/v1/list", headers={"X-API-Key": "author"})
    assert r.json() == [{"body_md": "문의 [PHONE-1] 서버 [IP-1] 이상"}]
    assert r.headers["x-citec-masking"] == "net=1,pii=1"


def test_author_cannot_unmask_denied_and_audited(env):
    c, audit, _ = env
    r = c.get("/v1/doc?unmask=1", headers={"X-API-Key": "author"})
    assert "[PHONE-1]" in r.json()["body_md"]
    assert r.json()["masking"]["unmask_denied"] is True
    assert audit[0]["outcome"] == "denied"


def test_senior_default_masked_toggle_reveals_and_audits(env):
    c, audit, _ = env
    assert "[PHONE-1]" in c.get("/v1/doc", headers={"X-API-Key": "senior"}).json()["body_md"]
    assert not audit
    r = c.get("/v1/doc", headers={"X-API-Key": "admin", "X-Citec-Unmask": "1"})
    assert r.json()["body_md"] == BODY
    assert audit[0]["outcome"] == "granted" and audit[0]["counts"] == {"pii": 1, "net": 1}
    assert audit[0]["doc_refs"] == ["T-1"]


def test_audit_failure_keeps_masked(env):
    c, _, state = env
    state["audit_fail"] = True
    r = c.get("/v1/doc?unmask=1", headers={"X-API-Key": "admin"})
    assert "[PHONE-1]" in r.json()["body_md"] and r.json()["masking"]["audit_failed"] is True


def test_auth_off_nobody_unmasks(env):
    c, audit, state = env
    state["mode"] = "off"
    r = c.get("/v1/doc?unmask=1")
    assert "[PHONE-1]" in r.json()["body_md"] and r.json()["masking"]["unmask_denied"] is True
    assert audit[0]["outcome"] == "denied"


def test_out_of_scope_and_skipped_paths_untouched(env):
    c, _, _ = env
    assert c.get("/other").json()["note"] == BODY
    assert c.get("/v1/health").json()["note"] == BODY


def test_engine_error_is_fail_closed(env, monkeypatch):
    c, _, _ = env
    monkeypatch.setattr(MW, "mask_json", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    r = c.get("/v1/boom", headers={"X-API-Key": "viewer"})
    assert r.status_code == 500 and BODY not in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_middleware.py -q`
Expected: FAIL — `ModuleNotFoundError: app.masking.middleware`

- [ ] **Step 3: Write minimal implementation** — `apps/api/app/masking/middleware.py`

```python
"""Response-masking middleware (spec §4–§6). Fail-closed: any error → 500, never the original."""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.audit.api_access import client_ip
from app.auth.principal import resolve_principal
from app.masking.audit import write_unmask_audit
from app.masking.dictionary import get_dictionary
from app.masking.engine import Masker, mask_json
from app.masking.policy import decide
from app.settings import get_settings

logger = logging.getLogger("citec.masking")

IN_SCOPE = ("/v1/", "/api/")
SKIP_PREFIXES = ("/v1/health", "/v1/ops/", "/v1/auth/", "/api/docs", "/api/openapi")
_REF_KEYS = ("document_id", "external_id")


def in_scope(path: str) -> bool:
    return path.startswith(IN_SCOPE) and not path.startswith(SKIP_PREFIXES)


def _unmask_requested(request: Request) -> bool:
    q = request.query_params.get("unmask", "").lower()
    return q in ("1", "true") or request.headers.get("x-citec-unmask", "").strip() == "1"


def _doc_refs(obj: Any, out: list, depth: int = 0) -> None:
    if depth > 3 or len(out) >= 50:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _REF_KEYS and isinstance(v, (str, int)):
                out.append(str(v))
            else:
                _doc_refs(v, out, depth + 1)
    elif isinstance(obj, list):
        for x in obj[:100]:
            _doc_refs(x, out, depth + 1)


class MaskingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not in_scope(request.url.path):
            return await call_next(request)
        response = await call_next(request)
        if "application/json" not in response.headers.get("content-type", ""):
            return response
        raw = b"".join([chunk async for chunk in response.body_iterator])
        try:
            return await run_in_threadpool(self._process, request, response, raw)
        except Exception:  # noqa: BLE001 — fail closed
            logger.exception("masking failed for %s %s", request.method, request.url.path)
            return JSONResponse({"detail": "masking failed"}, status_code=500)

    def _process(self, request: Request, response: Response, raw: bytes) -> Response:
        if not raw:
            return self._rebuild(response, raw, {})
        principal = resolve_principal(
            authorization=request.headers.get("authorization"), api_key=request.headers.get("x-api-key")
        )
        d = decide(principal, get_settings().auth_mode, _unmask_requested(request))
        data = json.loads(raw)

        # When an original is granted we still run pii/net masking: it counts what would be revealed
        # (for the audit row) and is the fallback body if the audit write fails.
        groups = frozenset({"pii", "net"}) if d.unmask_granted else d.groups
        masker = Masker(groups, get_dictionary())
        masked = mask_json(data, masker)
        counts = {k: v for k, v in masker.counts.items() if v}
        refs: list = []
        _doc_refs(data, refs)
        ip = client_ip(request.headers, request.client.host if request.client else None)
        audit_kw = dict(principal=principal, level=d.level, method=request.method, path=request.url.path,
                        doc_refs=refs, counts=counts, client_ip=ip)

        body_obj, meta = masked, {}
        if d.unmask_granted:
            if counts:
                try:
                    write_unmask_audit(outcome="granted", **audit_kw)
                    body_obj, meta = data, {"unmask_granted": True, "revealed": counts}
                except Exception:  # noqa: BLE001 — no audit row → no original
                    logger.exception("unmask audit write failed; serving masked")
                    meta = {"audit_failed": True}
            else:
                body_obj = data
        elif d.unmask_denied:
            meta = {"unmask_denied": True}
            try:
                write_unmask_audit(outcome="denied", **{**audit_kw, "counts": {}})
            except Exception:  # noqa: BLE001
                logger.exception("denied-unmask audit write failed")

        if (counts and body_obj is masked) or meta:
            info = {"applied": body_obj is masked, "level": d.level, "counts": counts, **meta}
            if isinstance(body_obj, dict):
                body_obj = {**body_obj, "masking": info}
            return self._rebuild(response, json.dumps(body_obj, ensure_ascii=False).encode(), info)
        return self._rebuild(response, json.dumps(body_obj, ensure_ascii=False).encode(), {})

    @staticmethod
    def _rebuild(response: Response, body: bytes, info: dict) -> Response:
        out = Response(content=body, status_code=response.status_code)
        out.raw_headers = [(k, v) for k, v in response.raw_headers if k.lower() != b"content-length"]
        out.raw_headers.append((b"content-length", str(len(body)).encode()))
        counts = info.get("counts") or {}
        if info.get("applied") and counts:
            out.headers["X-Citec-Masking"] = ",".join(f"{k}={v}" for k, v in sorted(counts.items()))
        return out
```

참고: 응답이 이미 `masking`을 갖는 경우를 위해 `{**body_obj, "masking": info}`가 덮어쓴다.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_middleware.py -q`
Expected: `8 passed`. (헤더는 `sorted(counts.items())`로 조립하므로 `net=1,pii=1` 순서로 고정된다.)

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/masking/middleware.py apps/api/tests/test_masking_middleware.py
git commit -m "feat(masking): MaskingMiddleware(fail-closed, 원본은 감사 기록 성공 후에만)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 앱 연결, 헬스 상태, 라우트 회귀 테스트

**Files:**
- Modify: `apps/api/app/main.py` (import 블록, 미들웨어 등록 ~line 100, `health()` ~line 195-230)
- Test: `apps/api/tests/test_masking_routes.py`

- [ ] **Step 1: Write the failing test**

```python
"""Regression: the app is masked and no route can sit outside the masked URL space."""

from app.main import app
from app.masking.middleware import IN_SCOPE, MaskingMiddleware

# FastAPI 내장 문서/루트 — 데이터 응답이 아니다.
OUT_OF_SCOPE_OK = {"/", "/docs", "/docs/oauth2-redirect", "/openapi.json", "/redoc"}


def test_middleware_installed():
    assert any(m.cls is MaskingMiddleware for m in app.user_middleware)


def test_every_route_is_in_masked_url_space():
    stray = sorted(
        {r.path for r in app.routes if hasattr(r, "path")}
        - OUT_OF_SCOPE_OK
        - {p for p in (r.path for r in app.routes if hasattr(r, "path")) if p.startswith(IN_SCOPE)}
    )
    assert not stray, f"routes outside {IN_SCOPE} bypass masking: {stray}"


def test_health_reports_masking_status():
    from app.main import _masking_health

    h = _masking_health()
    assert h["dictionary"] in ("ok", "missing") or h["dictionary"].startswith("error")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_routes.py -q`
Expected: FAIL — `test_middleware_installed` False, `ImportError: _masking_health`

- [ ] **Step 3: Write minimal implementation** — `apps/api/app/main.py`

import에 추가:

```python
from app.masking.dictionary import get_dictionary
from app.masking.middleware import MaskingMiddleware
```

`app.add_middleware(ApiAccessLogMiddleware)` 바로 아래에 추가:

```python
# 2026-11-09 — pii/net/biz 출력 시점 마스킹 + 원본 보기 토글(docs/superpowers/specs/2026-11-09-pii-masking-design.md).
app.add_middleware(MaskingMiddleware)
```

`health()` 위(`class HealthResponse` 아래)에 헬퍼 추가:

```python
def _masking_health() -> dict[str, Any]:
    status = get_dictionary().status
    return {"ok": status in ("ok", "missing"), "dictionary": status}
```

`health()` 안 `checks["llm"] = {...}` 블록 바로 뒤에 추가:

```python
    checks["masking"] = _masking_health()
```

(`critical_ok` 계산에는 넣지 않는다 — 사전 문제로 컨테이너 헬스체크가 실패하면 안 된다.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/api && ../../.venv/bin/python -m pytest tests/test_masking_routes.py tests/test_masking_middleware.py -q`
Expected: all pass. `test_every_route_is_in_masked_url_space`가 실패해 stray 라우트가 나오면, 그 라우트가 데이터를 내보내는지 확인하고 `/v1`·`/api` 아래로 옮기거나(권장) 데이터가 없는 경로면 `OUT_OF_SCOPE_OK`에 이유와 함께 추가한다.

- [ ] **Step 5: 전체 회귀**

Run: `cd apps/api && PYTHONPATH=. APP_ENV=test AUTH_MODE=off DATABASE_URL=postgresql+psycopg://citec:citec@127.0.0.1:1/citec_knowledge REDIS_URL=redis://127.0.0.1:1/0 ../../.venv/bin/python -m pytest tests -q --tb=line --ignore=tests/test_mock_idp_e2e.py 2>&1 | tail -5`
Expected: 기존 테스트 전부 통과(CI와 동일 환경변수). 기존 테스트가 `masking` 필드나 헤더 때문에 깨지면 해당 테스트가 미들웨어를 거치는지 확인하고 원인을 보고한다(임의로 테스트를 고치지 않는다).

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/main.py apps/api/tests/test_masking_routes.py
git commit -m "feat(masking): 미들웨어 등록, /v1/health masking 상태, 라우트 우회 회귀 테스트

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 웹 UI — 원본 보기 토글·붉은 띠·배지

**Files:**
- Modify: `apps/web/public/js/auth.js` (api 객체 위, `global.CitecAuth = api;` 앞)
- Modify: `apps/web/public/js/nav.js` (`init()`)
- Modify: `apps/web/public/css/theme.css` (파일 끝)

JS 단위 테스트 러너가 없으므로 이 태스크는 **수동 검증 절차**로 확인한다.

- [ ] **Step 1: `auth.js` — 토글 상태와 전역 `fetch` 래퍼**

`var api = {` 바로 위에 추가:

```js
  // ---- 원본 보기 토글 (senior/admin 전용 UI). 상태는 탭 단위 sessionStorage. ----
  var UNMASK_KEY = "citec_kb_unmask";

  function unmaskOn() {
    try {
      return sessionStorage.getItem(UNMASK_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function setUnmask(on) {
    try {
      if (on) sessionStorage.setItem(UNMASK_KEY, "1");
      else sessionStorage.removeItem(UNMASK_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  // 모든 페이지의 fetch(/v1|/api …)에 헤더를 붙인다. 페이지마다 apiFetch를 쓰지 않으므로 전역 래퍼로 처리.
  if (typeof global.fetch === "function" && !global.fetch.__citecUnmask) {
    var origFetch = global.fetch.bind(global);
    var wrapped = function (url, opts) {
      try {
        var u = typeof url === "string" ? url : (url && url.url) || "";
        if (unmaskOn() && /^(\/|https?:\/\/[^/]+\/)(v1|api)\//.test(u)) {
          opts = opts || {};
          var h = new Headers(opts.headers || {});
          h.set("X-Citec-Unmask", "1");
          opts.headers = h;
        }
      } catch (e) {
        /* fall through to the plain call */
      }
      return origFetch(url, opts);
    };
    wrapped.__citecUnmask = true;
    global.fetch = wrapped;
  }
```

`api` 객체에 `unmaskOn: unmaskOn, setUnmask: setUnmask,` 두 항목 추가.

- [ ] **Step 2: `nav.js` — 토글과 붉은 띠**

`init()` 뒤, 이벤트 등록 앞에 함수를 추가하고 `init()`에서 호출:

```js
  function mountUnmask(container) {
    var A = global.CitecAuth;
    if (!A || !A.me) return;
    A.me().then(function (res) {
      var roles = ((res.data && res.data.principal) || {}).roles || [];
      var allowed = roles.indexOf("senior") >= 0 || roles.indexOf("admin") >= 0;
      if (!allowed) {
        A.setUnmask(false);
        return;
      }
      var label = document.createElement("label");
      label.className = "unmask-toggle";
      label.innerHTML = '<input type="checkbox"> 원본 보기';
      var box = label.querySelector("input");
      box.checked = A.unmaskOn();
      box.addEventListener("change", function () {
        A.setUnmask(box.checked);
        location.reload();
      });
      container.appendChild(label);
      if (A.unmaskOn()) {
        var bar = document.createElement("div");
        bar.className = "unmask-banner";
        bar.textContent = "원본 표시 중 · 열람이 기록됩니다";
        document.body.insertBefore(bar, document.body.firstChild);
      }
    }).catch(function () {});
  }
```

`init()` 안 `render(nodes[i]);` 다음 줄에 `mountUnmask(nodes[i]);` 추가. (`auth.js`가 nav.js보다 먼저 로드되지 않는 페이지는 `global.CitecAuth`가 없어 조용히 건너뛴다 — Step 4에서 각 페이지의 스크립트 순서를 확인한다.)

- [ ] **Step 3: `theme.css` 끝에 추가**

```css
.unmask-toggle { margin-left: 12px; font-size: 12px; color: #fde68a; cursor: pointer; white-space: nowrap; }
.unmask-banner { position: sticky; top: 0; z-index: 1000; background: #b91c1c; color: #fff; text-align: center;
  font-size: 13px; font-weight: 600; padding: 4px 8px; }
.mask-badge { background: #e5e7eb; color: #4b5563; border-radius: 4px; padding: 0 4px; font-size: 0.9em;
  font-family: ui-monospace, monospace; }
```

- [ ] **Step 4: 토큰 배지 렌더링과 스크립트 순서**

`apps/web/public/js/markdown.js`의 HTML 이스케이프가 끝난 지점(텍스트 노드를 만드는 `inline` 계열 함수, 파일 열어 확인)에서 이스케이프된 문자열에 다음 치환을 한 번 적용한다:

```js
s = s.replace(/\[(PHONE|ADDR|ID|NAME|IP|CUST|HOST)-(\d+)\]/g, '<span class="mask-badge">[$1-$2]</span>');
```

`nav.js`를 로드하는 모든 HTML에서 `auth.js`가 `nav.js`보다 **앞**에 오는지 확인한다:
`grep -n 'js/nav.js\|js/auth.js' apps/web/public/*.html` — `nav.js`가 먼저인 페이지는 `auth.js` 줄을 `nav.js` 위로 옮긴다.

- [ ] **Step 5: 수동 검증**

1. 로컬 스택에서 `AUTH_MODE=off`로 `search.html` 접속 → 토글이 **보이지 않음**.
2. `AUTH_MODE=apikey` + `senior` 토큰으로 로그인(`login.html`) → 상단바에 "원본 보기" 표시.
3. 토글 ON → 붉은 띠 표시, 개발자도구 Network에서 `/v1/...` 요청에 `X-Citec-Unmask: 1` 확인.
4. 탭을 닫았다 열면 토글 OFF.
5. `author` 토큰으로 로그인 → 토글 없음, 응답에 `[PHONE-1]` 배지 표시.

- [ ] **Step 6: Commit**

```bash
git add apps/web/public/js/auth.js apps/web/public/js/nav.js apps/web/public/js/markdown.js apps/web/public/css/theme.css apps/web/public/*.html
git commit -m "feat(web): 원본 보기 토글(senior/admin), 붉은 안내 띠, 마스킹 토큰 배지

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: MCP 안내와 문서

**Files:**
- Modify: `mcp-server/server.py` (`_get_document_impl`, `kb_ticket`, `_search_impl`, `kb_tools_help`)
- Modify: `docs/AI_AGENT_GUIDE.md`
- Test: `mcp-server/test_masking_note.py`

MCP는 API 응답을 그대로 가공하므로 마스킹 자체는 코드 변경이 필요 없다. 여기서는 (a) 마스킹이 적용됐음을 알리는 한 줄과 (b) 복원 시도 금지 안내만 추가한다. 헤더 `X-Citec-Masking`은 JSON 배열 응답에도 오므로 헤더를 기준으로 삼는다.

- [ ] **Step 1: Write the failing test** — `mcp-server/test_masking_note.py`

```python
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import server  # noqa: E402


class R:
    def __init__(self, h):
        self.headers = h


def test_note_from_header():
    assert server._masking_note(R({"x-citec-masking": "net=1,pii=3"})) == "마스킹 적용: net 1, pii 3 (토큰 [PHONE-1] 등은 마스킹된 값이며 복원하지 말 것)\n"


def test_no_note_without_header():
    assert server._masking_note(R({})) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mcp-server && ../.venv/bin/python -m pytest test_masking_note.py -q`
Expected: FAIL — `AttributeError: module 'server' has no attribute '_masking_note'`

- [ ] **Step 3: Write minimal implementation** — `server.py`의 `_api_error_detail` 위에 추가

```python
def _masking_note(resp: httpx.Response) -> str:
    """API가 pii/net을 마스킹했으면(X-Citec-Masking 헤더) 응답 머리에 붙일 한 줄. 없으면 빈 문자열."""
    raw = (resp.headers.get("x-citec-masking") or "").strip()
    if not raw:
        return ""
    parts = ", ".join(p.replace("=", " ") for p in raw.split(","))
    return f"마스킹 적용: {parts} (토큰 [PHONE-1] 등은 마스킹된 값이며 복원하지 말 것)\n"
```

호출 지점 세 곳에서 `resp`를 받은 직후 결과 문자열 앞에 붙인다.
- `_get_document_impl`: 응답 문자열을 만드는 마지막 `return` 앞에서 `note = _masking_note(resp)`를 계산하고 반환 문자열을 `note + <기존 문자열>`로 바꾼다.
- `kb_ticket`: 이미 `note` 변수(입력 해석 안내)가 있으므로 `note = _masking_note(resp) + note`.
- `_search_impl`: 결과 목록 문자열 첫 줄 앞에 `_masking_note(resp)`를 붙인다.

각 함수의 `resp` 변수 이름과 반환부는 해당 위치를 열어 확인하고 최소 변경으로 적용한다.

`kb_tools_help()`의 안내 문자열 끝에 추가:

```
[마스킹]
  응답의 [PHONE-1] [ADDR-1] [ID-1] [NAME-1] [IP-1] 은 개인정보/IP가 마스킹된 값이다. 복원·추측하지 말고
  토큰 그대로 인용한다. 같은 응답 안에서 같은 번호는 같은 값이다. 고객사명·호스트명은 author 권한에서 원본이다.
```

`docs/AI_AGENT_GUIDE.md`에 `## 마스킹` 절을 추가해 같은 내용과 함께 "원본 보기는 사람(senior/admin) 웹 UI 전용이며 MCP로는 열 수 없다"를 적는다.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mcp-server && ../.venv/bin/python -m pytest test_masking_note.py test_search_format.py test_ticket_lookup.py -q`
Expected: 모두 통과 (기존 MCP 테스트가 깨지지 않아야 한다).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/server.py mcp-server/test_masking_note.py docs/AI_AGENT_GUIDE.md
git commit -m "feat(mcp): 마스킹 적용 안내 한 줄과 복원 금지 가이드

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 실데이터 점검 스크립트와 운영 문서

**Files:**
- Create: `scripts/masking_probe.py`
- Create: `docs/MASKING_RUNBOOK.md`

- [ ] **Step 1: `scripts/masking_probe.py` 작성**

```python
#!/usr/bin/env python3
"""Count what the masker finds in real documents WITHOUT printing any value.

  python3 scripts/masking_probe.py --ancestor 224797007            # 설문 및 수요조사 트리
  python3 scripts/masking_probe.py --source-type support_history --limit 200
Needs DATABASE_URL (same as the API). Output: per-group counts per document + totals only.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))

from sqlalchemy import text  # noqa: E402

from app.db.session import session_scope  # noqa: E402
from app.masking import Masker, get_dictionary  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ancestor", help="confluence_map ancestor page id")
    ap.add_argument("--source-type", default="confluence_map")
    ap.add_argument("--limit", type=int, default=500)
    a = ap.parse_args()

    where, params = "source_type = :st", {"st": a.source_type, "lim": a.limit}
    if a.ancestor:
        where += " AND metadata->'ancestor_ids' ? :anc"
        params["anc"] = a.ancestor
    sql = f"SELECT external_id, left(title, 40), body_md FROM documents WHERE {where} AND length(body_md) > 100 LIMIT :lim"

    total: Counter = Counter()
    docs_with_hits = 0
    dic = get_dictionary()
    with session_scope() as s:
        rows = s.execute(text(sql), params).all()
    for ext, title, body in rows:
        mk = Masker(frozenset({"pii", "net", "biz"}), dic)
        mk.mask_text(body or "")
        if mk.counts:
            docs_with_hits += 1
            print(f"{ext}\t{title}\t{dict(mk.counts)}")
        total.update(mk.counts)
    print(f"\ndocs scanned={len(rows)} with_hits={docs_with_hits} totals={dict(total)} dictionary={dic.status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 실행 확인**

Run (운영/로컬 스택의 DB가 보이는 환경, 예: api 컨테이너 안):
`docker compose exec -T api python /app/scripts/masking_probe.py --ancestor 224797007` — 스크립트가 컨테이너에 없으면 `python3 scripts/masking_probe.py --ancestor 224797007`를 `DATABASE_URL`을 맞춰 호스트에서 실행한다.
Expected: 문서별 `{'pii': n, 'net': m}` 건수와 합계만 출력되고 값은 출력되지 않는다. 설계 확인 시점의 기대치: 전화번호 17건·주소 15건이 있던 문서들에서 `pii` 히트가 나와야 한다(0건이면 정규식이 실데이터를 못 잡는 것이므로 Task 1·3로 돌아가 원인을 찾는다). 누락이 의심되는 문서는 값을 옮기지 말고 `re.sub(r'\d','9', …)`로 형태만 만들어 패턴을 비교한다.

- [ ] **Step 3: `docs/MASKING_RUNBOOK.md` 작성** — 다음 항목을 짧게 적는다.
  - 가시성 표(롤 × 분류)와 `AUTH_MODE=off`일 때 동작(익명=author 수준, 원본 불가).
  - `config/masking_dictionary.json` 작성법(`.example` 참조), 즉시 반영(재시작 불필요), `/v1/health`의 `checks.masking`으로 상태 확인.
  - 원본 열람 조회: `SELECT created_at, sub, roles, path, counts, outcome FROM unmask_audit_log ORDER BY id DESC LIMIT 50;`
  - 배포: `docker compose exec api alembic upgrade head` 후 `docker compose up -d --build api web`.
  - 롤백: 미들웨어 등록 줄 한 줄을 되돌리고 api 재빌드(감사 테이블은 남겨 둔다).
  - 알려진 한계: 사번은 `사번` 라벨/열 문맥에서만 탐지, 이름은 개인정보가 걸린 줄에서만, 지번주소 일부 누락 가능, 임베딩 원문·Confluence 직접 조회는 범위 밖.

- [ ] **Step 4: Commit**

```bash
git add scripts/masking_probe.py docs/MASKING_RUNBOOK.md
git commit -m "feat(masking): 실데이터 점검 스크립트(건수만 출력)와 운영 런북

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage**

| 스펙 절 | 구현 태스크 |
|---|---|
| §2 분류·롤 표, AUTH_MODE=off | Task 4 (`decide`), Task 6 (`test_auth_off_nobody_unmasks`) |
| §3 탐지기·치환·allowlist·사전 재로딩 | Task 1, 2, 3 |
| §4 미들웨어·범위·제외·fail-closed·`masking` 필드 | Task 6, 7 (조정 1: 필드는 조건부 + 헤더) |
| §5 원본 토글·거부 시 마스킹 유지·감사 선행 | Task 6 |
| §6 `unmask_audit_log` 동기·영구 | Task 5 (보관 정리 코드를 만들지 않음), Task 6 (조정 2) |
| §7 웹 UI 토글·띠·배지 | Task 8 |
| §8 MCP 안내 | Task 9 |
| §9 테스트(단위·미들웨어·회귀·실데이터) | Task 1–7, 10 |
| 헬스 노출 | Task 7 |

**2. Placeholder scan:** TBD/TODO 없음. Task 8 Step 4와 Task 9 Step 3은 대상 파일의 정확한 줄을 *열어 확인해서* 최소 변경으로 적용하라고 적었다(해당 함수의 변수명·반환 구조가 파일마다 달라 계획 단계에서 한 줄로 고정할 수 없음). 두 곳 모두 삽입할 코드는 전부 적혀 있다.

**3. Type/이름 일관성:** `Masker(groups, dictionary)`·`mask_text`·`.counts`(Task 3) = 미들웨어 사용(Task 6) = 점검 스크립트(Task 10). `Decision` 필드 `level/groups/unmask_requested/unmask_granted/unmask_denied`(Task 4) = 미들웨어 사용. `write_unmask_audit(**kw)`의 키워드(`principal, level, method, path, doc_refs, counts, client_ip, outcome`)는 `build_row`(Task 5)와 미들웨어 `audit_kw`(Task 6)가 일치한다. `Dictionary.empty(status)`/`.status`/`.allow`/`.customer_re`/`.host_res`는 Task 2 정의 = Task 3·6·7 사용.

**알려진 위험**
- 이름 후보 휴리스틱(성씨 시작 2~4자)은 개인정보가 걸린 줄에서 일반 단어를 과마스킹할 수 있다. 스펙의 "애매하면 마스킹" 정책에 따른 것이며 `NAME_STOPWORDS`로 조정한다.
- 미들웨어는 모든 `/v1`·`/api` JSON을 파싱·재직렬화한다. 지연이 문제면 Task 10의 점검 후 `mask_text`에 숫자/한글 사전 필터를 추가한다(YAGNI로 이번엔 넣지 않음).
