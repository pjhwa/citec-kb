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
_COLS = {"sabun": re.compile(r"\d{5}"), "name": re.compile(r"[가-힣]{2,4}")}


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
        state: dict = {}
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
        line = self._columns(line, state, labelled)
        if self.counts["pii"] > before:           # 이 줄에 개인정보가 있으면 같은 줄의 이름 후보도 가린다
            for rx in P.NAME_RULES:
                line = rx.sub(
                    lambda m: m.group(0) if m.group(1) in P.NAME_STOPWORDS
                    else m.group(0).replace(m.group(1), self._sub("name", m.group(1)), 1),
                    line,
                )
        return line

    @staticmethod
    def _cells(line: str) -> list:
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            return parts[1:] if line.lstrip().startswith("|") else parts
        return line.split()

    def _columns(self, line: str, state: dict, labelled: bool) -> str:
        """사번 열·이름 열: 헤더 줄에서 열 위치를 기억하고 뒤따르는 행의 해당 셀을 가린다. 헤더 줄은 바꾸지 않는다."""
        if not line.strip():
            for k in _COLS:
                state[k + "_col"], state[k + "_miss"] = None, 0
            return line
        if not labelled:
            cells = self._cells(line)
            header = False
            if "사번" in cells:
                state["sabun_col"], state["sabun_miss"] = cells.index("사번"), 0
                header = True
            for h in P.NAME_HEADERS:
                if h in cells:
                    state["name_col"], state["name_miss"] = cells.index(h), 0
                    header = True
                    break
            if header:
                return line
        for k, rx in _COLS.items():
            col = state.get(k + "_col")
            if col is None:
                continue
            line, hit = self._replace_cell(line, col, rx, k)
            if hit:
                state[k + "_miss"] = 0
            else:
                state[k + "_miss"] = state.get(k + "_miss", 0) + 1
                if state[k + "_miss"] >= 2:
                    state[k + "_col"], state[k + "_miss"] = None, 0
        return line

    def _replace_cell(self, line: str, col: int, rx: "re.Pattern", kind: str) -> tuple:
        if "|" in line:
            parts = line.split("|")
            i = col + (1 if line.lstrip().startswith("|") else 0)
            if i < len(parts) and rx.fullmatch(parts[i].strip()):
                parts[i] = re.sub(rx.pattern, lambda m: self._sub(kind, m.group(0)), parts[i], count=1)
                return "|".join(parts), True
            return line, False
        toks = list(_TOKEN.finditer(line))
        if col < len(toks) and rx.fullmatch(toks[col].group(0)):
            t = toks[col]
            return line[: t.start()] + self._sub(kind, t.group(0)) + line[t.end():], True
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
