"""개발 ↔ 운영 매칭 결과·문서 목록 동기화 (dogether_swim_matches, lookin_swim_matches).

    python -m app.matching.sync_matches --export FILE.jsonl
    python -m app.matching.sync_matches --diff A.jsonl B.jsonl
    python -m app.matching.sync_matches --import FILE.jsonl [--apply] [--only-human]

- 문서 id가 아니라 (source_type, external_id)로 문서를 가리키므로 환경이 달라도 옮길 수 있다.
- export에는 ① 매칭 행 ② 문서 목록(source_type, external_id, content_hash)이 들어 있다. 문서 목록은 매칭에 쓰이는
  문서만 담는다: dogether_preventive 전체, LOOKIN 기술지원 문서, incident_reports.
- --diff는 두 export(예: dev와 prod)를 비교해 한쪽에만 있는 문서·매칭 행, 상태가 다른 행, 상대 환경에 문서가 없어
  옮길 수 없는 행을 센다. 쓰기 없음.
- --import는 기본이 미리보기(--apply를 줘야 쓴다). 가져올 때의 규칙:
    * 대상 환경의 confirmed·rejected·manual 행은 절대 덮어쓰지 않는다.
    * 대상에 없는 쌍은 넣는다. 대상의 content 행(auto·review)은 들어오는 행이 사람 결정(manual)일 때만 바꾼다.
    * 대상에 문서가 없는 쌍은 건너뛰고 세어 알린다.
    * --only-human: 사람 결정(manual)만 가져온다(운영에서 content 행을 직접 계산할 때).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import text

from app.db.session import session_scope

# 매칭 테이블 → (게시글 쪽 source_type, 이름)
TABLES: dict[str, str] = {
    "dogether_swim_matches": "dogether_preventive",
    "lookin_swim_matches": "confluence_map",
}
INCIDENT = "incident_reports"
LOOKIN_PFX = "[CI-TEC] > Ⅲ. CI-TEC 기술지원"


def _inventory(session) -> Iterable[dict[str, Any]]:
    q = (
        "SELECT source_type, external_id, content_hash FROM documents WHERE status='active' AND ("
        "source_type IN ('dogether_preventive','incident_reports') OR "
        "(source_type='confluence_map' AND metadata->>'space_key'='LOOKIN' AND metadata->>'경로' LIKE :pfx)) "
        "ORDER BY source_type, external_id"
    )
    for r in session.execute(text(q), {"pfx": LOOKIN_PFX + "%"}):
        yield {"kind": "doc", "source_type": r.source_type, "external_id": r.external_id, "content_hash": r.content_hash}


def _matches(session) -> Iterable[dict[str, Any]]:
    for table, post_type in TABLES.items():
        rows = session.execute(text(
            f"SELECT p.external_id pe, i.external_id ie, m.method, m.status, m.score, m.rank, m.signals, m.matcher_version "
            f"FROM {table} m JOIN documents p ON p.id=m.post_document_id JOIN documents i ON i.id=m.incident_document_id "
            f"ORDER BY p.external_id, i.external_id"))
        for r in rows:
            yield {"kind": "match", "table": table, "post_source_type": post_type, "post": r.pe, "incident": r.ie,
                   "method": r.method, "status": r.status, "score": r.score, "rank": r.rank,
                   "signals": r.signals, "matcher_version": r.matcher_version}


def export(path: str) -> dict[str, int]:
    counts = {"docs": 0, "matches": 0}
    with session_scope() as session, open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"kind": "meta", "generated_at": datetime.now(timezone.utc).isoformat(),
                            "format": 1}, ensure_ascii=False) + "\n")
        for row in _inventory(session):
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            counts["docs"] += 1
        for row in _matches(session):
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            counts["matches"] += 1
    return counts


def _load(path: str) -> tuple[dict, dict, dict]:
    meta: dict = {}
    docs: dict[tuple[str, str], str] = {}
    matches: dict[tuple[str, str, str], dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            if r["kind"] == "meta":
                meta = r
            elif r["kind"] == "doc":
                docs[(r["source_type"], r["external_id"])] = r["content_hash"]
            elif r["kind"] == "match":
                matches[(r["table"], r["post"], r["incident"])] = r
    return meta, docs, matches


def diff(path_a: str, path_b: str, sample: int = 10) -> dict[str, Any]:
    _, da, ma = _load(path_a)
    _, db, mb = _load(path_b)
    out: dict[str, Any] = {"A": path_a, "B": path_b, "docs": {}, "matches": {}}
    for st in sorted({k[0] for k in (*da, *db)}):
        ka = {k[1] for k in da if k[0] == st}
        kb = {k[1] for k in db if k[0] == st}
        changed = sorted(e for e in ka & kb if da[(st, e)] != db[(st, e)])
        out["docs"][st] = {"A": len(ka), "B": len(kb), "only_A": len(ka - kb), "only_B": len(kb - ka),
                           "content_differs": len(changed),
                           "only_A_sample": sorted(ka - kb)[:sample], "only_B_sample": sorted(kb - ka)[:sample],
                           "content_differs_sample": changed[:sample]}
    for table in TABLES:
        ta = {k: v for k, v in ma.items() if k[0] == table}
        tb = {k: v for k, v in mb.items() if k[0] == table}

        def movable(rows: dict, other_docs: dict, post_type: str) -> int:
            return sum(1 for k in rows if (post_type, k[1]) in other_docs and (INCIDENT, k[2]) in other_docs)

        both = set(ta) & set(tb)
        out["matches"][table] = {
            "A": len(ta), "B": len(tb), "only_A": len(set(ta) - set(tb)), "only_B": len(set(tb) - set(ta)),
            "status_differs": sum(1 for k in both if ta[k]["status"] != tb[k]["status"]),
            "only_A_movable_to_B": movable({k: v for k, v in ta.items() if k not in tb}, db, TABLES[table]),
            "only_B_movable_to_A": movable({k: v for k, v in tb.items() if k not in ta}, da, TABLES[table]),
        }
    return out


def import_matches(path: str, *, apply: bool, only_human: bool) -> dict[str, Any]:
    _, _, matches = _load(path)
    stats = {"inserted": 0, "updated": 0, "kept_local_human": 0, "kept_local_content": 0,
             "skipped_missing_post": 0, "skipped_missing_incident": 0, "skipped_content_only_human": 0}
    with session_scope() as session:
        for (table, pe, ie), r in matches.items():
            if table not in TABLES:
                continue
            if only_human and r["method"] != "manual":
                stats["skipped_content_only_human"] += 1
                continue
            pid = session.execute(text("SELECT id FROM documents WHERE source_type=:t AND external_id=:e AND status='active'"),
                                  {"t": TABLES[table], "e": pe}).scalar()
            iid = session.execute(text("SELECT id FROM documents WHERE source_type=:t AND external_id=:e AND status='active'"),
                                  {"t": INCIDENT, "e": ie}).scalar()
            if not pid:
                stats["skipped_missing_post"] += 1
                continue
            if not iid:
                stats["skipped_missing_incident"] += 1
                continue
            ex = session.execute(text(f"SELECT method, status FROM {table} WHERE post_document_id=:p AND incident_document_id=:i"),
                                 {"p": pid, "i": iid}).first()
            params = {"p": pid, "i": iid, "m": r["method"], "s": r["score"], "r": r["rank"],
                      "sig": json.dumps(r["signals"], ensure_ascii=False), "st": r["status"], "v": r["matcher_version"]}
            if ex is None:
                if apply:
                    session.execute(text(
                        f"INSERT INTO {table} (post_document_id, incident_document_id, method, score, rank, signals, status, matcher_version) "
                        "VALUES (:p,:i,:m,:s,:r,CAST(:sig AS jsonb),:st,:v)"), params)
                stats["inserted"] += 1
            elif ex.method == "manual" or ex.status in ("confirmed", "rejected"):
                stats["kept_local_human"] += 1
            elif r["method"] == "manual":
                if apply:
                    session.execute(text(
                        f"UPDATE {table} SET method=:m, score=:s, rank=:r, signals=CAST(:sig AS jsonb), status=:st, "
                        "matcher_version=:v, updated_at=now() WHERE post_document_id=:p AND incident_document_id=:i"), params)
                stats["updated"] += 1
            else:
                stats["kept_local_content"] += 1
    return {"mode": "apply" if apply else "preview", "only_human": only_human, **stats}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", metavar="FILE")
    g.add_argument("--diff", nargs=2, metavar=("A", "B"))
    g.add_argument("--import", dest="imp", metavar="FILE")
    ap.add_argument("--apply", action="store_true", help="--import에서 실제로 쓴다(없으면 미리보기)")
    ap.add_argument("--only-human", action="store_true")
    args = ap.parse_args(argv)
    if args.export:
        out: Any = export(args.export)
    elif args.diff:
        out = diff(*args.diff)
    else:
        out = import_matches(args.imp, apply=args.apply, only_human=args.only_human)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
