"""Synonym candidate mining: batch -> LLM proposal -> code verification."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from app.synonyms.rules import (
    MAX_GROUP,
    clean_terms,
    static_covers,
    terms_key,
    verify_group,
)

logger = logging.getLogger("citec.synonyms.mine")

SYSTEM_RULES = (
    "당신은 IT 인프라 기술지원 티켓 검색용 동의어 사전 편집자입니다.\n"
    "아래 티켓 목록(제목/증상)에서, 같은 현상·개념을 가리키지만 표기가 다른 기술 용어 묶음"
    "(한글↔영문, 약어↔풀네임, 표기 변형)을 찾아 JSON으로만 답하세요.\n"
    "규칙:\n"
    "- 각 용어는 인용한 티켓의 텍스트에 글자 그대로 등장해야 합니다 (추측·번역 금지).\n"
    "- 한 묶음은 2~6개 용어, 한글 용어는 3자 이상.\n"
    "- 제품명·고객명·사람 이름, 넓은 분류어(장애, 오류, 서버 등)는 제외.\n"
    "- 단순히 같이 자주 나오는 연관어는 동의어가 아닙니다.\n"
    "- 각 묶음에 근거 티켓 ID를 2개 이상 [대괄호] 안의 ID 그대로 적으세요.\n"
    '출력 형식: {"groups":[{"terms":["..",".."],"tickets":["ID1","ID2"],"reason":"한 줄 근거"}]}\n'
    '없으면 {"groups":[]}'
)


@dataclass(frozen=True)
class Ticket:
    external_id: str
    title: str
    symptom: str
    text: str  # title + body, used only for verification


@dataclass
class Params:
    df_min: int = 3
    df_max: int = 1500
    min_terms_per_ticket: int = 2
    existing_keys: set[str] = field(default_factory=set)


def build_messages(tickets: list[Ticket]) -> list[dict[str, str]]:
    lines = []
    for t in tickets:
        title = " ".join((t.title or "").split())
        sym = " ".join((t.symptom or "").split())[:200]
        lines.append(f"[{t.external_id}] {title} | {sym}".rstrip(" |"))
    return [{"role": "user", "content": SYSTEM_RULES + "\n\n티켓 목록:\n" + "\n".join(lines)}]


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_response(raw: str) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            return []
        text = text[i : j + 1]
    try:
        data = json.loads(text)
    except ValueError:
        return []
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return []
    out = []
    for g in groups:
        if isinstance(g, dict) and isinstance(g.get("terms"), list):
            out.append(
                {
                    "terms": [str(x) for x in g["terms"]],
                    "tickets": [str(x) for x in (g.get("tickets") or [])],
                    "reason": str(g.get("reason") or "")[:300],
                }
            )
    return out


def _new_stats() -> dict[str, int]:
    return {
        "groups_in": 0,
        "accepted": 0,
        "llm_error": 0,
        "rej_terms": 0,
        "rej_static": 0,
        "rej_existing": 0,
        "rej_evidence": 0,
        "rej_df": 0,
    }


def process_groups(
    groups: list[dict[str, Any]],
    tickets: list[Ticket],
    *,
    term_df: Callable[[str], int],
    params: Params,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = _new_stats()
    texts = {t.external_id: t.text for t in tickets}
    items: list[dict[str, Any]] = []
    seen = set(params.existing_keys)
    for g in groups:
        stats["groups_in"] += 1
        terms = clean_terms(g.get("terms") or [])
        if len(terms) < 2:
            stats["rej_terms"] += 1
            continue
        verified = verify_group(
            terms, g.get("tickets") or [], texts, min_terms_per_ticket=params.min_terms_per_ticket
        )
        if verified is None:
            stats["rej_evidence"] += 1
            continue
        kept, ticket_ids = verified
        if static_covers(kept):
            stats["rej_static"] += 1
            continue
        dfs = {t: term_df(t) for t in kept}
        kept = [t for t in kept if params.df_min <= dfs[t] <= params.df_max]
        if len(kept) < 2:
            stats["rej_df"] += 1
            continue
        key = terms_key(kept)
        if key in seen:
            stats["rej_existing"] += 1
            continue
        seen.add(key)
        items.append(
            {
                "terms": kept[:MAX_GROUP],
                "evidence": {
                    "tickets": ticket_ids,
                    "df": {t: dfs[t] for t in kept},
                    "reason": g.get("reason", ""),
                    "min_terms_per_ticket": params.min_terms_per_ticket,
                },
            }
        )
        stats["accepted"] += 1
    return items, stats


def mine_batch(
    tickets: list[Ticket],
    *,
    chat: Callable[..., str],
    term_df: Callable[[str], int],
    params: Params,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        raw = chat(build_messages(tickets), max_tokens=3000, temperature=0.1, timeout=180.0)
    except Exception as exc:  # noqa: BLE001 — one failed batch must not stop the run
        logger.warning("synonym batch LLM failure: %s", exc)
        stats = _new_stats()
        stats["llm_error"] = 1
        return [], stats
    return process_groups(parse_response(raw), tickets, term_df=term_df, params=params)


def iter_batches(tickets: list[Ticket], size: int) -> Iterator[tuple[int, list[Ticket]]]:
    for i in range(0, len(tickets), size):
        yield i // size, tickets[i : i + size]
