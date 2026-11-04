"""CLI: python -m app.docexp.cli {count|sample|run|stats|export|import|embed}

stdout is exactly one JSON object; logs go to stderr.
Exit codes: 0 ok, 2 usage / LLM backend 'none', 3 completed but some documents hit
llm_error / fatal / parse_fail / empty_response / error, 4 aborted after 3 consecutive fatal LLM
results (HTTP 401/402/403; JSON gets "aborted")
(error = unexpected exception while generating or storing one document).
--out is written even when the run is interrupted. The per-document progress file
(<out>.progress.jsonl, `run` only) is append-only and is not consulted on rerun: a dry-run
rerun regenerates everything, and only committed rows are skipped (resume works via the DB).
LLM calls run in a thread pool; every DB read/write happens in the main thread and no DB
session is held while a call is in flight. Without --commit nothing is written to the DB.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import sys
import tempfile
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from app.docexp import gen, store
from app.docexp.selection import select_documents

logger = logging.getLogger("citec.docexp")

OUTCOMES = ("ok", "discarded", "parse_fail", "llm_error", "fatal", "empty_response", "error")
FAILURES = ("llm_error", "fatal", "parse_fail", "empty_response", "error")
FATAL_ABORT_AFTER = 3
EXIT_ABORTED = 4


def _write_json(path: str, data: Any) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _get_chat() -> tuple[Any, str] | None:
    """(chat callable, model name) or None when no LLM backend is configured."""
    from app.llm_chat import chat_complete
    from app.settings import get_settings

    s = get_settings()
    if s.llm_backend == "none":
        return None
    if s.llm_backend == "fabrix":
        model = getattr(s, "company_model_id", None) or "fabrix"
    else:
        model = s.openrouter_model_id
    return chat_complete, model


def _emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()


def cmd_count(args: argparse.Namespace) -> int:
    by_rule: Counter[str] = Counter()
    chars: Counter[str] = Counter()
    for d in select_documents():
        by_rule[d["rule"]] += 1
        chars[d["rule"]] += len(d.get("body_md") or "")
    _emit(
        {
            "total": sum(by_rule.values()),
            "body_chars": sum(chars.values()),
            "by_rule": {r: {"docs": by_rule[r], "body_chars": chars[r]} for r in sorted(by_rule)},
        }
    )
    return 0


def _public(doc: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "external_id": doc["external_id"],
        "title": doc.get("title"),
        "rule": doc["rule"],
        "summary": rec["summary"],
        "keywords": rec["keywords"],
        "grounded": rec["grounded"],
    }


def _generate(
    docs_iter: Any,
    *,
    chat: Any,
    model: str,
    run_id: str,
    commit: bool,
    workers: int,
    skip_existing: bool,
    progress_path: str | None,
    out_path: str | None = None,
) -> dict[str, Any]:
    stats = gen.new_stats()
    results: list[dict[str, Any]] = []
    total = skipped = committed = consecutive_fatal = 0
    aborted: str | None = None
    chunk_size = max(workers * 4, 50)
    progress = open(progress_path, "a", encoding="utf-8") if progress_path else None
    pool = ThreadPoolExecutor(max_workers=max(workers, 1))
    try:
        while aborted is None:
            chunk = list(itertools.islice(docs_iter, chunk_size))
            if not chunk:
                break
            total += len(chunk)
            if skip_existing:
                have = store.existing_ids(
                    [d["id"] for d in chunk], hashes={d["id"]: d.get("content_hash") for d in chunk}
                )
                todo = [d for d in chunk if d["id"] not in have]
                skipped += len(chunk) - len(todo)
            else:
                todo = chunk
            futs = {pool.submit(gen.expand_one, d, chat=chat): d for d in todo}
            for fut in as_completed(futs):
                doc = futs[fut]
                try:
                    rec, st = fut.result()
                except Exception:  # noqa: BLE001 — one bad document must not stop the run
                    logger.exception("docexp: generation failed for %s", doc.get("external_id"))
                    stats["error"] = stats.get("error", 0) + 1
                    continue
                for k, v in st.items():
                    stats[k] = stats.get(k, 0) + v
                consecutive_fatal = consecutive_fatal + 1 if st.get("fatal") else 0
                if consecutive_fatal >= FATAL_ABORT_AFTER:
                    aborted = f"{consecutive_fatal} consecutive fatal LLM errors (HTTP 401/402/403); stopped submitting"
                    logger.error("docexp: %s", aborted)
                    for f in futs:
                        f.cancel()
                    break
                if rec is None:
                    continue
                if commit:
                    try:
                        store.upsert(
                            doc["id"], rec, rule=doc["rule"], model=model, run_id=run_id,
                            content_hash=doc.get("content_hash"),
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("docexp: store failed for %s", doc.get("external_id"))
                        stats["ok"] -= 1
                        stats["error"] = stats.get("error", 0) + 1
                        continue
                    committed += 1
                item = _public(doc, rec)
                results.append(item)
                if progress:
                    progress.write(json.dumps(item, ensure_ascii=False) + "\n")
                    progress.flush()
    finally:
        # Normal path: every future is done. Ctrl-C: drop queued work, don't wait out running LLM calls.
        pool.shutdown(wait=False, cancel_futures=True)
        if progress:
            progress.close()
        if out_path:
            _write_json(out_path, sorted(results, key=lambda r: r["external_id"]))
    results.sort(key=lambda r: r["external_id"])
    return {"stats": stats, "results": results, "total": total, "skipped": skipped, "committed": committed, "aborted": aborted}


def _run_common(args: argparse.Namespace, *, limit: int | None, skip_existing: bool, name: str) -> int:
    got = _get_chat()
    if got is None:
        print("LLM not configured", file=sys.stderr)
        return 2
    chat, model = got
    run_id = uuid.uuid4().hex[:12]
    out = args.out
    out_data = _generate(
        select_documents(limit=limit, seed=args.seed),
        chat=chat,
        model=model,
        run_id=run_id,
        commit=args.commit,
        workers=args.workers,
        skip_existing=skip_existing,
        progress_path=f"{out}.progress.jsonl" if (out and name == "run") else None,
        out_path=out,
    )
    stats = out_data["stats"]
    payload = {
            "command": name,
            "run_id": run_id,
            "model": model,
            "dry_run": not args.commit,
            "total": out_data["total"],
            "skipped_existing": out_data["skipped"],
            "committed": out_data["committed"],
            "stats": stats,
            "out": out,
    }
    if out_data["aborted"]:
        payload["aborted"] = out_data["aborted"]
    _emit(payload)
    if out_data["aborted"]:
        return EXIT_ABORTED
    return 3 if any(stats.get(k, 0) for k in FAILURES) else 0


def cmd_sample(args: argparse.Namespace) -> int:
    # Sample is for human review: existing rows are regenerated, not skipped.
    return _run_common(args, limit=args.n, skip_existing=False, name="sample")


def cmd_run(args: argparse.Namespace) -> int:
    return _run_common(args, limit=args.limit, skip_existing=not args.force, name="run")


def cmd_stats(args: argparse.Namespace) -> int:
    _emit(store.stats())
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Stream doc_expansion (+ document identity) to JSONL; no LLM, constant memory."""
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for row in store.iter_export():
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _emit({"command": "export", "exported": n, "out": args.out})
    return 0


def _check_import_line(obj: Any) -> dict[str, Any] | None:
    """Shape + gen.validate checks for one export line; None = invalid."""
    if not isinstance(obj, dict):
        return None
    st, ext, summ, kws, gr = (obj.get(k) for k in ("source_type", "external_id", "summary", "keywords", "grounded"))
    if not (isinstance(st, str) and st and isinstance(ext, str) and ext and isinstance(summ, str)):
        return None
    if not (isinstance(kws, list) and isinstance(gr, list)):
        return None
    if not all(isinstance(k, str) for k in kws) or not all(isinstance(k, str) for k in gr):
        return None
    if len(summ) > gen.SUMMARY_MAX or len(kws) > gen.KEYWORDS_MAX or not set(gr) <= set(kws):
        return None
    clean = gen.validate({"summary": summ, "keywords": kws}, None)
    # Exported rows are already canonical: validation must neither drop nor split a keyword.
    if clean is None or clean["keywords"] != kws or clean["summary"] != " ".join(summ.split()):
        return None
    return {"summary": clean["summary"], "keywords": kws, "grounded": list(gr)}


def _iter_jsonl(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield line


def cmd_import(args: argparse.Namespace) -> int:
    counts = dict.fromkeys(
        ("read", "invalid", "matched", "missing_doc", "hash_mismatch", "skipped_existing", "written", "would_write"), 0
    )
    batch: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def flush() -> None:
        if not batch:
            return
        found = store.find_active_documents({(o["source_type"], o["external_id"]) for o, _ in batch})
        cand: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for obj, rec in batch:
            doc = found.get((obj["source_type"], obj["external_id"]))
            if doc is None:
                counts["missing_doc"] += 1
                continue
            counts["matched"] += 1
            if not args.ignore_hash and obj.get("content_hash") != doc["content_hash"]:
                counts["hash_mismatch"] += 1
                continue
            cand.append((obj, rec, doc))
        have = store.existing_ids([d["id"] for _, _, d in cand]) if args.skip_existing and cand else set()
        for obj, rec, doc in cand:
            if doc["id"] in have:
                counts["skipped_existing"] += 1
                continue
            if not args.commit:
                counts["would_write"] += 1
                continue
            try:
                # --ignore-hash: the TARGET hash is stored, i.e. the old text is assumed equivalent
                # (evaluation approximation only).
                store.upsert(
                    doc["id"], rec, rule=obj.get("rule"), model=obj.get("model"),
                    run_id=f"import:{obj.get('run_id') or ''}", content_hash=doc["content_hash"],
                )
                counts["written"] += 1
            except Exception:  # noqa: BLE001
                logger.exception("docexp import: store failed for %s", obj.get("external_id"))
                counts["invalid"] += 1
        batch.clear()

    for line in _iter_jsonl(args.in_path):
        counts["read"] += 1
        try:
            obj = json.loads(line)
        except ValueError:
            counts["invalid"] += 1
            continue
        rec = _check_import_line(obj)
        if rec is None:
            counts["invalid"] += 1
            continue
        batch.append((obj, rec))
        if len(batch) >= 500:
            flush()
    flush()
    _emit({"command": "import", "dry_run": not args.commit, "ignore_hash": args.ignore_hash, **counts})
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    from app.docexp import embed

    res = embed.embed_all(commit=args.commit, force=args.force, limit=args.limit)
    _emit({"command": "embed", "dry_run": not args.commit, **res})
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m app.docexp.cli", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("count").set_defaults(fn=cmd_count)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--seed", type=int, default=None)
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--commit", action="store_true", help="write to doc_expansion (default: dry run)")
        p.add_argument("--out", default=None, help="JSON output file")

    p = sub.add_parser("sample")
    p.add_argument("--n", type=int, default=200)
    common(p)
    p.set_defaults(fn=cmd_sample, seed=1)
    p = sub.add_parser("run")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--force", action="store_true", help="regenerate documents that already have an expansion")
    common(p)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("export")
    p.add_argument("--out", required=True, help="JSONL output path")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("import")
    p.add_argument("--in", dest="in_path", required=True, help="JSONL produced by export")
    p.add_argument("--ignore-hash", action="store_true", help="store the target document's hash even if the file's differs (evaluation approximation)")
    p.add_argument("--skip-existing", action="store_true")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="default")
    g.add_argument("--commit", action="store_true")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("embed")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--force", action="store_true", help="re-embed rows that already have a fresh vector")
    p.add_argument("--commit", action="store_true", help="write to doc_expansion_embedding (default: dry run)")
    p.set_defaults(fn=cmd_embed)
    return ap


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = build_parser().parse_args(argv)
    if getattr(args, "workers", 1) < 1:
        print("--workers must be >= 1", file=sys.stderr)
        return 2
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
