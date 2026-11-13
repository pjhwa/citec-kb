"""번호 없는 DOgether [장애사례] 글 ↔ SWIM 장애(incident_reports) 내용 매칭.

    python -m app.matching.dogether_swim --eval | --dry-run | --apply | --export-review CSV | --import-review CSV

원칙
- 결과는 `dogether_swim_matches`에만 쓴다(게시글 metadata 금지: 수집기 재업로드 때 덮인다).
- `confirmed`·`rejected`·`manual` 행은 매처가 절대 바꾸지 않는다. 매처가 만지는 행은
  method=content 이면서 status in (auto, review)인 것뿐이다.
- config/dogether_swim.json의 `auto_enabled`가 false(기본)이면 auto 후보도 review로만 저장한다.
  T1/M이 null이면 auto 판정은 나오지 않는다. 기준값은 `--eval` 스윕 결과로 정한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import text

from app.db.session import session_scope
from app.matching.dates import extract_incident_dates
from app.settings import get_settings
from app.tickets.query import parse_meta_date

MATCHER_VERSION = "1"
CONFIG_FILE = "dogether_swim.json"

DEFAULTS: dict[str, Any] = {
    "auto_enabled": False,
    "T1": None,
    "M": None,
    "T2": 0.0,
    "weights": {"date": 0.45, "similarity": 0.35, "components": 0.15, "domain": 0.05},
    "date_tolerance_days": 1,
    "window_before_days": 210,
    "window_after_days": 7,
    "review_top_n": 3,
    "hub_component_df_max": 0.3,
}

# 2026-11-10 수작업 대조(Confluence 2651967735) 정답 쌍. 게시글 external_id → 장애 번호.
GOLD_PAIRS: dict[str, str] = {
    "dogether-17811": "26011660045",
    "dogether-17933": "26022460447",
    "dogether-17972": "26012360126",
    "dogether-18077": "26030460525",
    "dogether-18168": "26032260665",
    "dogether-18234": "26040560785",
    "dogether-19195": "26091461373",
}

_INC_DATE_KEY = "발생일시(한국)"


def load_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULTS))
    p = Path(get_settings().config_dir) / CONFIG_FILE
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    for k, v in raw.items():
        if k.startswith("_"):
            continue
        if k == "weights" and isinstance(v, dict):
            cfg["weights"].update(v)
        else:
            cfg[k] = v
    return cfg


@dataclass
class Post:
    document_id: str
    external_id: str
    title: str
    body: str
    posted: Optional[date]
    domain: Optional[str]
    content_hash: str
    swim_ids: list[str] = field(default_factory=list)


@dataclass
class Group:
    """한 글에서 한 날짜(또는 날짜 없음)에 대한 후보 묶음. ranked[0]이 1위."""
    date_key: str            # 'YYYY-MM-DD' 또는 'nodate'
    dated: bool
    ranked: list[dict[str, Any]]


# ── 점수 ────────────────────────────────────────────────────────────

def date_score(extracted: date, incident: date, tolerance: int) -> float:
    gap = abs((incident - extracted).days)
    if gap == 0:
        return 1.0
    if gap <= tolerance:
        return 0.7
    return 0.0


def total_score(signals: dict[str, float], weights: dict[str, float]) -> float:
    wsum = sum(weights.values()) or 1.0
    return round(sum(weights.get(k, 0.0) * signals.get(k, 0.0) for k in weights) / wsum, 4)


def judge(group: Group, cfg: dict[str, Any]) -> dict[str, str]:
    """{incident_document_id: 'auto'|'review'} - 저장할 행만 담는다."""
    r = group.ranked
    if not r:
        return {}
    top = r[0]
    out: dict[str, str] = {}
    if top["score"] < (cfg.get("T2") or 0.0):
        return out
    gap = top["score"] - (r[1]["score"] if len(r) > 1 else 0.0)
    t1, m = cfg.get("T1"), cfg.get("M")
    is_auto = bool(group.dated and t1 is not None and m is not None and top["score"] >= t1 and gap >= m)
    if is_auto and cfg.get("auto_enabled"):
        out[top["incident_document_id"]] = "auto"
        return out
    for c in r[: max(1, int(cfg.get("review_top_n") or 1))]:
        out[c["incident_document_id"]] = "review"
    return out


# ── DB 조회 ─────────────────────────────────────────────────────────

def load_posts(session, *, unnumbered_only: bool, ids: Optional[list[str]] = None) -> list[Post]:
    where = ["source_type='dogether_preventive'", "status='active'", "work_type='장애사례'"]
    params: dict[str, Any] = {}
    if unnumbered_only:
        where.append("coalesce(jsonb_array_length(metadata->'swim_ids'),0)=0")
        where.append("coalesce(jsonb_array_length(metadata->'swim_id_candidates'),0)=0")
    else:
        where.append("coalesce(jsonb_array_length(metadata->'swim_ids'),0)>0")
    if ids:
        where.append("external_id = ANY(:ids)")
        params["ids"] = ids
    rows = session.execute(
        text(f"SELECT id, external_id, title, body_md, metadata, domain, content_hash FROM documents "
             f"WHERE {' AND '.join(where)} ORDER BY external_id"), params,
    ).all()
    return [
        Post(r.id, r.external_id, r.title or "", r.body_md or "", parse_meta_date((r.metadata or {}).get("게시일시")),
             r.domain, r.content_hash, list((r.metadata or {}).get("swim_ids") or []))
        for r in rows
    ]


def _incidents_between(session, lo: date, hi: date, before: Optional[date]) -> list[dict[str, Any]]:
    """발생일이 [lo, hi]인 active 장애. before가 있으면 그 날짜 이전 발생만."""
    hi_excl = (hi + timedelta(days=1)).isoformat()
    sql = (
        "SELECT id, external_id, title, domain, metadata->>:k AS occurred, left(body_md, 20000) AS body "
        "FROM documents WHERE source_type='incident_reports' AND status='active' "
        "AND metadata->>:k >= :lo AND metadata->>:k < :hi"
    )
    params: dict[str, Any] = {"k": _INC_DATE_KEY, "lo": lo.isoformat(), "hi": hi_excl}
    if before:
        sql += " AND metadata->>:k < :before"
        params["before"] = before.isoformat()
    out = []
    for r in session.execute(text(sql), params):
        d = parse_meta_date(r.occurred)
        if d:
            out.append({"incident_document_id": r.id, "external_id": r.external_id, "title": r.title or "",
                        "domain": r.domain, "date": d, "body": r.body or ""})
    return out


def _similarity(session, post_id: str, incident_ids: list[str]) -> dict[str, float]:
    """게시글 청크와 장애 청크 임베딩의 최대 코사인(기존 임베딩 재사용)."""
    if not incident_ids:
        return {}
    rows = session.execute(
        text(
            "SELECT c2.document_id AS did, max(1 - (e1.vector <=> e2.vector)) AS sim "
            "FROM chunks c1 JOIN embeddings e1 ON e1.chunk_id=c1.id "
            "JOIN embeddings e2 ON e2.model=e1.model "
            "JOIN chunks c2 ON c2.id=e2.chunk_id "
            "WHERE c1.document_id=:p AND c1.is_active AND c2.is_active AND c2.document_id = ANY(:ids) "
            "GROUP BY c2.document_id"
        ),
        {"p": post_id, "ids": incident_ids},
    )
    return {r.did: max(0.0, min(1.0, float(r.sim))) for r in rows}


def _components(body: str, title: str, lexicon: dict[str, list[str]]) -> set[str]:
    from app.graph.extract import extract_lexicon_components

    edges = extract_lexicon_components({"body_md": f"{title}\n{body}"}, lexicon_map=lexicon)
    return {e.target_value for e in edges}


def match_post(session, post: Post, cfg: dict[str, Any], lexicon: dict[str, list[str]]) -> list[Group]:
    dates, _src = extract_incident_dates(post.body, post.posted)
    tol = int(cfg["date_tolerance_days"])
    post_comp = _components(post.body, post.title, lexicon) if lexicon else set()
    groups: list[Group] = []
    if dates:
        for d in dates:
            cands = _incidents_between(session, d - timedelta(days=tol), d + timedelta(days=tol), post.posted)
            groups.append(_score_group(session, post, cands, cfg, lexicon, post_comp, str(d), True, d))
    elif post.posted:
        lo = post.posted - timedelta(days=int(cfg["window_before_days"]))
        hi = post.posted - timedelta(days=int(cfg["window_after_days"]))
        cands = _incidents_between(session, lo, hi, post.posted)
        groups.append(_score_group(session, post, cands, cfg, lexicon, post_comp, "nodate", False, None))
    return groups


def _score_group(session, post, cands, cfg, lexicon, post_comp, key, dated, extracted) -> Group:
    sims = _similarity(session, post.document_id, [c["incident_document_id"] for c in cands])
    comps = {c["incident_document_id"]: (_components(c["body"], c["title"], lexicon) if lexicon else set()) for c in cands}
    # 후보 대부분에 나오는 범용 구성요소(hub)는 신호에서 뺀다.
    df: dict[str, int] = {}
    for s in comps.values():
        for name in s:
            df[name] = df.get(name, 0) + 1
    hubs = {n for n, k in df.items() if len(cands) >= 5 and k / len(cands) > float(cfg["hub_component_df_max"])}
    ranked = []
    for c in cands:
        iid = c["incident_document_id"]
        shared = (post_comp & comps[iid]) - hubs
        sig = {
            "date": date_score(extracted, c["date"], int(cfg["date_tolerance_days"])) if dated else 0.0,
            "similarity": sims.get(iid, 0.0),
            "components": min(1.0, len(shared) / 3.0),
            "domain": 1.0 if (post.domain and c["domain"] and post.domain == c["domain"]) else 0.0,
        }
        detail = dict(sig)
        detail["shared_components"] = sorted(shared)
        detail["lag_days"] = (post.posted - c["date"]).days if post.posted else None
        detail["date_key"] = key
        ranked.append({"incident_document_id": iid, "external_id": c["external_id"], "title": c["title"],
                       "score": total_score(sig, cfg["weights"]), "signals": detail})
    ranked.sort(key=lambda x: (-x["score"], x["external_id"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return Group(key, dated, ranked)


# ── 평가 ────────────────────────────────────────────────────────────

SWEEP_T1 = [round(0.30 + 0.05 * i, 2) for i in range(0, 13)]   # 0.30 .. 0.90
SWEEP_M = [0.0, 0.05, 0.10, 0.15, 0.20]


def evaluate(session, cfg, lexicon) -> dict[str, Any]:
    """번호 있는 글에서 swim_ids를 숨기고 매처를 돌려 원래 번호를 1위로 찾는지 잰다. 쓰기 없음."""
    posts = load_posts(session, unnumbered_only=False)
    truth_by_post: dict[str, set[str]] = {}
    for p in posts:
        ids = session.execute(
            text("SELECT id FROM documents WHERE source_type='incident_reports' AND status='active' AND external_id = ANY(:e)"),
            {"e": p.swim_ids},
        ).scalars().all()
        truth_by_post[p.document_id] = set(ids)
    dated_groups: list[tuple[Group, set[str]]] = []
    undated: list[str] = []
    in_candidates = 0
    for p in posts:
        truth = truth_by_post[p.document_id]
        if not truth:
            continue
        groups = match_post(session, p, cfg, lexicon)
        if not any(g.dated for g in groups):
            undated.append(p.external_id)
        for g in groups:
            if g.dated:
                dated_groups.append((g, truth))
        if any(c["incident_document_id"] in truth for g in groups for c in g.ranked):
            in_candidates += 1
    # 한 글이 장애 여러 건을 다룰 수 있으므로 날짜 묶음이 정답 장애를 하나라도 포함할 때만 정확도 계산 대상으로 본다.
    scorable = [(g, t) for g, t in dated_groups if any(c["incident_document_id"] in t for c in g.ranked)]
    top1 = sum(1 for g, t in scorable if g.ranked and g.ranked[0]["incident_document_id"] in t)
    sweep = []
    for t1 in SWEEP_T1:
        for m in SWEEP_M:
            auto = hit = 0
            for g, t in dated_groups:
                if not g.ranked:
                    continue
                gap = g.ranked[0]["score"] - (g.ranked[1]["score"] if len(g.ranked) > 1 else 0.0)
                if g.ranked[0]["score"] >= t1 and gap >= m:
                    auto += 1
                    hit += 1 if g.ranked[0]["incident_document_id"] in t else 0
            sweep.append({"T1": t1, "M": m, "auto": auto, "correct": hit,
                          "precision": round(hit / auto, 4) if auto else None})
    gold = {}
    for ext, inc in GOLD_PAIRS.items():
        row = session.execute(text("SELECT id FROM documents WHERE source_type='dogether_preventive' AND external_id=:e"), {"e": ext}).first()
        iid = session.execute(text("SELECT id FROM documents WHERE source_type='incident_reports' AND external_id=:e"), {"e": inc}).scalar()
        if not row:
            gold[ext] = "post_missing"
            continue
        post = next(iter(load_posts(session, unnumbered_only=False, ids=[ext])), None) or next(
            iter(load_posts(session, unnumbered_only=True, ids=[ext])), None)
        if not post or not iid:
            gold[ext] = "not_work_type_or_incident_missing"
            continue
        ranks = [c["rank"] for g in match_post(session, post, cfg, lexicon) for c in g.ranked if c["incident_document_id"] == iid]
        gold[ext] = {"incident": inc, "rank": min(ranks) if ranks else None}
    return {
        "numbered_posts_evaluated": sum(1 for t in truth_by_post.values() if t),
        "posts_without_extracted_date": len(undated),
        "posts_without_extracted_date_ids": undated,
        "posts_truth_in_candidates": in_candidates,
        "dated_groups": len(dated_groups),
        "dated_groups_with_truth_in_candidates": len(scorable),
        "top1_hits": top1,
        "top1_rate": round(top1 / len(scorable), 4) if scorable else None,
        "sweep": sweep,
        "gold_pairs": gold,
        "config_used": {k: cfg[k] for k in ("weights", "date_tolerance_days", "window_before_days", "window_after_days")},
        "matcher_version": MATCHER_VERSION,
    }


# ── 실행(dry-run / apply) ───────────────────────────────────────────

def _desired_rows(groups: list[Group], cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for g in groups:
        decisions = judge(g, cfg)
        for c in g.ranked:
            st = decisions.get(c["incident_document_id"])
            if st:
                prev = out.get(c["incident_document_id"])
                if prev is None or c["score"] > prev["score"]:
                    out[c["incident_document_id"]] = {**c, "status": st}
    return out


def select_posts(session, *, force: bool, limit: Optional[int]) -> list[Post]:
    posts = load_posts(session, unnumbered_only=True)
    if not force:
        runs = {r.post_document_id: (r.content_hash, r.matcher_version)
                for r in session.execute(text("SELECT post_document_id, content_hash, matcher_version FROM dogether_swim_match_runs"))}
        posts = [p for p in posts if runs.get(p.document_id) != (p.content_hash, MATCHER_VERSION)]
    return posts[:limit] if limit else posts


def apply_post(session, post: Post, groups: list[Group], cfg: dict[str, Any]) -> dict[str, int]:
    desired = _desired_rows(groups, cfg)
    existing = {r.incident_document_id: r for r in session.execute(text(
        "SELECT incident_document_id, method, status FROM dogether_swim_matches WHERE post_document_id=:p"),
        {"p": post.document_id})}
    stats = {"inserted": 0, "updated": 0, "deleted": 0}
    for iid, ex in existing.items():
        if ex.method == "content" and ex.status in ("auto", "review") and iid not in desired:
            session.execute(text("DELETE FROM dogether_swim_matches WHERE post_document_id=:p AND incident_document_id=:i"),
                            {"p": post.document_id, "i": iid})
            stats["deleted"] += 1
    for iid, row in desired.items():
        ex = existing.get(iid)
        params = {"p": post.document_id, "i": iid, "s": row["score"], "r": row["rank"],
                  "sig": json.dumps(row["signals"], ensure_ascii=False), "st": row["status"], "v": MATCHER_VERSION}
        if ex is None:
            session.execute(text(
                "INSERT INTO dogether_swim_matches (post_document_id, incident_document_id, method, score, rank, signals, status, matcher_version) "
                "VALUES (:p,:i,'content',:s,:r,CAST(:sig AS jsonb),:st,:v)"), params)
            stats["inserted"] += 1
        elif ex.method == "content" and ex.status in ("auto", "review"):
            session.execute(text(
                "UPDATE dogether_swim_matches SET score=:s, rank=:r, signals=CAST(:sig AS jsonb), status=:st, matcher_version=:v, updated_at=now() "
                "WHERE post_document_id=:p AND incident_document_id=:i"), params)
            stats["updated"] += 1
        # confirmed / rejected / manual 행은 건드리지 않는다.
    session.execute(text(
        "INSERT INTO dogether_swim_match_runs (post_document_id, content_hash, matcher_version, candidates) VALUES (:p,:h,:v,:n) "
        "ON CONFLICT (post_document_id) DO UPDATE SET content_hash=:h, matcher_version=:v, candidates=:n, updated_at=now()"),
        {"p": post.document_id, "h": post.content_hash, "v": MATCHER_VERSION, "n": sum(len(g.ranked) for g in groups)})
    return stats


def run(*, apply: bool, force: bool, limit: Optional[int]) -> dict[str, Any]:
    from app.lexicon.seed import load_lexicon_map

    cfg = load_config()
    lexicon = load_lexicon_map()
    dist = {"posts": 0, "auto": 0, "review": 0, "no_candidate": 0, "no_date": 0}
    totals = {"inserted": 0, "updated": 0, "deleted": 0}
    with session_scope() as session:
        for post in select_posts(session, force=force, limit=limit):
            groups = match_post(session, post, cfg, lexicon)
            desired = _desired_rows(groups, cfg)
            dist["posts"] += 1
            if not any(g.dated for g in groups):
                dist["no_date"] += 1
            if not desired:
                dist["no_candidate"] += 1
            elif any(r["status"] == "auto" for r in desired.values()):
                dist["auto"] += 1
            else:
                dist["review"] += 1
            if apply:
                for k, v in apply_post(session, post, groups, cfg).items():
                    totals[k] += v
    return {"mode": "apply" if apply else "dry-run", "auto_enabled": bool(cfg["auto_enabled"]),
            "matcher_version": MATCHER_VERSION, "distribution": dist, **({"writes": totals} if apply else {})}


# ── 검토 목록 CSV ───────────────────────────────────────────────────

REVIEW_COLUMNS = ["post_external_id", "post_title", "post_url", "incident_external_id", "incident_title",
                  "score", "rank", "status", "signals", "decision"]


def export_review(path: str) -> int:
    with session_scope() as session:
        rows = session.execute(text(
            "SELECT p.external_id pe, p.title pt, p.source_uri pu, i.external_id ie, i.title it, m.score, m.rank, m.status, m.signals "
            "FROM dogether_swim_matches m JOIN documents p ON p.id=m.post_document_id JOIN documents i ON i.id=m.incident_document_id "
            "WHERE m.status IN ('review','auto') ORDER BY p.external_id, m.rank")).all()
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(REVIEW_COLUMNS)
        for r in rows:
            w.writerow([r.pe, r.pt, r.pu, r.ie, r.it, r.score, r.rank, r.status, json.dumps(r.signals, ensure_ascii=False), ""])
    return len(rows)


def import_review(path: str) -> dict[str, int]:
    """decision 열이 confirmed|rejected인 행만 반영한다. 반영된 행은 method=manual이 되어 매처가 다시 만지지 않는다."""
    stats = {"confirmed": 0, "rejected": 0, "skipped": 0}
    with open(path, encoding="utf-8-sig", newline="") as f, session_scope() as session:
        for row in csv.DictReader(f):
            dec = (row.get("decision") or "").strip().lower()
            if dec not in ("confirmed", "rejected"):
                stats["skipped"] += 1
                continue
            ids = session.execute(text(
                "SELECT (SELECT id FROM documents WHERE source_type='dogether_preventive' AND external_id=:p) pid, "
                "(SELECT id FROM documents WHERE source_type='incident_reports' AND external_id=:i) iid"),
                {"p": row["post_external_id"].strip(), "i": row["incident_external_id"].strip()}).one()
            if not ids.pid or not ids.iid:
                stats["skipped"] += 1
                continue
            session.execute(text(
                "INSERT INTO dogether_swim_matches (post_document_id, incident_document_id, method, score, rank, signals, status, matcher_version) "
                "VALUES (:p,:i,'manual',:s,:r,CAST(:sig AS jsonb),:st,:v) "
                "ON CONFLICT (post_document_id, incident_document_id) DO UPDATE SET method='manual', status=:st, updated_at=now()"),
                {"p": ids.pid, "i": ids.iid, "s": float(row.get("score") or 0), "r": int(row.get("rank") or 1),
                 "sig": row.get("signals") or "{}", "st": dec, "v": MATCHER_VERSION})
            stats[dec] += 1
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--eval", action="store_true", help="번호 숨김 평가 + 정답 쌍 + T1/M 스윕 표(쓰기 없음)")
    g.add_argument("--dry-run", action="store_true", help="새·바뀐 글을 매칭해 분포만 출력(쓰기 없음)")
    g.add_argument("--apply", action="store_true", help="새·바뀐 글을 매칭해 저장")
    g.add_argument("--export-review", metavar="CSV")
    g.add_argument("--import-review", metavar="CSV")
    ap.add_argument("--all", action="store_true", help="이미 처리한 글도 다시(매처 버전이 바뀔 때)")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    if args.eval:
        from app.lexicon.seed import load_lexicon_map

        with session_scope() as session:
            out = evaluate(session, load_config(), load_lexicon_map())
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
