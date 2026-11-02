"""CLI: python -m app.synonyms.cli {mine|import} …

mine   -- ask the LLM for synonym candidates, verify in code, write a JSON file
          (dry-run unless --commit). Resumable via <out>.progress.jsonl.
import -- load a mined JSON file into search_synonym_candidate as 'proposed'.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import uuid
from typing import Any, Callable

from app.synonyms import mine
from app.synonyms.rules import terms_key


def _merge_stats(total: dict[str, int], part: dict[str, int]) -> None:
    for k, v in part.items():
        total[k] = total.get(k, 0) + int(v)


def _ids_hash(batch: list[mine.Ticket]) -> str:
    return hashlib.sha1("\n".join(t.external_id for t in batch).encode("utf-8")).hexdigest()


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
    saved: dict[int, dict[str, Any]] = {}  # later line wins
    if os.path.exists(progress_path):
        with open(progress_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                    if rec.get("run_id") == run_id:
                        saved[int(rec["batch"])] = rec
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue  # truncated/corrupt line (e.g. killed mid-write): redo that batch

    used: dict[int, dict[str, Any]] = {}  # batches that make up this result
    failed: dict[str, int] = {}  # this run only; failed batches are not checkpointed -> retried on resume
    for idx, batch in mine.iter_batches(tickets, batch_size):
        if max_batches is not None and idx >= max_batches:
            break
        h = _ids_hash(batch)
        rec = saved.get(idx)
        if rec is not None:
            if rec.get("ids_hash") == h:
                used[idx] = rec
                continue
            print(f"batch {idx}: ticket set changed since checkpoint; redoing", file=sys.stderr)
        items, stats = mine.mine_batch(batch, chat=chat, term_df=term_df, params=params)
        if stats.get("llm_error") or stats.get("proc_error"):
            _merge_stats(failed, stats)
            print(f"batch {idx}: FAILED {stats}", file=sys.stderr)
            continue
        rec = {"run_id": run_id, "batch": idx, "ids_hash": h, "items": items, "stats": stats}
        used[idx] = rec
        with open(progress_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"batch {idx}: {stats}", file=sys.stderr)

    items_all: list[dict[str, Any]] = []
    keys: dict[str, dict[str, Any]] = {}
    stats_total: dict[str, int] = {}
    for idx in sorted(used):
        _merge_stats(stats_total, used[idx]["stats"])
        for it in used[idx]["items"]:
            k = terms_key(it["terms"])  # same key as store.upsert_candidates
            if k in keys:  # same group from another batch: union the evidence tickets
                have = keys[k]["evidence"]["tickets"]
                have += [t for t in it["evidence"]["tickets"] if t not in have]
                continue
            keys[k] = it
            items_all.append(it)
    _merge_stats(stats_total, failed)
    stats_total["batches_done"] = len(used)
    result = {"run_id": run_id, "model": model, "items": items_all, "stats": stats_total}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return result


def _preflight(out_path: str) -> str | None:
    """Make sure the output location is writable before spending LLM calls."""
    try:
        d = os.path.dirname(os.path.abspath(out_path))
        os.makedirs(d, exist_ok=True)
        with open(out_path + ".progress.jsonl", "a", encoding="utf-8"):
            pass
    except OSError as exc:
        return f"cannot write output/progress files for {out_path}: {exc}"
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Synonym candidate mining")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("mine")
    m.add_argument("--since-years", type=float, default=2.0)
    m.add_argument("--limit-tickets", type=int, default=None)
    m.add_argument("--batch-size", type=int, default=100)
    m.add_argument("--max-batches", type=int, default=None)
    m.add_argument("--run-id", default=None, help="default: derived from --out, so re-running resumes")
    m.add_argument("--min-terms-per-ticket", type=int, default=2, choices=[1, 2])
    m.add_argument("--df-min", type=int, default=3)
    m.add_argument("--df-max", type=int, default=1500)
    m.add_argument("--out", required=True)
    m.add_argument("--commit", action="store_true", help="also insert accepted candidates as 'proposed'")
    m.add_argument("--allow-partial", action="store_true", help="with --commit, commit even if some batches failed")

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

    if args.df_max >= mine.DF_COUNT_CAP:
        p.error(f"--df-max must be < {mine.DF_COUNT_CAP} (document frequency is counted only up to that cap)")

    from app.llm_chat import chat_complete
    from app.settings import get_settings
    from app.synonyms import store

    settings = get_settings()
    if settings.llm_backend == "none":
        print("LLM not configured", file=sys.stderr)
        return 2
    err = _preflight(args.out)
    if err:
        print(err, file=sys.stderr)
        return 2
    model = settings.company_model_id if settings.llm_backend == "fabrix" else settings.openrouter_model_id
    run_id = args.run_id or hashlib.sha1(os.path.abspath(args.out).encode("utf-8")).hexdigest()[:12]
    tickets = mine.load_tickets(since_years=args.since_years, limit=args.limit_tickets)
    print(
        f"run_id={run_id} tickets: {len(tickets)} backend={settings.llm_backend} model={model}",
        file=sys.stderr,
    )
    params = mine.Params(
        df_min=args.df_min, df_max=args.df_max,
        min_terms_per_ticket=args.min_terms_per_ticket,
        existing_keys=store.existing_keys(),
    )
    result = run_mine(
        tickets=tickets, batch_size=args.batch_size, chat=chat_complete,
        term_df=mine.make_term_df(), params=params,
        run_id=run_id, model=model,
        out_path=args.out, max_batches=args.max_batches,
    )
    stats = result["stats"]
    n_batches = -(-len(tickets) // args.batch_size)
    if args.max_batches is not None and args.max_batches < n_batches:
        print(f"WARNING: stopped early by --max-batches ({args.max_batches} of {n_batches} batches)", file=sys.stderr)
    partial = bool(stats.get("llm_error") or stats.get("proc_error") or stats.get("parse_fail"))
    if partial:
        print(
            f"WARNING: partial run (llm_error={stats.get('llm_error', 0)} proc_error={stats.get('proc_error', 0)} "
            f"parse_fail={stats.get('parse_fail', 0)}); re-run with the same --out to retry failed batches",
            file=sys.stderr,
        )
    commit: dict[str, Any] | None = None
    if args.commit:
        if partial and not args.allow_partial:
            print("WARNING: --commit skipped because the run was partial (use --allow-partial)", file=sys.stderr)
        else:
            commit = store.upsert_candidates(result["items"], run_id=result["run_id"], model=model)
    print(json.dumps({"run_id": run_id, "stats": stats, "commit": commit}, ensure_ascii=False))
    return 3 if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
