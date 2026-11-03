"""Tolerant JSON extraction from LLM text (fences, prose before/after)."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(raw: str | None) -> Any | None:
    text = (raw or "").strip()
    if not text:
        return None
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    elif text.startswith("```"):  # opening fence never closed (e.g. cut off by max_tokens)
        text = re.sub(r"^```[A-Za-z]*\s*", "", text)
    if not text.startswith(("{", "[")):
        starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if not starts:
            return None
        i = min(starts)
        j = max(text.rfind("}"), text.rfind("]"))
        if j <= i:
            return None
        text = text[i : j + 1]
    try:
        return json.loads(text)
    except ValueError:
        pass
    # Unclosed fence / trailing prose containing braces: decode the first complete value only.
    # A truncated (incomplete) object still fails here, so nothing partial is salvaged.
    try:
        val, _ = json.JSONDecoder().raw_decode(text)
        return val
    except ValueError:
        return None
