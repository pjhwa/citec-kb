"""config/masking_dictionary.json loader (customers, hostname patterns, allowlist).

Reloads on the next call after the file's (mtime, size, inode) changes, like config/auth.json.
A missing/broken file disables `biz` masking only; `pii`/`net` are unaffected.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
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
        # 타임스탬프 해상도보다 빠른 연속 수정을 놓치지 않도록(git의 "racy" 처리와 같은 발상),
        # 방금 수정된 파일은 내용 해시를 키에 포함한다.
        if time.time_ns() - st.st_mtime_ns < 2_000_000_000:
            key += (hashlib.sha1(path.read_bytes()).hexdigest(),)
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
