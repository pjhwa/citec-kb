#!/usr/bin/env python3
"""Count what the masker finds in documents WITHOUT printing any document text or matched value.

  python3 scripts/masking_probe.py --ancestor 224797007            # DB mode (needs DATABASE_URL)
  python3 scripts/masking_probe.py --source-type support_history --limit 200
  python3 scripts/masking_probe.py --stdin-jsonl < docs.jsonl      # no DB; lines: {"external_id","title","body"}
Options: --summary-only (totals only), --residual (re-scan MASKED text with raw patterns; expect 0).
Output: external_id, title (first 40 chars), per-group counts, totals. Never body text or matched values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api"))

from app.masking import Masker, get_dictionary  # noqa: E402
from app.masking import patterns as P  # noqa: E402

ALL_GROUPS = frozenset({"pii", "net", "biz"})


def _masked_title(title: str, dic) -> str:
    """First 40 chars; if the title itself triggers masking, print the masked form."""
    mk = Masker(ALL_GROUPS, dic)
    out = mk.mask_text((title or "")[:40])
    return out


def _residual_hit(masked: str, dic) -> bool:
    if P.PHONE.search(masked) or P.ADDR.search(masked):
        return True
    for rx in (P.IPV4, P.IPV6):
        if any(m.group(0) not in dic.allow for m in rx.finditer(masked)):
            return True
    return False


def scan_docs(docs, dic, residual: bool = False) -> dict:
    totals: Counter = Counter()
    out_docs = []
    with_hits = 0
    residual_ids = []
    n = 0
    for d in docs:
        n += 1
        ext = str(d.get("external_id", ""))
        mk = Masker(ALL_GROUPS, dic)
        masked = mk.mask_text(d.get("body") or "")
        if mk.counts:
            with_hits += 1
        totals.update(mk.counts)
        if residual and _residual_hit(masked, dic):
            residual_ids.append(ext)
        out_docs.append(
            {"external_id": ext, "title": _masked_title(d.get("title") or "", dic), "counts": dict(mk.counts)}
        )
    return {
        "docs_scanned": n,
        "with_hits": with_hits,
        "totals": dict(totals),
        "docs": out_docs,
        "residual_checked": residual,
        "residual_count": len(residual_ids),
        "residual_ids": residual_ids,
        "dictionary": dic.status,
    }


def print_report(r: dict, summary_only: bool = False) -> None:
    if not summary_only:
        for d in r["docs"]:
            if d["counts"]:
                print(f"{d['external_id']}\t{d['title']}\t{d['counts']}")
    print(f"\ndocs scanned={r['docs_scanned']} with_hits={r['with_hits']} totals={r['totals']} dictionary={r['dictionary']}")
    if r["residual_checked"]:
        print(f"residual docs={r['residual_count']} ids={r['residual_ids']}")


def read_jsonl(fh):
    docs = []
    for line in fh:
        line = line.strip()
        if line:
            docs.append(json.loads(line))
    return docs


def _load_db(a):
    from sqlalchemy import text

    from app.db.session import session_scope

    where, params = "source_type = :st", {"st": a.source_type, "lim": a.limit}
    if a.ancestor:
        where += " AND metadata->'ancestor_ids' ? :anc"
        params["anc"] = a.ancestor
    sql = f"SELECT external_id, left(title, 40), body_md FROM documents WHERE {where} AND length(body_md) > 100 LIMIT :lim"
    with session_scope() as s:
        rows = s.execute(text(sql), params).all()
    return [{"external_id": e, "title": t, "body": b} for e, t, b in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ancestor", help="confluence_map ancestor page id")
    ap.add_argument("--source-type", default="confluence_map")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--stdin-jsonl", action="store_true", help="read docs from stdin (no DB)")
    ap.add_argument("--summary-only", action="store_true")
    ap.add_argument("--residual", action="store_true")
    a = ap.parse_args()

    docs = read_jsonl(sys.stdin) if a.stdin_jsonl else _load_db(a)
    r = scan_docs(docs, get_dictionary(), a.residual)
    print_report(r, a.summary_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
