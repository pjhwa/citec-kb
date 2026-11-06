#!/usr/bin/env python3
"""예전 형식의 자동 제안(스캔이 만든 'proposed')을 찾아 보류(retire)한다. 기본은 조회만(dry-run).

대상 (모두 created_by_kind=server_job, status=proposed, 판정 이력 없음):
  1. 도메인 전체 단위 공백 제안     source_key = gap:<도메인>:<fb도메인>          (새 형식은 …:<고객사>가 붙음)
  2. 지지 사례 0건 버킷 확정 제안   source_key = fb:<버킷 id> 이고 버킷 support_count = 0 (또는 버킷이 없음)
  3. 예전 방식 공백 제안            source_key = gap:… 또는 gap2:<도메인>:<fb도메인>:<고객사>  (원인 미확정 장애가 섞이고
                                    초기 보고 문구를 원인으로 보여 줌. 새 스캔은 gap3:… 키로 다시 제안)

확정(confirmed)된 결정, 사람이 만든 결정, 판정이 있는 결정은 건드리지 않는다. 보류는 복구할 수 있다
(카드의 '복구' 또는 POST /v1/decisions/{id}/restore). 보류 후 다음 스캔이 새 형식으로 다시 올린다.

  python3 scripts/retire_legacy_decision_proposals.py                 # 대상만 보여 줌
  python3 scripts/retire_legacy_decision_proposals.py --apply         # 실제로 보류

환경: API_BASE(기본 http://localhost:8573), API_TOKEN(인증을 켠 경우 senior/admin 토큰, Bearer).
종료 코드: 0 정상, 1 호출 오류가 있었음.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def call(base: str, token: str, method: str, path: str):
    req = urllib.request.Request(base.rstrip("/") + path, method=method,
                                 headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "null")
        except ValueError:
            return exc.code, None


def classify(item: dict, bucket_support) -> str | None:
    """대상이면 사유를, 아니면 None. bucket_support: fb 제안의 버킷 support_count(없으면 None)."""
    if item.get("status") != "proposed" or item.get("created_by_kind") != "server_job" or item.get("current_verdict"):
        return None
    key = item.get("source_key") or ""
    if key.startswith("gap:") and len(key.split(":")) == 3:
        return "도메인 전체 단위 공백 제안(예전 형식)"
    if key.startswith(("gap:", "gap2:")) and len(key.split(":")) == 4:
        return "예전 방식 공백 제안(초기 보고 원인·원인 미확정 장애 포함 — 스캔이 gap3: 키로 다시 제안)"
    if key.startswith("fb:"):
        if bucket_support is None:
            return "버킷을 찾을 수 없음"
        if bucket_support == 0:
            return "지지 사례 0건 버킷"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="실제로 보류한다(기본: 조회만)")
    ap.add_argument("--base", default=os.environ.get("API_BASE", "http://localhost:8573"))
    a = ap.parse_args()
    token = os.environ.get("API_TOKEN", "")

    items, offset = [], 0
    while True:
        st, d = call(a.base, token, "GET", f"/v1/decisions?status=proposed&sort=recent&limit=200&offset={offset}")
        if st != 200:
            print(f"목록 조회 실패 HTTP {st}", file=sys.stderr)
            return 1
        items += d["items"]
        offset += 200
        if offset >= d["total"]:
            break

    targets, errors = [], 0
    for it in items:
        support = None
        if (it.get("source_key") or "").startswith("fb:"):
            bid = it["source_key"][3:]
            st, b = call(a.base, token, "GET", f"/v1/failure-buckets/{bid}")
            if st == 200 and b.get("status") == "active":
                support = int(b.get("support_count") or 0)
            elif st not in (200, 404):
                print(f"! 버킷 조회 실패 {bid[:8]} HTTP {st} — 건너뜀", file=sys.stderr)
                errors += 1
                continue
        why = classify(it, support if support is not None else None)
        if why:
            targets.append((it, why))

    print(f"제안 {len(items)}건 중 정리 대상 {len(targets)}건" + ("" if a.apply else " (조회만 — 보류하려면 --apply)"))
    for it, why in targets:
        print(f"  - {it['id'][:8]}  {it['source_key'][:60]:<60}  {why}")
    if not a.apply:
        return 1 if errors else 0

    done = 0
    for it, _ in targets:
        st, _d = call(a.base, token, "POST", f"/v1/decisions/{it['id']}/retire")
        if st == 200:
            done += 1
        else:
            print(f"! 보류 실패 {it['id'][:8]} HTTP {st}", file=sys.stderr)
            errors += 1
    print(f"보류 완료 {done}/{len(targets)}건")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
