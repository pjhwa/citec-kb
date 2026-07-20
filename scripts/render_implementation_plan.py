#!/usr/bin/env python3
"""Render docs/IMPLEMENTATION_PLAN.md → apps/web/public/docs/implementation-plan.html

Run after every plan update:
  .venv/bin/python scripts/render_implementation_plan.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import markdown
except ImportError:
    print("pip install markdown  (or: .venv/bin/pip install markdown)", file=sys.stderr)
    raise

ROOT = Path(__file__).resolve().parents[1]
MD_PATH = ROOT / "docs" / "IMPLEMENTATION_PLAN.md"
OUT_PATH = ROOT / "apps" / "web" / "public" / "docs" / "implementation-plan.html"
# keep markdown mirror in public docs
MD_PUBLIC = ROOT / "apps" / "web" / "public" / "docs" / "IMPLEMENTATION_PLAN.md"


def main() -> None:
    md_text = MD_PATH.read_text(encoding="utf-8")
    MD_PUBLIC.write_text(md_text, encoding="utf-8")

    m = re.search(r"\|\s*문서 버전\s*\|\s*\*\*([^*]+)\*\*", md_text)
    version = m.group(1).strip() if m else "?"
    m2 = re.search(r"\|\s*갱신\s*\|\s*\*\*([^*]+)\*\*", md_text)
    updated = m2.group(1).strip() if m2 else ""
    phase = ""
    for line in md_text.splitlines():
        if "진행 페이즈" in line and "|" in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                phase = parts[-1]
            break

    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"],
        output_format="html5",
    )
    body = body.replace("<li>[x] ", '<li class="check">✅ ')
    body = body.replace("<li>[X] ", '<li class="check">✅ ')
    body = body.replace("<li>[ ] ", '<li class="check">☐ ')
    body = re.sub(
        r"(<table>.*?</table>)",
        r'<div class="table-wrap">\1</div>',
        body,
        flags=re.S,
    )

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>구현 계획 {version} — CI-TEC Knowledge</title>
<style>
  :root {{ --bg:#f6f8fb; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; --ok:#166534; --okbg:#dcfce7; }}
  body {{ margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); line-height:1.65; }}
  .top {{ background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; position:sticky; top:0; z-index:10; }}
  .top a {{ color:var(--primary); margin-right:14px; text-decoration:none; font-size:14px; font-weight:600; }}
  .banner {{ background:var(--okbg); color:var(--ok); border:1px solid #86efac; border-radius:10px; padding:12px 16px; margin:0 0 20px; font-size:14px; }}
  .wrap {{ max-width:960px; margin:0 auto; padding:28px 20px 60px; }}
  h1 {{ font-size:1.7rem; }} h2 {{ font-size:1.3rem; margin-top:1.6em; color:#1e3a8a; border-bottom:1px solid var(--border); padding-bottom:6px; }}
  h3 {{ font-size:1.1rem; margin-top:1.2em; }} h4 {{ font-size:1rem; margin-top:1em; color:#334155; }}
  code {{ background:#f1f5f9; padding:1px 5px; border-radius:4px; font-size:.9em; }}
  pre {{ background:#0f172a; color:#e2e8f0; padding:14px; border-radius:10px; overflow:auto; font-size:13px; }}
  pre code {{ background:none; color:inherit; padding:0; }}
  .table-wrap {{ overflow:auto; border:1px solid var(--border); border-radius:10px; margin:12px 0; background:#fff; }}
  table {{ border-collapse:collapse; width:100%; font-size:13.5px; }}
  th, td {{ border-bottom:1px solid var(--border); padding:8px 10px; text-align:left; vertical-align:top; }}
  th {{ background:#f1f5f9; }}
  li {{ margin:4px 0; margin-left:1.2em; }}
  li.check {{ list-style:none; margin-left:0.2em; }}
  p {{ color:#334155; }}
  blockquote {{ border-left:4px solid var(--primary); margin:12px 0; padding:4px 14px; background:#eff6ff; color:#1e3a8a; }}
  .meta {{ color:var(--muted); font-size:13px; margin-bottom:18px; }}
</style>
</head>
<body>
<div class="top">
  <a href="/">홈</a>
  <a href="/docs/">문서 목록</a>
  <a href="/docs/design.html">시스템 설계서</a>
  <a href="/search.html">검색</a>
  <a href="/docs/IMPLEMENTATION_PLAN.md">Markdown 원본</a>
</div>
<div class="wrap">
  <div class="banner"><strong>현재 상태:</strong> {phase or 'see markdown'} · 문서 {version}</div>
  <p class="meta">문서 버전 {version} · 레포 citec-kb · 원본 <code>docs/IMPLEMENTATION_PLAN.md</code> 렌더 · 갱신 {updated}</p>
{body}
</div>
</body>
</html>
"""
    OUT_PATH.write_text(html, encoding="utf-8")
    print(f"wrote {OUT_PATH} (version {version})")


if __name__ == "__main__":
    main()
