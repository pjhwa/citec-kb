"""CLI: python -m app.evalv2.cli {prep-real|gen-synth|pool|judge|sheet|import-labels}

Read-only against production data paths: it only reads the DB / calls /v1/search and writes local files.
Exit codes: 0 ok, 2 usage/validation/refused-overwrite, 3 completed but some LLM/pool work failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from app.evalv2 import judge as judge_mod
from app.evalv2 import labels, pool, qfilter, sheet, synth

JUDGE_DONE = {"ok", "unverified", "missing", "invalid"}   # llm_error / empty are redone on --resume
STATUSES = ("ok", "unverified", "missing", "invalid", "empty", "llm_error")


def _write_json(path: str, data: Any) -> None:
    """Serialise first, then write a temp file in the same dir and os.replace (old file survives any failure)."""
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


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_id_for(queries: list[dict[str, Any]]) -> str:
    """Deterministic id of a query set: sha1 over the sorted (id, text) pairs, first 10 hex."""
    blob = "\n".join(f"{q['id']}\t{q['q']}" for q in sorted(queries, key=lambda q: q["id"]))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]


def _dupes(ids: list[str]) -> list[str]:
    c = Counter(ids)
    return sorted(i for i, n in c.items() if n > 1)


def _get_chat():
    from app.llm_chat import chat_complete
    from app.settings import get_settings

    if get_settings().llm_backend == "none":
        raise SystemExit("LLM not configured")
    return chat_complete


def _model_name() -> str:
    from app.settings import get_settings

    s = get_settings()
    return s.company_model_id if s.llm_backend == "fabrix" else s.openrouter_model_id


def _load_bodies(keys: list[Any]) -> dict[str, str]:
    """keys: external_ids or (source_type, external_id) pairs. Pairs filter on both columns (a bare external_id
    can exist in several sources). Result is keyed by external_id."""
    from sqlalchemy import select, tuple_

    from app.db.models import Document
    from app.db.session import session_scope

    pairs = sorted({(k[0], k[1]) for k in keys if isinstance(k, (tuple, list)) and k[0]})
    bare = sorted({k[1] if isinstance(k, (tuple, list)) else k for k in keys
                   if not (isinstance(k, (tuple, list)) and k[0])})
    out: dict[str, str] = {}
    with session_scope() as session:
        if pairs:
            rows = session.execute(
                select(Document.external_id, Document.body_md)
                .where(tuple_(Document.source_type, Document.external_id).in_(pairs))
            ).all()
            out.update({r.external_id: r.body_md or "" for r in rows})
        if bare:
            rows = session.execute(
                select(Document.external_id, Document.body_md).where(Document.external_id.in_(bare))
            ).all()
            out.update({r.external_id: r.body_md or "" for r in rows})
    return out


def _known(args: argparse.Namespace) -> set[str]:
    return qfilter.load_known_questions(getattr(args, "holdout", None), list(getattr(args, "evalset", None) or []))


def _existing_out(args: argparse.Namespace, run_id: str) -> tuple[dict[str, Any] | None, int | None]:
    """Decide what to do with an existing --out. Returns (existing_doc_to_resume_from, exit_code_if_refused)."""
    p = Path(args.out)
    if not p.exists() or args.force:
        return None, None
    if not args.resume:
        print(f"{args.out} already exists; use --resume to continue it or --force to overwrite", file=sys.stderr)
        return None, 2
    try:
        prev = _read_json(args.out)
    except (OSError, ValueError) as exc:
        print(f"cannot read existing {args.out}: {exc}; use --force to overwrite", file=sys.stderr)
        return None, 2
    if not isinstance(prev, dict) or prev.get("run_id") != run_id:
        print(f"existing {args.out} has run_id {prev.get('run_id') if isinstance(prev, dict) else None!r}, "
              f"expected {run_id!r} (different inputs); use --force to overwrite", file=sys.stderr)
        return None, 2
    return prev, None


def cmd_prep_real(args: argparse.Namespace) -> int:
    rows = qfilter.parse_log_tsv(Path(args.tsv).read_text(encoding="utf-8"))
    items, stats = qfilter.prepare_real(rows, _known(args))
    _write_json(args.out, {"items": items, "stats": stats})
    flagged = sum(1 for i in items if i["hints"])
    print(json.dumps({"kept": len(items), "flagged_for_review": flagged, "stats": stats}, ensure_ascii=False))
    if flagged:
        print(f"WARNING: {flagged} queries have sensitive-looking hints (email/ip/phone/url/long digits) — "
              "edit the output file to remove or neutralise them before using it.", file=sys.stderr)
    return 0


def cmd_gen_synth(args: argparse.Namespace) -> int:
    from app.db.session import session_scope

    chat = _get_chat()
    print(f"model: {_model_name()}", file=sys.stderr)
    failures = 0

    def counted_chat(*a: Any, **kw: Any) -> str:
        nonlocal failures
        try:
            return chat(*a, **kw)
        except Exception:
            failures += 1
            raise

    with session_scope() as session:
        docs = synth.sample_docs(session, args.slice, args.n, seed=args.seed)
    items = synth.generate(counted_chat, args.slice, docs, known=_known(args),
                           id_prefix=synth.id_prefix(args.slice, args.seed),
                           max_per_doc=args.per_doc, want_docs=args.n)
    _write_json(args.out, {"items": items, "model": _model_name(), "seed": args.seed})
    print(json.dumps({"docs": len(docs), "queries": len(items), "llm_failures": failures}))
    if docs and (not items or failures):
        print(f"WARNING: {len(items)} queries from {len(docs)} sampled docs, {failures} LLM call(s) failed",
              file=sys.stderr)
        return 3
    return 0


def cmd_pool(args: argparse.Namespace) -> int:
    search = pool.rest_search(args.base_url)
    queries: list[dict[str, Any]] = []
    for path in args.queries:
        queries.extend(_read_json(path)["items"])
    dups = _dupes([q["id"] for q in queries])
    if dups:
        print(f"duplicate query ids across --queries files: {', '.join(dups)}", file=sys.stderr)
        return 2
    run_id = run_id_for(queries)
    prev, refused = _existing_out(args, run_id)
    if refused:
        return refused
    reusable = {q["id"]: q for q in (prev or {}).get("queries", []) if not q.get("pool_error")}
    results: dict[str, dict[str, Any]] = {}

    def flush() -> None:
        _write_json(args.out, {"run_id": run_id, "queries": [
            results.get(q["id"]) or reusable[q["id"]] for q in queries if q["id"] in results or q["id"] in reusable]})

    failed = 0
    for q in queries:
        if q["id"] in reusable:
            print(f"{q['id']}: skipped (resume)", file=sys.stderr)
            continue
        try:
            extra = [q["seed_doc"]] if q.get("seed_doc") else None
            cands = pool.pool_candidates(search, q["q"], q.get("filters") or {}, top_n=args.top_n,
                                         max_pool=args.max_pool, extra=extra)
            results[q["id"]] = {**q, "candidates": cands}
            print(f"{q['id']}: {len(cands)} candidates", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 — one bad query must not abort the whole run
            failed += 1
            print(f"{q.get('id')}: pool failed: {exc}", file=sys.stderr)
            results[q["id"]] = {**q, "candidates": [], "pool_error": str(exc)}
        flush()
    flush()
    print(f"pooled {len(queries) - failed}/{len(queries)} queries ({failed} failed) run_id={run_id}", file=sys.stderr)
    return 3 if failed else 0


def _cand_key(c: dict[str, Any]) -> Any:
    return (c.get("source_type"), c["external_id"])


def cmd_judge(args: argparse.Namespace) -> int:
    chat = _get_chat()
    model = _model_name()
    print(f"model: {model}", file=sys.stderr)
    pooled = _read_json(args.pooled)
    queries = pooled["queries"]
    dups = _dupes([q["id"] for q in queries])
    if dups:
        print(f"duplicate query ids in {args.pooled}: {', '.join(dups)}", file=sys.stderr)
        return 2
    run_id = pooled.get("run_id") or run_id_for(queries)
    prev, refused = _existing_out(args, run_id)
    if refused:
        return refused
    reusable = {q["id"]: q for q in (prev or {}).get("queries", [])
                if all((c.get("llm") or {}).get("status") in JUDGE_DONE for c in q["candidates"])}
    todo = [q for q in queries if q["id"] not in reusable]
    bodies = _load_bodies(sorted({_cand_key(c) for q in todo for c in q["candidates"]}, key=str)) if todo else {}
    results: dict[str, dict[str, Any]] = {}

    def flush() -> None:
        _write_json(args.out, {"run_id": run_id, "model": model, "queries": [
            results.get(q["id"]) or reusable[q["id"]] for q in queries if q["id"] in results or q["id"] in reusable]})

    llm_error_queries = 0
    for q in queries:
        if q["id"] in reusable:
            print(f"{q['id']}: skipped (resume)", file=sys.stderr)
            continue
        cands = [{**c, "excerpt": bodies.get(c["external_id"], "")} for c in q["candidates"]]
        out_q = {**q, "candidates": [dict(c) for c in q["candidates"]]}
        try:
            res = judge_mod.judge_candidates(chat, q["q"], cands, bodies)
        except Exception as exc:  # noqa: BLE001 — one failing query must not abort the whole run
            llm_error_queries += 1
            print(f"{q['id']}: llm_error: {exc}", file=sys.stderr)
            for c in out_q["candidates"]:
                c["llm"] = {"grade": None, "status": "llm_error", "evidence": ""}
        else:
            for c in out_q["candidates"]:
                c["llm"] = res.get(c["external_id"], {"grade": None, "evidence": "", "status": "missing"})
            print(f"{q['id']}: judged {len(cands)}", file=sys.stderr)
        results[q["id"]] = out_q
        flush()
    flush()
    final = [results.get(q["id"]) or reusable[q["id"]] for q in queries]
    counts = Counter((c.get("llm") or {}).get("status", "missing") for q in final for c in q["candidates"])
    print("judged " + f"{len(queries) - llm_error_queries}/{len(queries)} queries, llm_error queries: {llm_error_queries}; "
          "candidates: " + ", ".join(f"{s}={counts.get(s, 0)}" for s in STATUSES), file=sys.stderr)
    return 3 if (llm_error_queries or counts.get("empty") or counts.get("llm_error")) else 0


def cmd_sheet(args: argparse.Namespace) -> int:
    html = sheet.render(_read_json(args.judged), doc_base=args.doc_base or "")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"saved {args.out}")
    return 0


def cmd_import_labels(args: argparse.Namespace) -> int:
    judged, review = _read_json(args.judged), _read_json(args.review)
    out = Path(args.out_dir)
    frozen_at = None
    old = out / "evalset-v2-frozen.json"
    if old.exists():
        try:
            frozen_at = _read_json(str(old)).get("frozen_at_utc")
        except (OSError, ValueError, AttributeError):
            frozen_at = None
    try:
        built = labels.build_evalsets(judged, review, evalset_id=args.evalset_id, frozen_at=frozen_at)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _write_json(str(out / "evalset-v2-dev.json"), built["dev"])
    _write_json(str(out / "evalset-v2-frozen.json"), built["frozen"])
    _write_json(str(out / "report.json"), built["report"])
    print(json.dumps({**built["report"], "dev": len(built["dev"]["cases"]), "frozen": len(built["frozen"]["cases"])},
                     ensure_ascii=False))
    return 0


def _add_resume(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--resume", action="store_true", help="continue an existing --out with the same run_id")
    sp.add_argument("--force", action="store_true", help="overwrite an existing --out")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Evalset v2 tooling")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("prep-real")
    a.add_argument("--tsv", required=True)
    a.add_argument("--holdout")
    a.add_argument("--evalset", action="append")
    a.add_argument("--out", required=True)
    a.set_defaults(fn=cmd_prep_real)

    g = sub.add_parser("gen-synth")
    g.add_argument("--slice", required=True, choices=sorted(synth.SLICE_SOURCES))
    g.add_argument("--n", type=int, default=15)
    g.add_argument("--per-doc", type=int, default=2)
    g.add_argument("--seed", default="evalv2")
    g.add_argument("--holdout")
    g.add_argument("--evalset", action="append")
    g.add_argument("--out", required=True)
    g.set_defaults(fn=cmd_gen_synth)

    po = sub.add_parser("pool")
    po.add_argument("--queries", nargs="+", required=True)
    po.add_argument("--base-url", default="http://localhost:8000")
    po.add_argument("--top-n", type=int, default=10)
    po.add_argument("--max-pool", type=int, default=15)
    po.add_argument("--out", required=True)
    _add_resume(po)
    po.set_defaults(fn=cmd_pool)

    j = sub.add_parser("judge")
    j.add_argument("--pooled", required=True)
    j.add_argument("--out", required=True)
    _add_resume(j)
    j.set_defaults(fn=cmd_judge)

    s = sub.add_parser("sheet")
    s.add_argument("--judged", required=True)
    s.add_argument("--doc-base", default="", help="web UI base URL incl. port, e.g. http://localhost:8573 (http/https only)")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_sheet)

    i = sub.add_parser("import-labels")
    i.add_argument("--judged", required=True)
    i.add_argument("--review", required=True)
    i.add_argument("--evalset-id", required=True)
    i.add_argument("--out-dir", required=True)
    i.set_defaults(fn=cmd_import_labels)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
