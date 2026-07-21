#!/usr/bin/env python3
"""Generate pilot domain sign-off evidence pack (engineering + human checklist).

Does not claim domain sign-off is complete — produces a report for human review.

Usage:
  .venv/bin/python scripts/pilot_domain_signoff.py
  .venv/bin/python scripts/pilot_domain_signoff.py --base http://127.0.0.1:8573

Outputs:
  data/reports/pilot_signoff_latest.json
  apps/web/public/docs/pilot-signoff.html
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _get(base: str, path: str, timeout: float = 30.0) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(base.rstrip("/") + path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
        except Exception:
            body = {"error": str(e)}
        return e.code, body
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def _post(base: str, path: str, body: dict, timeout: float = 90.0) -> tuple[int, Any]:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            body_j = json.loads(e.read().decode())
        except Exception:
            body_j = {"error": str(e)}
        return e.code, body_j
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def run_pilot_tech(base: str) -> dict[str, Any]:
    """Re-run the same automated checks as pilot_tech_check (inline)."""
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    code, ops = _get(base, "/v1/ops/status")
    add("ops.status", code == 200 and ops.get("status") == "ok", str(ops.get("status")))
    add("ops.pilot_engineering_ready", bool(ops.get("pilot_engineering_ready")))
    w = (ops.get("checks") or {}).get("worker") or {}
    add("worker.heartbeat", bool(w.get("ok")), str(w.get("age_sec")))

    code, s = _post(base, "/v1/search", {"q": "CITECTS-2502", "top_k": 3, "multi_query": True})
    top = ((s.get("results") or [{}])[0] or {}).get("external_id")
    add("A1 search CITECTS-2502 top1", top == "CITECTS-2502", str(top))

    code, s2 = _post(base, "/v1/search", {"q": "모니모 Redis", "top_k": 5})
    ids = [r.get("external_id") for r in (s2.get("results") or [])]
    add("A3 monimo redis hits", len(ids) > 0, ",".join(str(x) for x in ids[:3]))

    code, r = _post(base, "/v1/query/route", {"q": "지난 주 지원건"})
    add("route list", r.get("intent") == "time_scoped_list", str(r.get("intent")))
    code, r = _post(base, "/v1/query/route", {"q": "2주 Linux 대수"})
    add("route capacity", r.get("intent") == "capacity", str(r.get("intent")))
    code, r = _post(base, "/v1/query/route", {"q": "연도별 지원 건수"})
    add("route analytics", r.get("intent") == "analytics", str(r.get("intent")))

    code, si = _post(base, "/v1/similar-incident", {"symptom": "모니모 Redis 타임아웃", "top_k": 2})
    cases = si.get("cases") or []
    add("C1 SI cases", len(cases) >= 1, str(len(cases)))
    if cases:
        lab = (cases[0].get("applicability") or {}).get("label")
        add("C1 SI has applicability", bool(lab), str(lab))

    code, job = _post(base, "/v1/jobs", {"type": "ping"})
    jid = job.get("id")
    st = job.get("status")
    for _ in range(25):
        if not jid:
            break
        c2, j = _get(base, f"/v1/jobs/{jid}")
        st = j.get("status")
        if st in ("done", "failed"):
            break
        time.sleep(0.4)
    add("job ping done", st == "done", str(st))

    counts = ((ops.get("checks") or {}).get("postgres") or {}).get("counts") or {}
    # ops may nest differently
    if not counts and isinstance(ops.get("checks"), dict):
        counts = (ops["checks"].get("postgres") or {}).get("counts") or {}
    # fallback entities from ops seeds
    seeds = (ops.get("checks") or {}).get("seeds") or {}
    ent = counts.get("entities") or seeds.get("entities") or 0
    lex = counts.get("lexicon_terms") or seeds.get("lexicon_terms") or 0
    add("entities>=1", int(ent or 0) >= 1, str(ent))
    add("lexicon>=1", int(lex or 0) >= 1, str(lex))

    passed = sum(1 for c in checks if c["ok"])
    return {
        "total": len(checks),
        "passed": passed,
        "failed": len(checks) - passed,
        "pass": passed == len(checks),
        "checks": checks,
        "ops": ops if code else ops,
    }


def load_sla_snapshot() -> dict[str, Any] | None:
    path = ROOT / "data" / "reports" / "load_sla_latest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def unit_test_count() -> dict[str, Any]:
    """Best-effort: count tests via docker if available."""
    try:
        r = subprocess.run(
            [
                "docker",
                "compose",
                "exec",
                "-T",
                "api",
                "python",
                "-m",
                "pytest",
                "/app/tests",
                "-q",
                "--collect-only",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=60,
        )
        # last line like "87 tests collected"
        out = (r.stdout or "") + (r.stderr or "")
        n = None
        for line in out.splitlines():
            if "collected" in line:
                parts = line.strip().split()
                for i, p in enumerate(parts):
                    if p.isdigit() and i + 1 < len(parts) and "test" in parts[i + 1]:
                        n = int(p)
        return {"collected": n, "ok": r.returncode == 0, "raw_tail": out.strip().splitlines()[-3:]}
    except Exception as exc:  # noqa: BLE001
        return {"collected": None, "ok": False, "error": str(exc)}


def human_checklist() -> list[dict[str, Any]]:
    """Items that require human domain / ops sign-off (not auto-pass)."""
    return [
        {
            "id": "H1",
            "title": "검색 품질 현장 워크스루 (gold-50 / 실무 질의 10건)",
            "owner": "도메인 전문가",
            "status": "pending_human",
            "evidence": "hit@3 engineering 0.96; re-run with live queries",
        },
        {
            "id": "H2",
            "title": "Fast/Deep QA groundedness 현장 사인 (G2)",
            "owner": "도메인 전문가",
            "status": "pending_human",
            "evidence": "Trust/chat runner exists; formal ≥0.95 gate not signed",
        },
        {
            "id": "H3",
            "title": "유사장애 그룹장 워크스루 (G3 G01–G10)",
            "owner": "그룹장",
            "status": "pending_human",
            "evidence": "SI eval pass_rate=1.0 engineering",
        },
        {
            "id": "H4",
            "title": "공수/집계 숫자 Rules-only 현장 확인 (G4)",
            "owner": "관리자",
            "status": "pending_human",
            "evidence": "capacity DB + unit tests",
        },
        {
            "id": "H5",
            "title": "Insight 승인 플로우 시니어 리뷰 1회",
            "owner": "시니어",
            "status": "pending_human",
            "evidence": "API+UI+async reindex available",
        },
        {
            "id": "H6",
            "title": "SSO: Keycloak/Entra 실서버 연동 (G6)",
            "owner": "인프라",
            "status": "pending_human",
            "evidence": "mock IdP e2e + docs/OIDC_IDP_SETUP.md",
        },
        {
            "id": "H7",
            "title": "파일럿 사용자 온보딩 (1.5) · 50명 스모크 일정",
            "owner": "조직",
            "status": "pending_human",
            "evidence": "load/SLA report pass at c=8",
        },
    ]


def render_html(report: dict[str, Any]) -> str:
    eng = report["engineering"]
    rows = ""
    for c in eng.get("checks") or []:
        badge = "OK" if c["ok"] else "FAIL"
        color = "#047857" if c["ok"] else "#b91c1c"
        rows += (
            f"<tr><td style='color:{color};font-weight:700'>{badge}</td>"
            f"<td>{c['name']}</td><td><code>{c.get('detail','')}</code></td></tr>\n"
        )
    hrows = ""
    for h in report.get("human_checklist") or []:
        hrows += (
            f"<tr><td>{h['id']}</td><td>{h['title']}</td><td>{h['owner']}</td>"
            f"<td><em>{h['status']}</em></td><td class='meta'>{h['evidence']}</td></tr>\n"
        )
    sla = report.get("load_sla") or {}
    gates = (sla.get("gates") or {}) if isinstance(sla, dict) else {}
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8"/>
<title>Pilot domain sign-off — CI-TEC KB</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:960px;margin:24px auto;padding:0 16px;color:#0f172a}}
 table{{border-collapse:collapse;width:100%;margin:12px 0}}
 th,td{{border:1px solid #e2e8f0;padding:8px;text-align:left;font-size:14px}}
 th{{background:#f8fafc}}
 .ok{{color:#047857;font-weight:700}} .bad{{color:#b91c1c;font-weight:700}}
 .meta{{color:#64748b;font-size:12px}} .card{{border:1px solid #e2e8f0;border-radius:12px;padding:16px;margin:12px 0}}
 h1{{font-size:1.4rem}} code{{background:#f1f5f9;padding:1px 4px;border-radius:4px}}
</style></head><body>
<h1>파일럿 도메인 사인 증거 팩</h1>
<p class="meta">generated {report.get('generated_at')} · engineering_pass=
<span class="{'ok' if eng.get('pass') else 'bad'}">{eng.get('pass')}</span>
· human items remain <strong>pending_human</strong></p>

<div class="card">
<strong>요약</strong>
<ul>
<li>Engineering automated: {eng.get('passed')}/{eng.get('total')}</li>
<li>Unit tests collected: {(report.get('unit_tests') or {}).get('collected')}</li>
<li>Load/SLA pass: {(sla or {}).get('pass')}</li>
<li>Repo: <code>{report.get('repo')}</code></li>
</ul>
<p class="meta">이 문서는 <strong>도메인 사인을 대체하지 않습니다</strong>. H1–H7을 담당자가 체크한 뒤 서명하세요.</p>
</div>

<div class="card">
<strong>Engineering checks (auto)</strong>
<table><thead><tr><th>결과</th><th>항목</th><th>detail</th></tr></thead><tbody>
{rows}
</tbody></table>
</div>

<div class="card">
<strong>Human sign-off checklist</strong>
<table><thead><tr><th>ID</th><th>항목</th><th>Owner</th><th>Status</th><th>Evidence</th></tr></thead><tbody>
{hrows}
</tbody></table>
<p>서명: _______________ 일자: _______________ 역할: _______________</p>
</div>

<div class="card">
<strong>Load/SLA snapshot</strong>
<pre class="meta">{json.dumps({k: sla.get(k) for k in ('pass','gates','concurrency','generated_at') if isinstance(sla, dict)}, ensure_ascii=False, indent=2) if sla else 'no load_sla_latest.json'}</pre>
<p class="meta">gates: {json.dumps(gates, ensure_ascii=False)}</p>
</div>

<p class="meta"><a href="/docs/implementation-plan.html">구현 계획</a> ·
<a href="/docs/OIDC_IDP_SETUP.md">OIDC 가이드</a> ·
<a href="/admin.html">Admin</a> ·
<a href="/">홈</a></p>
</body></html>
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="http://127.0.0.1:8573")
    p.add_argument(
        "--out-dir",
        default=str(ROOT / "data" / "reports"),
    )
    args = p.parse_args()

    eng = run_pilot_tech(args.base)
    sla = load_sla_snapshot()
    units = unit_test_count()
    code, auth = _get(args.base, "/v1/auth/status")
    code_h, health = _get(args.base, "/v1/health")

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "repo": "https://github.com/pjhwa/citec-kb",
        "engineering": eng,
        "load_sla": sla,
        "unit_tests": units,
        "auth": auth if code == 200 else {"error": auth},
        "health": health if code_h == 200 else {"error": health},
        "human_checklist": human_checklist(),
        "domain_signoff_complete": False,
        "note": "Engineering auto-pass does not equal domain sign-off. Complete H1–H7 with humans.",
    }
    report["engineering_ready"] = bool(eng.get("pass"))
    report["ready_for_human_review"] = bool(eng.get("pass"))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"pilot_signoff_{stamp}.json"
    latest = out_dir / "pilot_signoff_latest.json"
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    json_path.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")

    html = render_html(report)
    html_path = ROOT / "apps" / "web" / "public" / "docs" / "pilot-signoff.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html, encoding="utf-8")

    print(payload)
    print(f"\nwrote {json_path}")
    print(f"wrote {html_path}")
    # exit 0 if engineering auto checks pass (human still pending)
    raise SystemExit(0 if eng.get("pass") else 1)


if __name__ == "__main__":
    main()
