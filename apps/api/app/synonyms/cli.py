"""CLI: python -m app.synonyms.cli {mine|import} …

mine   -- ask the LLM for synonym candidates, verify in code, write a JSON file
          (dry-run unless --commit). Resumable via <out>.progress.jsonl.
import -- load a mined JSON file into search_synonym_candidate as 'proposed'.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from typing import Any, Callable

from app.synonyms import mine


def _merge_stats(total: dict[str, int], part: dict[str, int]) -> None:
    for k, v in part.items():
        total[k] = total.get(k, 0) + int(v)


def run_mine(
    *,
    tickets: list[mine.Ticket],
    batch_size: int,
    chat: Callable[..., str],
    term_df: Callable[[str], int],
    params: mine.Params,
    run_id: str,
    model: str,
    out_path: str,
    max_batches: int | None,
) -> dict[str, Any]:
    progress_path = out_path + ".progress.jsonl"
    done: dict[int, dict[str, Any]] = {}
    if os.path.exists(progress_path):
        with open(progress_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    if rec.get("run_id") == run_id:
                        done[int(rec["batch"])] = rec
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue  # truncated/corrupt line (e.g. killed mid-write): redo that batch

    failed: dict[str, int] = {}  # this run only; failed batches are not checkpointed -> retried on resume
    for idx, batch in mine.iter_batches(tickets, batch_size):
        if max_batches is not None and idx >= max_batches:
            break
        if idx in done:
            continue
        items, stats = mine.mine_batch(batch, chat=chat, term_df=term_df, params=params)
        if stats.get("llm_error") or stats.get("proc_error"):
            _merge_stats(failed, stats)
            print(f"batch {idx}: FAILED {stats}", file=sys.stderr)
            continue
        rec = {"run_id": run_id, "batch": idx, "items": items, "stats": stats}
        done[idx] = rec
        with open(progress_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"batch {idx}: {stats}", file=sys.stderr)

    items_all: list[dict[str, Any]] = []
    keys: dict[str, dict[str, Any]] = {}
    stats_total: dict[str, int] = {}
    for idx in sorted(done):
        _merge_stats(stats_total, done[idx]["stats"])
        for it in done[idx]["items"]:
            k = "|".join(sorted(t.lower() for t in it["terms"]))
            if k in keys:  # same group from another batch: union the evidence tickets
                have = keys[k]["evidence"]["tickets"]
                have += [t for t in it["evidence"]["tickets"] if t not in have]
                continue
            keys[k] = it
            items_all.append(it)
    _merge_stats(stats_total, failed)
    stats_total["batches_done"] = len(done)
    result = {"run_id": run_id, "model": model, "items": items_all, "stats": stats_total}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Synonym candidate mining")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mine")
    m.add_argument("--since-years", type=float, default=2.0)
    m.add_argument("--limit-tickets", type=int, default=None)
    m.add_argument("--batch-size", type=int, default=100)
    m.add_argument("--max-batches", type=int, default=None)
    m.add_argument("--run-id", default=None)
    m.add_argument("--min-terms-per-ticket", type=int, default=2, choices=[1, 2])
    m.add_argument("--df-min", type=int, default=3)
    m.add_argument("--df-max", type=int, default=1500)
    m.add_argument("--out", required=True)
    m.add_argument("--commit", action="store_true", help="also insert accepted candidates as 'proposed'")

    i = sub.add_parser("import")
    i.add_argument("--file", required=True)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    if args.cmd == "import":
        from app.synonyms import store

        try:
            with open(args.file, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"cannot read {args.file}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            print(f"{args.file}: missing 'items' list (not a mine output file?)", file=sys.stderr)
            return 2
        res = store.upsert_candidates(data["items"], run_id=data.get("run_id"), model=data.get("model"))
        print(json.dumps(res))
        return 0

    from app.llm_chat import chat_complete
    from app.settings import get_settings
    from app.synonyms import store

    settings = get_settings()
    if settings.llm_backend == "none":
        print("LLM not configured", file=sys.stderr)
        return 2
    model = settings.company_model_id if settings.llm_backend == "fabrix" else settings.openrouter_model_id
    tickets = mine.load_tickets(since_years=args.since_years, limit=args.limit_tickets)
    print(f"tickets: {len(tickets)} backend={settings.llm_backend} model={model}", file=sys.stderr)
    params = mine.Params(
        df_min=args.df_min, df_max=args.df_max,
        min_terms_per_ticket=args.min_terms_per_ticket,
        existing_keys=store.existing_keys(),
    )
    result = run_mine(
        tickets=tickets, batch_size=args.batch_size, chat=chat_complete,
        term_df=mine.make_term_df(), params=params,
        run_id=args.run_id or uuid.uuid4().hex[:12], model=model,
        out_path=args.out, max_batches=args.max_batches,
    )
    print(json.dumps(result["stats"], ensure_ascii=False))
    if args.commit:
        print(json.dumps(store.upsert_candidates(result["items"], run_id=result["run_id"], model=model)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
