"""LLM-assisted relevance judging. The LLM only *suggests*; evidence quotes are verified in code.

Error policy: an exception raised by ``chat`` (transport/auth/timeouts) is NOT swallowed here; it
propagates to the caller (the CLI decides whether to retry, skip the query or abort). Only an
empty/blocked response is retried once and then recorded as status "empty".
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Callable
from typing import Any

from app.evalv2.llmjson import extract_json

MIN_EVIDENCE_LEN = 8
EXCERPT_CHARS = 1200

RULES = (
    "당신은 사내 기술지원 지식베이스의 검색 평가자입니다. 질의와 후보 문서들을 보고, "
    "각 후보가 질의에 답하는 데 도움이 되는 정도를 판정하세요.\n"
    "등급: 2 = 질의에 직접 답하는 문서, 1 = 관련은 있으나 부분적, 0 = 무관.\n"
    "등급이 1 또는 2이면 해당 후보 본문에서 **글자 그대로 복사한** 근거 문장(8자 이상)을 evidence에 넣으세요. "
    "본문에 없는 문장을 만들거나 요약하지 마세요.\n"
    '반드시 JSON으로만 답하세요: {"judgments":[{"id":"c1","grade":2,"evidence":"…"}]}'
)


def _n(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s or "").split())


def build_messages(query: str, cands: list[dict[str, Any]]) -> list[dict[str, str]]:
    blocks = []
    for i, c in enumerate(cands, 1):
        excerpt = _n(c.get("excerpt") or "")[:EXCERPT_CHARS]
        blocks.append(f"[c{i}] 제목: {_n(c.get('title') or '')}\n본문: {excerpt}")
    content = f"{RULES}\n\n질의: {_n(query)}\n\n후보 문서:\n" + "\n\n".join(blocks)
    return [{"role": "user", "content": content}]


def verify_evidence(evidence: str, body: str) -> bool:
    ev = _n(evidence)
    return len(ev) >= MIN_EVIDENCE_LEN and ev in _n(body)


def _judge_chunk(
    chat: Callable[..., str], query: str, chunk: list[dict[str, Any]],
    bodies: dict[str, str], sleep: Callable[[float], None],
) -> dict[str, dict[str, Any]]:
    raw = ""
    for attempt in range(2):
        raw = chat(build_messages(query, chunk), max_tokens=2500, temperature=0.0, timeout=180.0)
        if (raw or "").strip():
            break
        if attempt == 0:
            sleep(2.0)
    if not (raw or "").strip():
        return {c["external_id"]: {"grade": None, "evidence": "", "status": "empty"} for c in chunk}
    data = extract_json(raw)
    items = data.get("judgments") if isinstance(data, dict) else None
    by_id: dict[str, dict] = {}
    for it in items if isinstance(items, list) else []:
        if isinstance(it, dict) and isinstance(it.get("id"), str):
            by_id[it["id"].strip().strip("[]")] = it
    out: dict[str, dict[str, Any]] = {}
    for i, c in enumerate(chunk, 1):
        eid = c["external_id"]
        it = by_id.get(f"c{i}")
        if it is None:
            out[eid] = {"grade": None, "evidence": "", "status": "missing"}
            continue
        grade = it.get("grade")
        evidence = str(it.get("evidence") or "")
        if isinstance(grade, bool) or grade not in (0, 1, 2):
            out[eid] = {"grade": None, "evidence": "", "status": "invalid"}
        elif grade == 0:
            out[eid] = {"grade": 0, "evidence": "", "status": "ok"}
        elif verify_evidence(evidence, bodies.get(eid, "")):
            out[eid] = {"grade": grade, "evidence": evidence, "status": "ok"}
        else:
            out[eid] = {"grade": None, "evidence": evidence, "status": "unverified"}
    return out


def judge_candidates(
    chat: Callable[..., str], query: str, cands: list[dict[str, Any]], bodies: dict[str, str],
    *, chunk_size: int = 8, sleep: Callable[[float], None] = time.sleep,
) -> dict[str, dict[str, Any]]:
    """external_id -> {grade 0/1/2/None, evidence, status ok|unverified|invalid|missing|empty}."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(cands), chunk_size):
        out.update(_judge_chunk(chat, query, cands[i : i + chunk_size], bodies, sleep))
    return out
