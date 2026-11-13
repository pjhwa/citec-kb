"""LOOKIN(Confluence) `Ⅲ. CI-TEC 기술지원` 문서 ↔ SWIM 장애(incident_reports) 내용 매칭.

    python -m app.matching.lookin_swim --eval | --dry-run | --apply | --export-review CSV | --import-review CSV

대상은 연도 폴더(`NN년 기술지원`) 바로 아래 문서 중 제목이 `[MM/DD ...]`로 시작하는 것(하위 문서는 제외).
제목의 월/일 + 폴더의 연도로 지원 날짜를 만들고, 그 날짜 ±N일의 SWIM 장애를 후보로 점수를 매긴다.
점수·판정·재실행 안전 규칙은 dogether_swim과 같다(`confirmed`·`rejected`·`manual` 행은 건드리지 않음).
결과는 `lookin_swim_matches`에만 쓴다. 설정은 config/lookin_swim.json(기본 auto 꺼짐).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from typing import Any, Optional

from sqlalchemy import text

from app.db.session import session_scope
from app.matching import dogether_swim as dg
from app.matching.dogether_swim import Group, Post
from app.tickets.query import parse_meta_date

MATCHER_VERSION = "1"
CONFIG_FILE = "lookin_swim.json"
MATCHES = "lookin_swim_matches"
RUNS = "lookin_swim_match_runs"
PATH_PREFIX = "[CI-TEC] > Ⅲ. CI-TEC 기술지원"

_TITLE_DATE = re.compile(r"^\s*\[\s*(\d{1,2})\s*/\s*(\d{1,2})(?!\d)")
_YEAR_DIR = re.compile(r"(\d{2})년\s*기술지원")
_SWIM_NO = re.compile(r"(?<![0-9])(2\d{10})(?![0-9])")


def support_date(title: str, path: str) -> Optional[date]:
    """제목 `[MM/DD...]` + 경로의 `NN년 기술지원` → 날짜. 어느 한쪽이 없거나 달력에 없는 날짜면 None."""
    m, y = _TITLE_DATE.match(title or ""), _YEAR_DIR.search(path or "")
    if not m or not y:
        return None
    try:
        return date(2000 + int(y.group(1)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def load_pages(session, ids: Optional[list[str]] = None) -> list[Post]:
    """기술지원 연도 폴더 바로 아래 문서. Post.posted에 지원 날짜(제목 기준)를 넣는다 - 없으면 None."""
    where = ["source_type='confluence_map'", "status='active'", "metadata->>'space_key'='LOOKIN'",
             "metadata->>'경로' LIKE :pfx", "array_length(string_to_array(metadata->>'경로',' > '),1)=4"]
    params: dict[str, Any] = {"pfx": PATH_PREFIX + "%"}
    if ids:
        where.append("external_id = ANY(:ids)")
        params["ids"] = ids
    rows = session.execute(text(
        "SELECT id, external_id, title, body_md, metadata->>'경로' AS path, domain, content_hash FROM documents "
        f"WHERE {' AND '.join(where)} ORDER BY external_id"), params).all()
    return [Post(r.id, r.external_id, r.title or "", r.body_md or "", support_date(r.title or "", r.path or ""),
                 r.domain, r.content_hash, sorted(set(_SWIM_NO.findall(r.body_md or "")))) for r in rows]


def match_page(session, page: Post, cfg: dict[str, Any], lexicon: dict[str, list[str]]) -> list[Group]:
    """날짜가 없는 문서는 후보 창이 없으므로 빈 목록(= '날짜 없음'으로 집계)."""
    if not page.posted:
        return []
    tol = int(cfg["date_tolerance_days"])
    from datetime import timedelta

    cands = dg._incidents_between(session, page.posted - timedelta(days=tol), page.posted + timedelta(days=tol), None)
    comp = dg._components(page.body, page.title, lexicon) if lexicon else set()
    g = dg._score_group(session, page, cands, cfg, lexicon, comp, str(page.posted), True, page.posted)
    return [g]


def evaluate(session, cfg, lexicon) -> dict[str, Any]:
    """본문에 SWIM 번호가 적힌 문서로 평가. 번호가 그 문서의 장애가 아니라 '참고 인용'일 수 있어 정답이 노이즈를 포함한다."""
    pages = [p for p in load_pages(session) if p.swim_ids]
    groups_truth: list[tuple[Group, set[str]]] = []
    stats = {"pages_with_swim_no": len(pages), "no_title_date": 0, "number_not_in_kb": 0,
             "truth_in_date_candidates": 0}
    for p in pages:
        truth = set(session.execute(text(
            "SELECT id FROM documents WHERE source_type='incident_reports' AND status='active' AND external_id = ANY(:e)"),
            {"e": p.swim_ids}).scalars().all())
        if not truth:
            stats["number_not_in_kb"] += 1
            continue
        groups = match_page(session, p, cfg, lexicon)
        if not groups:
            stats["no_title_date"] += 1
            continue
        if any(c["incident_document_id"] in truth for g in groups for c in g.ranked):
            stats["truth_in_date_candidates"] += 1
            groups_truth.extend((g, truth) for g in groups)
    top1 = sum(1 for g, t in groups_truth if g.ranked and g.ranked[0]["incident_document_id"] in t)
    return {**stats, "scorable_groups": len(groups_truth), "top1_hits": top1,
            "top1_rate": round(top1 / len(groups_truth), 4) if groups_truth else None,
            "sweep": dg.sweep_table(groups_truth), "matcher_version": MATCHER_VERSION}


def run(*, apply: bool, force: bool, limit: Optional[int]) -> dict[str, Any]:
    from app.lexicon.seed import load_lexicon_map

    cfg = dg.load_config(CONFIG_FILE)
    lexicon = load_lexicon_map()
    dist = {"pages": 0, "auto": 0, "review": 0, "no_candidate": 0, "no_date": 0}
    totals = {"inserted": 0, "updated": 0, "deleted": 0}
    by_year: dict[str, dict[str, int]] = {}
    with session_scope() as session:
        pages = dg.select_posts(session, force=force, limit=limit, posts=load_pages(session), runs_table=RUNS)
        for page in pages:
            groups = match_page(session, page, cfg, lexicon)
            desired = dg._desired_rows(groups, cfg)
            dg.probe_summary(by_year, str(page.posted.year) if page.posted else "no_date", dg.probe(groups))
            dist["pages"] += 1
            if not groups:
                dist["no_date"] += 1
            elif not desired:
                dist["no_candidate"] += 1
            elif any(r["status"] == "auto" for r in desired.values()):
                dist["auto"] += 1
            else:
                dist["review"] += 1
            if apply:
                for k, v in dg.apply_post(session, page, groups, cfg, matches_table=MATCHES, runs_table=RUNS).items():
                    totals[k] += v
    return {"mode": "apply" if apply else "dry-run", "auto_enabled": bool(cfg["auto_enabled"]),
            "matcher_version": MATCHER_VERSION, "distribution": dist, "probe_T1": dg.PROBE_T1,
            "by_year": dict(sorted(by_year.items())), **({"writes": totals} if apply else {})}


REVIEW_COLUMNS = ["page_external_id", "page_title", "page_url", "incident_external_id", "incident_title",
                  "score", "rank", "status", "signals", "decision"]


def export_review(path: str) -> int:
    with session_scope() as session:
        rows = session.execute(text(
            "SELECT p.external_id pe, p.title pt, p.source_uri pu, i.external_id ie, i.title it, m.score, m.rank, m.status, m.signals "
            f"FROM {MATCHES} m JOIN documents p ON p.id=m.post_document_id JOIN documents i ON i.id=m.incident_document_id "
            "WHERE m.status IN ('review','auto') ORDER BY p.external_id, m.rank")).all()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(REVIEW_COLUMNS)
        for r in rows:
            w.writerow([r.pe, r.pt, r.pu, r.ie, r.it, r.score, r.rank, r.status, json.dumps(r.signals, ensure_ascii=False), ""])
    return len(rows)


def import_review(path: str) -> dict[str, int]:
    stats = {"confirmed": 0, "rejected": 0, "skipped": 0}
    with open(path, encoding="utf-8-sig", newline="") as f, session_scope() as session:
        for row in csv.DictReader(f):
            dec = (row.get("decision") or "").strip().lower()
            if dec not in ("confirmed", "rejected"):
                stats["skipped"] += 1
                continue
            ids = session.execute(text(
                "SELECT (SELECT id FROM documents WHERE source_type='confluence_map' AND external_id=:p) pid, "
                "(SELECT id FROM documents WHERE source_type='incident_reports' AND external_id=:i) iid"),
                {"p": row["page_external_id"].strip(), "i": row["incident_external_id"].strip()}).one()
            if not ids.pid or not ids.iid:
                stats["skipped"] += 1
                continue
            session.execute(text(
                f"INSERT INTO {MATCHES} (post_document_id, incident_document_id, method, score, rank, signals, status, matcher_version) "
                "VALUES (:p,:i,'manual',:s,:r,CAST(:sig AS jsonb),:st,:v) "
                "ON CONFLICT (post_document_id, incident_document_id) DO UPDATE SET method='manual', status=:st, updated_at=now()"),
                {"p": ids.pid, "i": ids.iid, "s": float(row.get("score") or 0), "r": int(row.get("rank") or 1),
                 "sig": row.get("signals") or "{}", "st": dec, "v": MATCHER_VERSION})
            stats[dec] += 1
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--eval", action="store_true")
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--export-review", metavar="CSV")
    g.add_argument("--import-review", metavar="CSV")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    if args.eval:
        from app.lexicon.seed import load_lexicon_map

        with session_scope() as session:
            out = evaluate(session, dg.load_config(CONFIG_FILE), load_lexicon_map())
    elif args.export_review:
        out = {"exported": export_review(args.export_review)}
    elif args.import_review:
        out = import_review(args.import_review)
    else:
        out = run(apply=args.apply, force=args.all, limit=args.limit)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
