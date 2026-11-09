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
