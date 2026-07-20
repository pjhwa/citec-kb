"""Gold retrieval eval — calls shipped hybrid_search (not a reimplementation)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.models import Document
from app.db.session import session_scope
from app.retrieval.search import SearchFilters, SearchRequest, hybrid_search

logger = logging.getLogger("citec.eval")


def build_gold_from_db(min_queries: int = 50) -> list[dict[str, Any]]:
    """Construct ≥50 labeled queries from real DB content."""
    gold: list[dict[str, Any]] = []
    with session_scope() as session:
        # 1) Issue-key exact queries from support_history
        rows = session.execute(
            select(Document.external_id, Document.title)
            .where(Document.source_type == "support_history")
            .where(Document.status == "active")
            .where(Document.external_id.like("CITECTS-%"))
            .order_by(Document.external_id)
            .limit(25)
        ).all()
        for ext, title in rows:
            gold.append(
                {
                    "id": f"exact-{ext}",
                    "q": ext,
                    "relevant_external_ids": [ext],
                    "filters": {"source_type": "support_history"},
                    "kind": "exact_key",
                }
            )

        # 2) Title keyword queries (first significant token phrase)
        rows = session.execute(
            select(Document.external_id, Document.title)
            .where(Document.source_type == "support_history")
            .where(Document.title.ilike("%모니모%"))
            .limit(8)
        ).all()
        if rows:
            gold.append(
                {
                    "id": "entity-monimo",
                    "q": "모니모 Redis 통신 장애",
                    "relevant_external_ids": [r.external_id for r in rows],
                    "filters": {"source_type": "support_history"},
                    "kind": "entity",
                }
            )
            gold.append(
                {
                    "id": "entity-monimo-list",
                    "q": "모니모",
                    "relevant_external_ids": [r.external_id for r in rows],
                    "filters": {"source_type": "support_history"},
                    "kind": "entity",
                }
            )

        # 3) Checkitem code queries
        rows = session.execute(
            select(Document.external_id, Document.title)
            .where(Document.source_type == "checkitem")
            .where(Document.external_id.like("PISAOLNX%"))
            .order_by(Document.external_id)
            .limit(15)
        ).all()
        for ext, title in rows:
            gold.append(
                {
                    "id": f"pisa-{ext}",
                    "q": ext,
                    "relevant_external_ids": [ext],
                    "filters": {"source_type": "checkitem"},
                    "kind": "exact_code",
                }
            )

        # 4) Tech repo page id
        rows = session.execute(
            select(Document.external_id, Document.title)
            .where(Document.source_type == "tech_repo")
            .where(Document.title != "")
            .order_by(Document.external_id)
            .limit(15)
        ).all()
        for ext, title in rows:
            # use short distinctive title words + filter
            q = (title or ext)[:40].strip() or ext
            gold.append(
                {
                    "id": f"tech-{ext}",
                    "q": q if len(q) >= 4 else ext,
                    "relevant_external_ids": [ext],
                    "filters": {"source_type": "tech_repo"},
                    "kind": "tech_title",
                }
            )

        # 5) Free-text Korean technical
        for q, src in [
            ("파일 시스템 체크 fstab", "checkitem"),
            ("GRO offload rx-gro-hw", "support_history"),
            ("메모리 커널 파라미터", "tech_repo"),
        ]:
            rows = session.execute(
                select(Document.external_id)
                .where(Document.source_type == src)
                .where(
                    (Document.title.ilike(f"%{q.split()[0]}%"))
                    | (Document.body_md.ilike(f"%{q.split()[0]}%"))
                )
                .limit(5)
            ).all()
            rel = [r[0] for r in rows]
            if rel:
                gold.append(
                    {
                        "id": f"ft-{src}-{q[:12]}",
                        "q": q,
                        "relevant_external_ids": rel,
                        "filters": {"source_type": src},
                        "kind": "freetext",
                    }
                )

    # dedupe by id
    seen = set()
    uniq = []
    for g in gold:
        if g["id"] in seen:
            continue
        seen.add(g["id"])
        uniq.append(g)
    if len(uniq) < min_queries:
        logger.warning("gold size %s < %s", len(uniq), min_queries)
    return uniq[: max(min_queries, len(uniq))]


def precision_at_k(retrieved: list[str], relevant: set[str], k: int = 3) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    return sum(1 for x in top if x in relevant) / float(k)


def hit_at_k(retrieved: list[str], relevant: set[str], k: int = 3) -> float:
    top = retrieved[:k]
    return 1.0 if any(x in relevant for x in top) else 0.0


def run_eval(
    gold: list[dict[str, Any]],
    *,
    use_vectors: bool = True,
    top_k: int = 10,
) -> dict[str, Any]:
    qvec_fn = None
    if use_vectors:
        try:
            from app.embed.model import embed_query

            qvec_fn = embed_query
        except Exception as exc:  # noqa: BLE001
            logger.warning("embed unavailable: %s", exc)

    per_query = []
    p3_sum = 0.0
    hit3_sum = 0.0
    n = 0
    with session_scope() as session:
        for g in gold:
            qvec = qvec_fn(g["q"]) if qvec_fn else None
            filters = SearchFilters(**(g.get("filters") or {}))
            req = SearchRequest(q=g["q"], top_k=top_k, filters=filters)
            resp = hybrid_search(session, req, query_vector=qvec)
            retrieved = [r.external_id for r in resp.results]
            rel = set(g["relevant_external_ids"])
            p3 = precision_at_k(retrieved, rel, 3)
            h3 = hit_at_k(retrieved, rel, 3)
            p3_sum += p3
            hit3_sum += h3
            n += 1
            per_query.append(
                {
                    "id": g["id"],
                    "q": g["q"],
                    "kind": g.get("kind"),
                    "p@3": p3,
                    "hit@3": h3,
                    "retrieved": retrieved[:5],
                    "relevant": list(rel)[:10],
                    "trust": resp.trust_retrieval,
                    "vector_used": qvec is not None,
                }
            )

    report = {
        "n_queries": n,
        "precision_at_3": (p3_sum / n) if n else 0.0,
        "hit_at_3": (hit3_sum / n) if n else 0.0,
        # Phase-1 gate uses hit@3 as success rate (single-label friendly); also report classic p@3
        "gate_metric": "hit_at_3",
        "gate_score": (hit3_sum / n) if n else 0.0,
        "pass": ((hit3_sum / n) if n else 0.0) >= 0.90 and n >= 50,
        "per_query": per_query,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--min-queries", type=int, default=50)
    ap.add_argument("--no-vector", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    gold = build_gold_from_db(min_queries=args.min_queries)
    # ensure at least 50
    if len(gold) < 50:
        print(json.dumps({"ok": False, "error": f"gold too small: {len(gold)}"}, ensure_ascii=False))
        return 2
    report = run_eval(gold[: max(50, args.min_queries)], use_vectors=not args.no_vector)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text[:2000])
    print(
        f"\nSUMMARY n={report['n_queries']} hit@3={report['hit_at_3']:.4f} "
        f"p@3={report['precision_at_3']:.4f} pass={report['pass']}"
    )
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
