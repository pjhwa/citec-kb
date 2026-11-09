"""Where does the Fabrix content filter (FR-201) bite? Measures block rates; never prints ticket text.

Phases (each LLM call is a single attempt, no retries, so the rates are raw):
  prompt   the mining prompt with three harmless tickets -> is the prompt itself blocked?
  sizes    random chunks of several sizes -> block rate by chunk size
  scan     every ticket in chunks of ``--chunk`` -> blocked chunks, then each of their tickets alone
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from app.synonyms import mine

HARMLESS = [
    mine.Ticket("P-1", "디스크 사용률 경고 알람", "디스크 사용률 90% 초과", ""),
    mine.Ticket("P-2", "서버 재부팅 요청", "점검을 위한 재부팅 요청", ""),
    mine.Ticket("P-3", "백업 작업 실패", "야간 백업 작업이 실패함", ""),
]


def blocked(chat: Callable[..., str], tickets: list[mine.Ticket], counter: list[int]) -> bool:
    """One raw call. True when the answer is empty (filter or otherwise); a raised error counts as not blocked."""
    from app import llm_fabrix

    llm_fabrix.reset_empty_info()
    counter[0] += 1
    try:
        raw = chat(mine.build_messages(tickets), max_tokens=3000, temperature=0.1, timeout=180.0)
    except Exception:  # noqa: BLE001 — probing: an error is not a filter block
        counter[1] += 1
        return False
    return not (isinstance(raw, str) and raw.strip())


def run_probe(
    tickets: list[mine.Ticket], *, chat: Callable[..., str], chunk: int = 5, sizes: tuple[int, ...] = (5, 15, 30),
    samples: int = 10, prompt_trials: int = 6, max_calls: int = 400, seed: int = 7,
) -> dict[str, Any]:
    rnd = random.Random(seed)
    counter = [0, 0]  # calls, errors
    out: dict[str, Any] = {"tickets": len(tickets)}

    def room() -> bool:
        return counter[0] < max_calls

    out["prompt"] = {"trials": prompt_trials, "blocked": sum(blocked(chat, HARMLESS, counter) for _ in range(prompt_trials) if room())}

    by_size: dict[str, dict[str, int]] = {}
    for n in sizes:
        done = hit = 0
        for _ in range(samples):
            if not room() or len(tickets) < n:
                break
            done += 1
            hit += blocked(chat, rnd.sample(tickets, n), counter)
        by_size[str(n)] = {"calls": done, "blocked": hit}
    out["by_size"] = by_size

    bad_chunks: list[list[str]] = []
    bad_single: list[str] = []
    ok_single = 0
    for i in range(0, len(tickets), chunk):
        if not room():
            out["stopped_by_max_calls"] = True
            break
        part = tickets[i : i + chunk]
        if blocked(chat, part, counter):
            bad_chunks.append([t.external_id for t in part])
    for ids in bad_chunks:
        for t in (x for x in tickets if x.external_id in ids):
            if not room():
                out["stopped_by_max_calls"] = True
                break
            if blocked(chat, [t], counter):
                bad_single.append(t.external_id)
            else:
                ok_single += 1
    out["scan"] = {"chunk": chunk, "blocked_chunks": len(bad_chunks), "singles_blocked": bad_single, "singles_passed": ok_single}
    out["calls"], out["errors"] = counter
    return out
