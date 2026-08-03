#!/usr/bin/env python3
"""Render docs/*.md → apps/web/public/docs/*.html (styled, sidebar+TOC).

Single source of truth for every generated (non-hand-authored) doc page in
the web app. Uses the CI-TEC design-doc CSS (apps/web/public/css/docs.css)
so all rendered docs look consistent with docs/CI-TEC_Knowledge_Platform_Design.html.

Run after editing any docs/*.md that has a mapping below:
  .venv/bin/python scripts/render_docs_html.py

Do NOT hand-edit the generated *.html files in apps/web/public/docs/ —
edit the source .md in docs/ and re-run this script.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

try:
    import markdown
except ImportError:
    print("pip install markdown  (or: .venv/bin/pip install markdown)", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parents[1]
DOCS_SRC = ROOT / "docs"
DOCS_OUT = ROOT / "apps" / "web" / "public" / "docs"

# (source md filename, output html filename, nav title, subtitle)
DOCS = [
    ("AI_AGENT_GUIDE.md", "ai-agent-guide.html", "AI Agent Guide", "Claude / MCP / REST 도구 선택 가이드"),
    ("CONFLUENCE_DRAWIO_MCP_GUIDE.md", "confluence-drawio-mcp-guide.html", "Confluence draw.io MCP 가이드", "다이어그램 읽기/쓰기 워크플로 (Claude 전용)"),
    ("DEPLOY.md", "deploy.html", "폐쇄망 배포 가이드", "분리 번들 · 버전 추적 · 변경분만 배포"),
    ("EXTERNAL_API.md", "external-api.html", "외부 연동 API", "citec-wiki-qa 호환 /api/* 레이어"),
    ("IMPLEMENTATION_PLAN.md", "implementation-plan.html", "구현 계획", "PR DAG · 백로그 · API 계약 · 게이트 G0–G6"),
    ("MCP.md", "mcp.html", "MCP 서버", "Claude / Cursor용 kb_search · kb_ask · kb_query"),
    ("OIDC_IDP_SETUP.md", "oidc-idp-setup.html", "OIDC / IdP 연동 가이드", "Keycloak 및 외부 IdP 연동 모드"),
    ("PACKET_ANALYSIS_MCP_GUIDE.md", "packet-analysis-mcp-guide.html", "패킷 분석 MCP 가이드", "failure_bucket 검색·등록 워크플로 (Claude 전용)"),
    ("PHASE2_PILOT_CHECKLIST.md", "phase2-pilot-checklist.html", "Phase 2 파일럿 체크리스트", "G2/G3 사인오프 항목"),
    ("Query_Catalog_100_Analysis.md", "query-catalog-analysis.html", "예상 질문 100 분석", "역할별 질의 커버리지 · 답변 가능성 등급"),
]

TOP_NAV = """<div class="top" id="topNav" data-page="doc"></div>"""


def git_last_updated(path: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%ad", "--date=short", "--", str(path)],
            cwd=ROOT, capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        return out or "-"
    except Exception:
        return "-"


def flatten_toc(tokens, out, max_level=3):
    for tok in tokens:
        if tok["level"] <= max_level:
            out.append(tok)
        flatten_toc(tok.get("children") or [], out, max_level)


def render_one(md_name: str, html_name: str, nav_title: str, subtitle: str) -> None:
    md_path = DOCS_SRC / md_name
    md_text = md_path.read_text(encoding="utf-8")

    # keep a fresh markdown mirror alongside the html (matches implementation-plan pattern)
    (DOCS_OUT / md_name).write_text(md_text, encoding="utf-8")

    md_engine = markdown.Markdown(
        extensions=["tables", "fenced_code", "toc", "sane_lists", "nl2br"],
        extension_configs={"toc": {"permalink": False}},
        output_format="html5",
    )
    body = md_engine.convert(md_text)
    toc_items: list[dict] = []
    flatten_toc(md_engine.toc_tokens, toc_items, max_level=3)

    # checklist styling
    body = body.replace("<li>[x] ", "<li>✅ ").replace("<li>[X] ", "<li>✅ ")
    body = body.replace("<li>[ ] ", "<li>☐ ")
    # scrollable tables (docs.css .table-wrap)
    body = re.sub(r"(<table>.*?</table>)", r'<div class="table-wrap">\1</div>', body, flags=re.S)

    title_match = re.search(r"^#\s+(.+)$", md_text, flags=re.M)
    doc_title = title_match.group(1).strip() if title_match else nav_title
    updated = git_last_updated(md_path)

    nav_html = "\n".join(
        f'      <a href="#{tok["id"]}"{" style=\"padding-left:20px;font-size:12px\"" if tok["level"] == 3 else ""}>{tok["name"]}</a>'
        for tok in toc_items
    )

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{nav_title} — CI-TEC Knowledge</title>
<link rel="stylesheet" href="/css/theme.css" />
<link rel="stylesheet" href="/css/docs.css" />
<script src="/js/nav.js"></script>
</head>
<body>
{TOP_NAV.format(md_name=md_name)}
<div class="page">
  <aside class="sidebar">
    <div class="brand">CI-TEC Knowledge · Docs</div>
    <h1>{doc_title}</h1>
    <nav class="nav">
{nav_html}
    </nav>
  </aside>
  <main class="main">
    <header class="hero">
      <div class="badge-row">
        <span class="badge blue">Reference Doc</span>
      </div>
      <h1>{doc_title}</h1>
      <p class="lead">{subtitle}</p>
      <div class="meta-grid">
        <div class="meta-card"><div class="label">원본</div><div class="value">docs/{md_name}</div></div>
        <div class="meta-card"><div class="label">최종 갱신</div><div class="value">{updated}</div></div>
      </div>
    </header>
    <section>
      <div class="section-card">
{body}
      </div>
    </section>
  </main>
</div>
</body>
</html>
"""
    (DOCS_OUT / html_name).write_text(html, encoding="utf-8")
    print(f"wrote {DOCS_OUT / html_name}")


def main() -> None:
    for md_name, html_name, nav_title, subtitle in DOCS:
        render_one(md_name, html_name, nav_title, subtitle)


if __name__ == "__main__":
    main()
