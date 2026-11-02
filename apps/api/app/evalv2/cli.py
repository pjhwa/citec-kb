"""CLI: python -m app.evalv2.cli {prep-real|gen-synth|pool|judge|sheet|import-labels}

Read-only against production data paths: it only reads the DB / calls /v1/search and writes local files.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from app.evalv2 import judge as judge_mod
from app.evalv2 import labels, pool, qfilter, sheet, synth


def _write_json(path: str, data: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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


def _load_bodies(eids: list[str]) -> dict[str, str]:
    from sqlalchemy import select

    from app.db.models import Document
    from app.db.session import session_scope

    with session_scope() as session:
        rows = session.execute(
            select(Document.external_id, Document.body_md).where(Document.external_id.in_(eids))
        ).all()
    return {r.external_id: r.body_md or "" for r in rows}


def _known(args: argparse.Namespace) -> set[str]:
    return qfilter.load_known_questions(getattr(args, "holdout", None), list(getattr(args, "evalset", None) or []))


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
    with session_scope() as session:
        docs = synth.sample_docs(session, args.slice, args.n, seed=args.seed)
    items = synth.generate(chat, args.slice, docs, known=_known(args), id_prefix=f"s-{args.slice}",
                           max_per_doc=args.per_doc)
    _write_json(args.out, {"items": items, "model": _model_name(), "seed": args.seed})
    print(json.dumps({"docs": len(docs), "queries": len(items)}))
    return 0


def cmd_pool(args: argparse.Namespace) -> int:
    search = pool.rest_search(args.base_url)
    queries: list[dict[str, Any]] = []
    for path in args.queries:
        queries.extend(_read_json(path)["items"])
    out = []
    failed = 0
    for q in queries:
        try:
            extra = [q["seed_doc"]] if q.get("seed_doc") else None
            cands = pool.pool_candidates(search, q["q"], q.get("filters") or {}, top_n=args.top_n,
                                         max_pool=args.max_pool, extra=extra)
        except Exception as exc:  # noqa: BLE001 — one bad query must not abort the whole run
            failed += 1
            print(f"{q.get('id')}: pool failed: {exc}", file=sys.stderr)
            out.append({**q, "candidates": [], "pool_error": str(exc)})
            continue
        out.append({**q, "candidates": cands})
        print(f"{q['id']}: {len(cands)} candidates", file=sys.stderr)
    _write_json(args.out, {"queries": out})
    print(f"pooled {len(out) - failed}/{len(out)} queries ({failed} failed)", file=sys.stderr)
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    chat = _get_chat()
    model = _model_name()
    print(f"model: {model}", file=sys.stderr)
    pooled = _read_json(args.pooled)
    all_eids = sorted({c["external_id"] for q in pooled["queries"] for c in q["candidates"]})
    bodies = _load_bodies(all_eids)
    llm_errors = 0
    for q in pooled["queries"]:
        cands = [{**c, "excerpt": bodies.get(c["external_id"], "")} for c in q["candidates"]]
        try:
            res = judge_mod.judge_candidates(chat, q["q"], cands, bodies)
        except Exception as exc:  # noqa: BLE001 — one failing query must not abort the whole run
            llm_errors += 1
            print(f"{q['id']}: llm_error: {exc}", file=sys.stderr)
            for c in q["candidates"]:
                c["llm"] = {"grade": None, "status": "llm_error", "evidence": ""}
            continue
        for c in q["candidates"]:
            c["llm"] = res.get(c["external_id"], {"grade": None, "evidence": "", "status": "missing"})
        print(f"{q['id']}: judged {len(cands)}", file=sys.stderr)
    _write_json(args.out, {"run_id": pooled.get("run_id") or "run", "model": model, "queries": pooled["queries"]})
    print(f"judged {len(pooled['queries']) - llm_errors}/{len(pooled['queries'])} queries, llm_error: {llm_errors}",
          file=sys.stderr)
    return 3 if llm_errors else 0


def cmd_sheet(args: argparse.Namespace) -> int:
    html = sheet.render(_read_json(args.judged), doc_base=args.doc_base or "")
    Path(args.out).write_text(html, encoding="utf-8")
    print(f"saved {args.out}")
    return 0


def cmd_import_labels(args: argparse.Namespace) -> int:
    judged, review = _read_json(args.judged), _read_json(args.review)
    try:
        built = labels.build_evalsets(judged, review, evalset_id=args.evalset_id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    out = Path(args.out_dir)
    _write_json(str(out / "evalset-v2-dev.json"), built["dev"])
    _write_json(str(out / "evalset-v2-frozen.json"), built["frozen"])
    _write_json(str(out / "report.json"), built["report"])
    print(json.dumps({**built["report"], "dev": len(built["dev"]["cases"]), "frozen": len(built["frozen"]["cases"])}))
    return 0


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
    po.set_defaults(fn=cmd_pool)

    j = sub.add_parser("judge")
    j.add_argument("--pooled", required=True)
    j.add_argument("--out", required=True)
    j.set_defaults(fn=cmd_judge)

    s = sub.add_parser("sheet")
    s.add_argument("--judged", required=True)
    s.add_argument("--doc-base", default="")
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
