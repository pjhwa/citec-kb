#!/usr/bin/env python3
"""citec-kb A/B/C 비교 테스트 러너 (표준 라이브러리만 사용, Windows/Linux/macOS 공용).

  python run_ab.py catalog                       # cases.json -> CASES.md
  python run_ab.py check   --config arms.json    # 환경 점검 (claude, MCP 설정, 스킬 격리)
  python run_ab.py oracle  --config arms.json [--out oracle_pre]   # 정답+환경 지문 재조회 (run 직전/직후 2회 권장)
  python run_ab.py oracle-diff --pre oracle_pre --post oracle_post  # 실행 중 정답이 변했는지 확인
  python run_ab.py run     --config arms.json [--arms A,B,C] [--cases SW1,GP*] [--repeat 1] [--dry-run]

각 팔은 `claude -p`를 독립 프로세스로 실행한다. --strict-mcp-config 로 MCP를 팔별로 고정하고,
--tools "" 로 내장 도구(Bash/Read 등)를 꺼서 MCP만으로 답하게 한다.
"""
import argparse, csv, fnmatch, json, os, random, re, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def select_cases(cases, spec):
    if not spec or spec == "all":
        return cases
    pats = [s.strip() for s in spec.split(",") if s.strip()]
    return [c for c in cases if any(fnmatch.fnmatch(c["id"], p) or c["category"] == p or c["kind"] == p for p in pats)]


# ---------------------------------------------------------------- catalog
def cmd_catalog(_a):
    d = load_json(HERE / "cases.json")
    m, cs = d["meta"], d["cases"]
    out = ["# 사례 카탈로그", "", f"정답 출처: {m['truth_source']}", "", "## 구분(kind)"]
    out += [f"- **{k}**: {v}" for k, v in m["kinds"].items()]
    for cat, name in m["categories"].items():
        group = [c for c in cs if c["category"] == cat]
        if not group:
            continue
        out += ["", f"## {cat}. {name}", "", "| ID | 구분 | 제목 | 프롬프트 | 정답(KB 기준) |", "|---|---|---|---|---|"]
        for c in group:
            esc = lambda s: s.replace("|", "\\|").replace("\n", " ")
            out.append(f"| {c['id']} | {c['kind']} | {esc(c['title'])} | {esc(c['prompt'])} | {esc(c['truth'])} |")
    (HERE / "CASES.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"CASES.md 작성 ({len(cs)}건)")


# ---------------------------------------------------------------- command build
def claude_bin(cfg):
    b = cfg.get("claude_bin", "claude")
    p = shutil.which(b)
    if not p:
        sys.exit(f"claude 실행 파일을 찾을 수 없음: {b}")
    return p


def resolve(base, p):
    if p is None:
        return None
    q = Path(p)
    return q if q.is_absolute() else (base / q)


def prepare_arm(cfg_dir, name, arm):
    """팔별 작업 디렉터리를 만들고 스킬을 복사(C) 또는 제거(A/B)한다."""
    cwd = resolve(cfg_dir, arm["cwd"])
    skills_root = cwd / ".claude" / "skills"
    if skills_root.exists():
        shutil.rmtree(skills_root)
    cwd.mkdir(parents=True, exist_ok=True)
    if arm.get("skill_src"):
        src = resolve(cfg_dir, arm["skill_src"])
        if not src.exists():
            sys.exit(f"[{name}] skill_src 없음: {src}")
        dst = skills_root / arm.get("skill_name", src.name)
        shutil.copytree(src, dst)
    return cwd


def build_cmd(cfg, cfg_dir, arm, system_prompt):
    cmd = [claude_bin(cfg), "-p", "--output-format", "stream-json", "--verbose",
           "--strict-mcp-config", "--mcp-config", str(resolve(cfg_dir, arm["mcp_config"])),
           "--setting-sources", "project,local", "--tools", "",
           "--no-session-persistence", "--append-system-prompt", system_prompt]
    if arm.get("allowed_tools"):
        cmd += ["--allowedTools", ",".join(arm["allowed_tools"])]
    if arm.get("disallowed_tools"):
        cmd += ["--disallowedTools", ",".join(arm["disallowed_tools"])]
    if cfg.get("model"):
        cmd += ["--model", cfg["model"]]
    return cmd


# ---------------------------------------------------------------- stream parsing
def parse_stream(text):
    info = {"init": {}, "tool_calls": [], "result": None, "answer": "", "mcp_status": {}}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            info["init"] = ev
            for s in ev.get("mcp_servers", []) or []:
                info["mcp_status"][s.get("name")] = s.get("status")
        elif t == "assistant":
            for blk in (ev.get("message", {}).get("content") or []):
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    info["tool_calls"].append(blk.get("name", "?"))
        elif t == "result":
            info["result"] = ev
            info["answer"] = ev.get("result") or ""
    return info


def init_report(info):
    ini = info["init"]
    skills = list(ini.get("skills") or []) + [s for s in (ini.get("slash_commands") or [])]
    return {
        "mcp_servers": info["mcp_status"],
        "skills_visible": sorted(set(skills)),
        "tools_n": len(ini.get("tools") or []),
    }


def auto_score(must, answer):
    if not must:
        return None
    hits = sum(1 for pat in must if re.search(pat, answer or "", re.S))
    return round(hits / len(must), 3)


def check_isolation(name, arm, rep):
    warns = []
    skill = arm.get("skill_name") or (Path(arm["skill_src"]).name if arm.get("skill_src") else None)
    vis = rep["skills_visible"]
    if arm.get("expect_skill"):
        if skill and not any(skill in s for s in vis):
            warns.append(f"[{name}] 스킬 '{skill}'이 init에 안 보임")
    else:
        if any("citec" in s.lower() or "workbench" in s.lower() for s in vis):
            warns.append(f"[{name}] 스킬이 새어 들어옴: {[s for s in vis if 'citec' in s.lower() or 'workbench' in s.lower()]}")
    for prefix in arm.get("expect_mcp", []):
        if not any(prefix in k and v == "connected" for k, v in rep["mcp_servers"].items()):
            warns.append(f"[{name}] MCP '{prefix}' 연결 안 됨: {rep['mcp_servers']}")
    for prefix in arm.get("forbid_mcp", []):
        if any(prefix in k for k in rep["mcp_servers"]):
            warns.append(f"[{name}] 금지 MCP '{prefix}'가 로드됨")
    return warns


def run_claude(cmd, prompt, cwd, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           cwd=str(cwd), timeout=timeout)
        return p.stdout, p.stderr, time.time() - t0, p.returncode
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return out, f"TIMEOUT {timeout}s", time.time() - t0, -1


# ---------------------------------------------------------------- commands
def cmd_check(a):
    cfg_path = Path(a.config).resolve()
    cfg, cfg_dir = load_json(cfg_path), cfg_path.parent
    print("claude:", claude_bin(cfg))
    bad = 0
    for name, arm in cfg["arms"].items():
        cwd = prepare_arm(cfg_dir, name, arm)
        cmd = build_cmd(cfg, cfg_dir, arm, cfg["common_system_prompt"])
        out, err, sec, rc = run_claude(cmd, "한 단어로 'ok'라고만 답해.", cwd, cfg.get("timeout_sec", 300))
        info = parse_stream(out)
        rep = init_report(info)
        warns = check_isolation(name, arm, rep)
        print(f"\n[{name}] rc={rc} {sec:.1f}s mcp={rep['mcp_servers']} skills={rep['skills_visible'][:8]}")
        if rc != 0 and not info["init"]:
            print("  stderr:", (err or "")[:300]); bad += 1
        for w in warns:
            print("  WARN", w); bad += 1
    print("\n점검 결과:", "문제 없음" if not bad else f"경고 {bad}건 — 위 항목을 고친 뒤 run")


def oracle_prompt(c):
    o = c["oracle"]
    return (f"MCP 도구 `{o['tool']}`(서버 접두사 mcp__wiki-mcp__ 가 붙은 이름)을 다음 인자로 정확히 한 번만 호출하고, "
            f"반환된 결과 원문을 가공·요약 없이 그대로 출력해라. 인자(JSON): {json.dumps(o['args'], ensure_ascii=False)}")


def cmd_oracle(a):
    cfg_path = Path(a.config).resolve()
    cfg, cfg_dir = load_json(cfg_path), cfg_path.parent
    arm = cfg["oracle_arm"]
    cwd = prepare_arm(cfg_dir, "oracle", arm)
    cmd = build_cmd(cfg, cfg_dir, arm, "요청받은 도구를 그대로 호출하고 결과 원문만 출력한다.")
    outdir = Path(a.out or HERE / "oracle")
    outdir.mkdir(parents=True, exist_ok=True)
    cases = select_cases(load_json(HERE / "cases.json")["cases"], a.cases)
    for c in cases:
        if not c.get("oracle"):
            continue
        out, err, sec, rc = run_claude(cmd, oracle_prompt(c), cwd, cfg.get("timeout_sec", 300))
        info = parse_stream(out)
        (outdir / f"{c['id']}.txt").write_text(info["answer"] or f"(실패 rc={rc}) {err[:500]}", encoding="utf-8")
        print(f"{c['id']}: {len(info['answer'])}자 {sec:.0f}s")
    # 환경 지문: 어느 서버(개발/운영)인지, 코퍼스 규모, 맵·그래프의 최신 시각
    for pr in load_json(HERE / "cases.json").get("env_probes", []):
        c = {"id": "_env_" + pr["id"], "oracle": pr}
        out, err, sec, rc = run_claude(cmd, oracle_prompt(c), cwd, cfg.get("timeout_sec", 300))
        info = parse_stream(out)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        (outdir / f"{c['id']}.txt").write_text(f"# captured_at={stamp}\n" + (info["answer"] or f"(실패 rc={rc}) {err[:300]}"), encoding="utf-8")
        print(f"{c['id']}: {len(info['answer'])}자")
    print("oracle 저장:", outdir, "— 정답(truth)과 다르면 cases.json을 갱신하거나 채점 때 oracle 값을 우선한다.")


def cmd_oracle_diff(a):
    pre, post = Path(a.pre), Path(a.post)
    norm = lambda t: re.sub(r"\s+", " ", re.sub(r"^# captured_at=.*$", "", t, flags=re.M)).strip()
    changed, same = [], 0
    for f in sorted(pre.glob("*.txt")):
        g = post / f.name
        if not g.exists():
            changed.append((f.stem, "post 없음")); continue
        if norm(f.read_text(encoding="utf-8")) == norm(g.read_text(encoding="utf-8")):
            same += 1
        else:
            changed.append((f.stem, "내용 변경"))
    print(f"동일 {same}건, 변경 {len(changed)}건")
    for k, why in changed:
        print(" -", k, why)
    if changed:
        print("변경된 문항은 실행 중 정답이 움직인 것이다. 채점 시 pre/post 중 응답에 가까운 값을 허용하거나 해당 문항을 제외한다.")


def cmd_run(a):
    cfg_path = Path(a.config).resolve()
    cfg, cfg_dir = load_json(cfg_path), cfg_path.parent
    arm_names = [x.strip() for x in a.arms.split(",")]
    cases = select_cases(load_json(HERE / "cases.json")["cases"], a.cases)
    ts = time.strftime("%Y%m%d-%H%M%S")
    outdir = Path(a.out or HERE / "results" / ts)
    rows, blind, key = [], [], {}
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    odir = Path(a.oracle_dir) if a.oracle_dir else HERE / "oracle"
    rng = random.Random(a.seed)
    warned = set()

    prepared = {}
    for n in arm_names:
        prepared[n] = prepare_arm(cfg_dir, n, cfg["arms"][n])

    for c in cases:
        labels = {}
        order = arm_names[:]
        rng.shuffle(order)
        for i, n in enumerate(order):
            labels[n] = f"R{i + 1}"
        answers = {}
        for n in arm_names:
            arm = cfg["arms"][n]
            cmd = build_cmd(cfg, cfg_dir, arm, cfg["common_system_prompt"])
            for r in range(a.repeat):
                if a.dry_run:
                    print(f"[dry] {c['id']} {n}: {' '.join(cmd[:8])} ... < stdin: {c['prompt'][:50]}")
                    continue
                out, err, sec, rc = run_claude(cmd, c["prompt"], prepared[n], cfg.get("timeout_sec", 300))
                raw = outdir / "raw" / n
                raw.mkdir(parents=True, exist_ok=True)
                (raw / f"{c['id']}_{r}.jsonl").write_text(out, encoding="utf-8")
                info = parse_stream(out)
                rep = init_report(info)
                for w in check_isolation(n, arm, rep):
                    if w not in warned:
                        warned.add(w); print("WARN", w)
                res = info["result"] or {}
                row = {
                    "case": c["id"], "kind": c["kind"], "arm": n, "rep": r,
                    "auto_score": (None if (c.get("stability") == "drifting" and not a.trust_must) else auto_score(c.get("must", []), info["answer"])),
                    "tool_calls": len(info["tool_calls"]), "distinct_tools": len(set(info["tool_calls"])),
                    "turns": res.get("num_turns"), "duration_s": round(sec, 1),
                    "cost_usd": res.get("total_cost_usd"), "is_error": bool(res.get("is_error")) or rc != 0,
                    "answer_chars": len(info["answer"]),
                }
                rows.append(row)
                if r == 0:
                    answers[n] = (info["answer"] or f"(응답 없음 rc={rc} {err[:200]})", info["tool_calls"])
                print(f"{c['id']:4} {n} auto={row['auto_score']} calls={row['tool_calls']} {sec:.0f}s")
        if answers:
            key[c["id"]] = labels
            blind.append((c, labels, answers))

    if a.dry_run:
        return
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "summary.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    (outdir / "meta.json").write_text(json.dumps({"started": started, "finished": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "arms": arm_names, "cases": [c["id"] for c in cases], "repeat": a.repeat, "oracle_dir": str(odir),
        "trust_must": a.trust_must}, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 블라인드 채점지", "", "응답의 라벨(R1..)만 보고 채점한다. 팔 매핑은 key.json — 채점 끝나기 전에 열지 말 것.",
          "채점: 정확도 0~2(일치 2/부분 1/불일치 0) + 정직성 0/1(한계를 밝혔으면 1, 단정했으면 0)", ""]
    grade = [["case", "label", "accuracy_0_2", "honesty_0_1", "notes"]]
    for c, labels, answers in blind:
        drift = c.get("stability") == "drifting"
        md += [f"## {c['id']} {c['title']}  ({c['kind']}, {c.get('stability', '?')})", "", f"**질문**: {c['prompt']}", "",
               f"**정답({'개발 서버 참고값 — oracle 우선' if drift else 'KB 기준'})**: {c['truth']}", ""]
        if c.get("dev_note"):
            md += [f"**개발 서버 한계**: {c['dev_note']}", ""]
        ot = odir / f"{c['id']}.txt"
        if ot.exists():
            md += ["**oracle(실행 시점 KB 원문)**:", "```", ot.read_text(encoding="utf-8")[:1500], "```", ""]
        md += [f"**채점 메모**: {c.get('notes') or '-'}", ""]
        for n, lab in sorted(labels.items(), key=lambda x: x[1]):
            if n not in answers:
                continue
            ans, calls = answers[n]
            md += [f"### {lab}", "", ans.strip(), "", f"_도구 호출 {len(calls)}회: {', '.join(sorted(set(calls)))}_", ""]
            grade.append([c["id"], lab, "", "", ""])
    (outdir / "blind_answers.md").write_text("\n".join(md), encoding="utf-8")
    with open(outdir / "grading_sheet.csv", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(grade)

    print("\n=== 팔별 요약(자동 지표) ===")
    for n in arm_names:
        rs = [r for r in rows if r["arm"] == n]
        sc = [r["auto_score"] for r in rs if r["auto_score"] is not None]
        print(f"{n}: 문항 {len(rs)}, 자동점수 평균 {sum(sc) / len(sc):.2f}" if sc else f"{n}: 문항 {len(rs)}",
              f"| 평균 호출 {sum(r['tool_calls'] for r in rs) / len(rs):.1f}",
              f"| 평균 {sum(r['duration_s'] for r in rs) / len(rs):.0f}s",
              f"| 오류 {sum(r['is_error'] for r in rs)}")
    print("산출물:", outdir)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalog").set_defaults(fn=cmd_catalog)
    for name, fn in (("check", cmd_check), ("oracle", cmd_oracle), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("--config", default="arms.json")
        p.set_defaults(fn=fn)
        if name in ("oracle", "run"):
            p.add_argument("--cases", default="all", help="ID/카테고리/kind 목록, glob 가능 (예: SW*,win)")
            p.add_argument("--out", default=None)
        if name == "run":
            p.add_argument("--arms", default="A,B,C")
            p.add_argument("--repeat", type=int, default=1)
            p.add_argument("--seed", type=int, default=7)
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--oracle-dir", default=None, help="채점지에 붙일 oracle 폴더 (기본 ./oracle)")
            p.add_argument("--trust-must", action="store_true", help="drifting 문항도 개발 서버 정규식으로 자동 채점(권장 안 함)")
    d = sub.add_parser("oracle-diff")
    d.add_argument("--pre", required=True)
    d.add_argument("--post", required=True)
    d.set_defaults(fn=cmd_oracle_diff)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
