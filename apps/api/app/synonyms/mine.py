"""Synonym candidate mining: batch -> LLM proposal -> code verification."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.synonyms.rules import (
    MAX_GROUP,
    clean_terms,
    term_present,
    static_covers,
    terms_key,
    verify_group,
)

logger = logging.getLogger("citec.synonyms.mine")

# make_term_df counts matching chunks only up to this cap (same as retrieval.search._RARE_DF_CAP);
# a count equal to the cap means "at least this many", so Params.df_max must stay below it.
DF_COUNT_CAP = 2000

SYSTEM_RULES = (
    "당신은 IT 인프라 기술지원 티켓 검색용 동의어 사전 편집자입니다.\n"
    "아래 티켓 목록(제목/증상)에서, 같은 현상·개념을 가리키지만 표기가 다른 기술 용어 묶음"
    "(한글↔영문, 약어↔풀네임, 표기 변형)을 찾아 JSON으로만 답하세요.\n"
    "규칙:\n"
    "- 각 용어는 인용한 티켓의 텍스트에 글자 그대로 등장해야 합니다 (추측·번역 금지).\n"
    "- 한 묶음은 2~6개 용어, 한글 용어는 3자 이상, 영문 용어는 3자 이상.\n"
    "- 용어에 조사/어미를 붙이지 말 것 (예: '메모리가' 대신 '메모리').\n"
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


_MAX_CITED = 20
_MAX_TERMS_IN = 12


def _clean_id(x: Any) -> str:
    if isinstance(x, bool) or not isinstance(x, (str, int)):
        return ""
    return str(x).strip().strip("[]").strip()


def _parse(raw: str) -> list[dict[str, Any]] | None:
    """Parsed groups, or None when the text is unparseable / has the wrong shape."""
    text = (raw or "").strip() if isinstance(raw, str) else ""
    if not text:
        return None
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    if not text.startswith("{"):
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            return None
        text = text[i : j + 1]
    try:
        data = json.loads(text)
    except ValueError:
        return None
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        return None
    out = []
    for g in groups:
        if isinstance(g, dict) and isinstance(g.get("terms"), list):
            cited = g.get("tickets")
            ids = [i for i in (_clean_id(x) for x in (cited if isinstance(cited, list) else [])) if i]
            out.append(
                {
                    "terms": [x for x in g["terms"] if isinstance(x, str)][:_MAX_TERMS_IN],
                    "tickets": ids[:_MAX_CITED],
                    "reason": str(g.get("reason") or "")[:300],
                }
            )
    return out


def parse_response(raw: str) -> list[dict[str, Any]]:
    return _parse(raw) or []


def _new_stats() -> dict[str, int]:
    return {
        "groups_in": 0,
        "accepted": 0,
        "llm_error": 0,
        "proc_error": 0,
        "parse_fail": 0,
        "empty_response": 0,
        "empty_retried": 0,
        "empty_recovered": 0,
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
    df_cache: dict[str, int] = {}

    def df_of(term: str) -> int:
        if term not in df_cache:
            df_cache[term] = term_df(term)
        return df_cache[term]

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
        if terms_key(kept) in seen:  # cheap early reject, before any DF query
            stats["rej_existing"] += 1
            continue
        dfs = {t: df_of(t) for t in kept}
        trimmed = [t for t in kept if params.df_min <= dfs[t] <= params.df_max]
        if len(trimmed) < 2:
            stats["rej_df"] += 1
            continue
        if len(trimmed) != len(kept):
            # trimming may have removed the terms that made each ticket count as evidence
            again = verify_group(
                trimmed, ticket_ids, texts, min_terms_per_ticket=params.min_terms_per_ticket
            )
            if again is None:
                stats["rej_df"] += 1
                continue
            kept, ticket_ids = again
            if static_covers(kept):
                stats["rej_static"] += 1
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
                    # tickets where >=2 of the terms appear together; 0 = each term only seen in separate tickets
                    "cooccur_tickets": sum(
                        1 for tid in ticket_ids
                        if sum(1 for t in kept if term_present(texts.get(tid, ""), t)) >= 2
                    ),
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
    notes: list[dict[str, Any]] | None = None,
    empty_retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = lambda: random.uniform(0.0, 1.0),
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """notes: optional side channel; receives one info dict per empty LLM response."""
    messages = build_messages(tickets)  # programming errors here must surface
    from app import llm_fabrix

    stats0 = _new_stats()
    raw: Any = ""
    for attempt in range(max(0, int(empty_retries)) + 1):
        if attempt:
            sleep(2.0 * (2 ** (attempt - 1)) + jitter())  # 2s, 4s, 8s (+ jitter)
            stats0["empty_retried"] += 1
        llm_fabrix.reset_empty_info()
        try:
            raw = chat(messages, max_tokens=3000, temperature=0.1, timeout=180.0)
        except Exception as exc:  # noqa: BLE001 — one failed batch must not stop the run
            logger.warning("synonym batch LLM failure: %s", exc)
            stats0["llm_error"] = 1
            return [], stats0
        if isinstance(raw, str) and raw.strip():
            if attempt:
                stats0["empty_recovered"] = 1
            break
    else:
        info = llm_fabrix.last_empty_info() or {}
        logger.warning(
            "synonym batch: LLM returned empty content after %d retries (filter=%s)",
            stats0["empty_retried"], info.get("filter_code"),
        )
        stats0["empty_response"] = 1
        if notes is not None:
            notes.append(info)
        return [], stats0
    groups = _parse(raw)
    if groups is None:
        if isinstance(raw, str) and raw.strip():
            logger.warning("synonym batch unparseable LLM output: %r", raw.strip()[:200])
            stats0["parse_fail"] = 1
        return [], stats0
    try:
        items, st = process_groups(groups, tickets, term_df=term_df, params=params)
        st["empty_retried"] = stats0["empty_retried"]
        st["empty_recovered"] = stats0["empty_recovered"]
        return items, st
    except Exception as exc:  # noqa: BLE001 — e.g. DB error from term_df; keep the run alive
        logger.warning("synonym batch processing failure: %s", exc)
        stats0["proc_error"] = 1
        return [], stats0


def iter_batches(tickets: list[Ticket], size: int) -> Iterator[tuple[int, list[Ticket]]]:
    for i in range(0, len(tickets), size):
        yield i // size, tickets[i : i + size]


_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def created_on_or_after(meta: dict[str, Any], cutoff: date) -> bool:
    """Created date >= cutoff; tickets with an unparseable date are included."""
    m = _DATE.search(str((meta or {}).get("Created") or ""))
    if not m:
        return True
    try:
        return date(int(m[1]), int(m[2]), int(m[3])) >= cutoff
    except ValueError:
        return True


def load_tickets(*, since_years: float, limit: int | None = None) -> list[Ticket]:
    """Active support_history tickets, ordered by external_id (stable batches).

    The Created window is applied in Python: tickets with an unparseable/missing
    Created date are included, and memory is O(all active tickets), not O(window).
    """
    from sqlalchemy import select

    from app.db.models import Document, IssueFrame
    from app.db.session import session_scope

    cutoff = date.today() - timedelta(days=int(365 * since_years))
    out: list[Ticket] = []
    with session_scope() as session:
        # issue_frames.document_id is UNIQUE, so the outer join cannot duplicate tickets.
        rows = session.execute(
            select(
                Document.external_id, Document.title, Document.body_md,
                Document.metadata_, IssueFrame.symptom,
            )
            .select_from(Document)
            .outerjoin(IssueFrame, IssueFrame.document_id == Document.id)
            .where(Document.source_type == "support_history", Document.status == "active")
            .order_by(Document.external_id)
        ).all()
    for ext, title, body, meta, symptom in rows:
        if not (title or "").strip() or not created_on_or_after(meta or {}, cutoff):
            continue
        sym = (symptom or "").strip() or (body or "").strip()[:200]
        out.append(Ticket(ext, title.strip(), sym, f"{title}\n{body or ''}"))
        if limit and len(out) >= limit:
            break
    return out


def make_term_df() -> Callable[[str], int]:
    """Active-chunk FTS document frequency (capped), same query as search._token_df."""
    from sqlalchemy import func, select

    from app.db.models import Chunk
    from app.db.session import session_scope

    cache: dict[str, int] = {}

    def df(term: str) -> int:
        key = term.lower()
        if key in cache:
            return cache[key]
        with session_scope() as session:
            # Capped at DF_COUNT_CAP: a count equal to the cap means "at least that many",
            # which is always > Params.df_max (enforced < cap by the CLI) -> rejected as too common.
            capped = (
                select(Chunk.id)
                .where(Chunk.is_active.is_(True))
                .where(Chunk.tsv.op("@@")(func.plainto_tsquery("simple", term)))
                .limit(DF_COUNT_CAP)
                .subquery()
            )
            n = int(session.scalar(select(func.count()).select_from(capped)) or 0)
        cache[key] = n
        return n

    return df
