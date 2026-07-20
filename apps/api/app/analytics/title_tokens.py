"""Title token frequency analytics (no LLM)."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Optional

from sqlalchemy import select

from app.db.models import Document
from app.db.session import session_scope

_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣][A-Za-z0-9가-힣_./-]{1,}")
_STOP = {
    "및",
    "관련",
    "대한",
    "위한",
    "있는",
    "없는",
    "//",
    "the",
    "and",
    "for",
    "with",
    "from",
    "issue",
    "jira",
    "citec",
    "citecsts",
}


def title_token_stats(
    *,
    source_type: str = "support_history",
    component: Optional[str] = None,
    top_k: int = 20,
    min_len: int = 2,
) -> dict[str, Any]:
    """Count tokens in document titles, optionally filtered by Component metadata."""
    top_k = max(1, min(int(top_k), 100))
    with session_scope() as session:
        stmt = (
            select(Document)
            .where(Document.status == "active")
            .where(Document.source_type == source_type)
        )
        docs = list(session.scalars(stmt).all())
        rows: list[str] = []
        for d in docs:
            meta = d.metadata_ if isinstance(d.metadata_, dict) else {}
            if component:
                c = str(meta.get("Component") or "").strip()
                if c != component and component.lower() not in c.lower():
                    continue
            title = d.title or ""
            # strip [CITECTS-####] prefix noise
            title = re.sub(r"\[CITECTS-\d+\]\s*", "", title)
            rows.append(title)

    counter: Counter[str] = Counter()
    for title in rows:
        for tok in _TOKEN_RE.findall(title):
            t = tok.strip()
            if len(t) < min_len:
                continue
            low = t.lower()
            if low in _STOP or re.fullmatch(r"\d+", t):
                continue
            if re.fullmatch(r"CITECTS-\d+", t, re.I):
                continue
            counter[t] += 1

    buckets = [
        {"token": k, "count": n, "share": round(n / len(rows), 4) if rows else 0.0}
        for k, n in counter.most_common(top_k)
    ]
    return {
        "source_type": source_type,
        "component": component,
        "document_n": len(rows),
        "unique_tokens": len(counter),
        "top_k": top_k,
        "buckets": buckets,
        "method": "title_token_count",
        "llm_used": False,
    }
