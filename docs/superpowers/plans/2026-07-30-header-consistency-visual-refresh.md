# 헤더 일관성 버그 수정 · 시각 언어 차용 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **If dispatching subagents in an isolated git worktree:** every dispatch prompt MUST open with an explicit worktree-path confirmation step (run `git branch --show-current` in the worktree and confirm the expected branch name before touching any file) and MUST NOT `cd` outside that worktree. A prior run of a similar plan had 4 of 12 mechanical per-page tasks land commits directly on `main` because the dispatch prompts didn't pin the working directory explicitly — do not repeat that mistake.

**Goal:** Fix the two real bugs causing the top nav to look different on every page (`<details open>` forced on 6 of 12 pages; 12 independently-drifting copies of `.top`/`.top a` CSS), extend the same shared header to the `/docs/` hub, and apply a header/color/typography refresh borrowed from `citec-wiki-qa` (dark navy header, Samsung SDS blue `#1428A0`, vendored Pretendard + JetBrains Mono fonts — no CDN).

**Architecture:** A new static stylesheet `apps/web/public/css/theme.css` becomes the single owner of all header/nav-chrome CSS and `@font-face` declarations; `nav.js` is stripped down to pure DOM-structure rendering (its JS-injected `<style>` and the `open`-attribute bug are removed). All 12 app pages plus the `/docs/` hub link `theme.css` and delete their local `.top`/`.top a` rules so they can never drift again. Font files are downloaded once during implementation and committed as static assets — production never makes a CDN request.

**Tech Stack:** Vanilla JS/CSS, static HTML served by nginx, Python (`scripts/render_docs_html.py`, uses `markdown` package) for the `/docs/` hub's 9 generated pages. No test runner — verification is via `node --check`, `grep`, and a live `curl`/docker smoke test at the end.

---

## File Structure

- **Modify** `apps/web/public/js/nav.js` — remove `<details open>` bug, remove `injectStyle()` (styling moves to `theme.css`).
- **Create** `apps/web/public/fonts/` — 8 vendored `.woff2` files (Pretendard × 4 weights, JetBrains Mono × 4 weights).
- **Create** `apps/web/public/css/theme.css` — `@font-face` rules + all header/dropdown/admin-divider CSS.
- **Modify** (one task each, 12 files): `index.html`, `search.html`, `chat.html`, `si.html`, `tickets.html`, `analytics.html`, `capacity.html`, `bundles.html`, `insights.html`, `admin.html`, `doc.html`, `login.html` — link `theme.css`, delete local `.top`/`.top a` rules, change `--primary` to `#1428A0`, prepend `'Pretendard'` to `body`'s `font-family`.
- **Modify** `scripts/render_docs_html.py` — `TOP_NAV` template becomes the shared `#topNav` placeholder + `theme.css`/`nav.js` includes; regenerate the 9 output pages.
- **Modify** `apps/web/public/docs/index.html` — hand-apply the same pattern (hand-authored, not script-generated).
- **Modify** `apps/web/public/docs/design.html` — add `theme.css` link only (no structural change, per spec's explicit non-goal).
- **Modify** `apps/web/public/css/docs.css` — update `--primary` to `#1428A0` for consistency (docs hub already references it throughout).
- **Not touched**: `apps/web/public/docs/pilot-signoff.html` — a standalone generated evidence-pack document (via `scripts/pilot_domain_signoff.py`, not `render_docs_html.py`) with no `.top` header of any kind. Same rationale as `design.html`: out of scope, not part of the reported inconsistency (it never had a nav to be inconsistent with).

---

### Task 1: Fix `nav.js` — remove the `open` bug and the JS-injected stylesheet

**Files:**
- Modify: `apps/web/public/js/nav.js`

- [ ] **Step 1: Update the file header comment**

Old (`nav.js:1-5`):
```javascript
/**
 * Shared top navigation, rendered into <div class="top" id="topNav" data-page="...">.
 * Reads --primary/--border/--muted from the host page's own :root so no
 * page-specific CSS is required.
 */
```

New:
```javascript
/**
 * Shared top navigation, rendered into <div class="top" id="topNav" data-page="...">.
 * Visual styling (colors, dropdown, admin divider, fonts) lives in
 * /css/theme.css — this file only builds the DOM structure.
 */
```

- [ ] **Step 2: Delete `injectStyle()` entirely**

Old (`nav.js:24-39`):
```javascript
  function injectStyle() {
    if (document.getElementById("nav-style")) return;
    var style = document.createElement("style");
    style.id = "nav-style";
    style.textContent = [
      ".top .nav-dd { position: relative; }",
      ".top .nav-dd summary { cursor: pointer; color: var(--primary); font-weight: 600; font-size: 14px; list-style: none; }",
      ".top .nav-dd summary::-webkit-details-marker { display: none; }",
      ".top .nav-dd-menu { position: absolute; top: 100%; left: 0; margin-top: 6px; background: #fff; border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 10px 28px rgba(15,23,42,0.12); padding: 6px; display: flex; flex-direction: column; min-width: 140px; z-index: 20; }",
      ".top .nav-dd-menu a { padding: 6px 10px; border-radius: 6px; white-space: nowrap; }",
      ".top .nav-dd-menu a:hover { background: #f1f5f9; }",
      ".top a.nav-current, .top summary.nav-current { text-decoration: underline; }",
      ".top .nav-admin { margin-left: auto; padding-left: 14px; border-left: 1px solid var(--border); }",
    ].join("\n");
    document.head.appendChild(style);
  }

  function linkHtml(id, label, href, current) {
```

New:
```javascript
  function linkHtml(id, label, href, current) {
```

- [ ] **Step 3: Remove the `open` attribute from the dropdown `<details>`**

Old (`nav.js:53`):
```javascript
    html += '<details class="nav-dd"' + (searchCurrent ? " open" : "") + ">";
```

New:
```javascript
    html += '<details class="nav-dd">';
```

(Leave the `searchCurrent` variable and the `<summary>` line's `nav-current`/`aria-current` logic untouched — only the forced `open` attribute is removed. The dropdown now only ever opens on user click/hover, never forced open on load.)

- [ ] **Step 4: Remove the `injectStyle()` call from `init()`**

Old (`nav.js:70-74`):
```javascript
  function init() {
    injectStyle();
    var nodes = document.querySelectorAll("#topNav[data-page]");
    for (var i = 0; i < nodes.length; i++) render(nodes[i]);
  }
```

New:
```javascript
  function init() {
    var nodes = document.querySelectorAll("#topNav[data-page]");
    for (var i = 0; i < nodes.length; i++) render(nodes[i]);
  }
```

- [ ] **Step 5: Syntax-check**

Run: `node --check apps/web/public/js/nav.js`
Expected: no output, exit code 0.

- [ ] **Step 6: Verify the bug is actually gone**

Run: `grep -n '" open"\|injectStyle' apps/web/public/js/nav.js`
Expected: no matches.

- [ ] **Step 7: Commit**

```bash
git add apps/web/public/js/nav.js
git commit -m "fix(web): remove forced-open dropdown bug and JS-injected nav styles"
```

---

### Task 2: Vendor Pretendard + JetBrains Mono font files

**Files:**
- Create: `apps/web/public/fonts/Pretendard-Regular.woff2`
- Create: `apps/web/public/fonts/Pretendard-Medium.woff2`
- Create: `apps/web/public/fonts/Pretendard-SemiBold.woff2`
- Create: `apps/web/public/fonts/Pretendard-Bold.woff2`
- Create: `apps/web/public/fonts/JetBrainsMono-Regular.woff2`
- Create: `apps/web/public/fonts/JetBrainsMono-Medium.woff2`
- Create: `apps/web/public/fonts/JetBrainsMono-SemiBold.woff2`
- Create: `apps/web/public/fonts/JetBrainsMono-Bold.woff2`

- [ ] **Step 1: Create the directory and download the 8 files**

These exact URLs were verified reachable (HTTP 200) during planning — download each with `curl -fsSL -o <dest> <url>`:

```bash
mkdir -p apps/web/public/fonts
cd apps/web/public/fonts

curl -fsSL -o Pretendard-Regular.woff2  "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/web/static/woff2/Pretendard-Regular.woff2"
curl -fsSL -o Pretendard-Medium.woff2   "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/web/static/woff2/Pretendard-Medium.woff2"
curl -fsSL -o Pretendard-SemiBold.woff2 "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/web/static/woff2/Pretendard-SemiBold.woff2"
curl -fsSL -o Pretendard-Bold.woff2     "https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/packages/pretendard/dist/web/static/woff2/Pretendard-Bold.woff2"

curl -fsSL -o JetBrainsMono-Regular.woff2  "https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-400-normal.woff2"
curl -fsSL -o JetBrainsMono-Medium.woff2   "https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-500-normal.woff2"
curl -fsSL -o JetBrainsMono-SemiBold.woff2 "https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-600-normal.woff2"
curl -fsSL -o JetBrainsMono-Bold.woff2     "https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono@latest/latin-700-normal.woff2"

cd -
```

- [ ] **Step 2: Verify all 8 files downloaded and are non-trivial in size**

```bash
ls -la apps/web/public/fonts/
```

Expected: 8 files, each with a nonzero size. Pretendard files will be considerably larger (~700KB each — full Hangul glyph coverage) than JetBrains Mono files (~20KB each — Latin-only). If any file is 0 bytes or the command fails, re-run `curl` for that file (these are stable release-tagged CDN URLs, not "latest" for Pretendard, so they will not change under you — `jetbrains-mono@latest` may in principle update, but is expected stable for the duration of this task).

- [ ] **Step 3: Verify these are valid font files, not error pages**

```bash
file apps/web/public/fonts/*.woff2
```

Expected: each line reports `Web Open Font Format (Version 2)` (or similar) — not `HTML document` or `ASCII text` (which would indicate a downloaded error page).

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/fonts/
git commit -m "chore(web): vendor Pretendard and JetBrains Mono font files"
```

---

### Task 3: Create the shared stylesheet `theme.css`

**Files:**
- Create: `apps/web/public/css/theme.css`

- [ ] **Step 1: Write the file**

```css
/* apps/web/public/css/theme.css
   Shared header/nav chrome + vendored fonts (Pretendard, JetBrains Mono).
   No CDN — safe for air-gapped deployment. Linked by every apps/web/public
   page and by the /docs/ hub. Single source of truth for .top/.top a — do
   not redefine these rules in any page's local <style> block. */

@font-face {
  font-family: "Pretendard";
  src: url("/fonts/Pretendard-Regular.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "Pretendard";
  src: url("/fonts/Pretendard-Medium.woff2") format("woff2");
  font-weight: 500;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "Pretendard";
  src: url("/fonts/Pretendard-SemiBold.woff2") format("woff2");
  font-weight: 600;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "Pretendard";
  src: url("/fonts/Pretendard-Bold.woff2") format("woff2");
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "JetBrains Mono";
  src: url("/fonts/JetBrainsMono-Regular.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "JetBrains Mono";
  src: url("/fonts/JetBrainsMono-Medium.woff2") format("woff2");
  font-weight: 500;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "JetBrains Mono";
  src: url("/fonts/JetBrainsMono-SemiBold.woff2") format("woff2");
  font-weight: 600;
  font-style: normal;
  font-display: swap;
}
@font-face {
  font-family: "JetBrains Mono";
  src: url("/fonts/JetBrainsMono-Bold.woff2") format("woff2");
  font-weight: 700;
  font-style: normal;
  font-display: swap;
}

.top {
  background: #0C111F;
  border-bottom: 1px solid #1A2238;
  padding: 12px 20px;
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  align-items: center;
}
.top a {
  color: rgba(255, 255, 255, 0.68);
  text-decoration: none;
  font-weight: 600;
  font-size: 14px;
  transition: color 0.15s;
}
.top a:hover { color: #fff; }
.top a.nav-current,
.top summary.nav-current { color: #fff; }
.top .nav-dd { position: relative; }
.top .nav-dd summary {
  cursor: pointer;
  color: rgba(255, 255, 255, 0.68);
  font-weight: 600;
  font-size: 14px;
  list-style: none;
  transition: color 0.15s;
}
.top .nav-dd summary:hover { color: #fff; }
.top .nav-dd summary::-webkit-details-marker { display: none; }
.top .nav-dd-menu {
  position: absolute;
  top: 100%;
  left: 0;
  margin-top: 6px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.18);
  padding: 6px;
  display: flex;
  flex-direction: column;
  min-width: 140px;
  z-index: 20;
}
.top .nav-dd-menu a { color: #1428A0; padding: 6px 10px; border-radius: 6px; white-space: nowrap; }
.top .nav-dd-menu a:hover { background: #f1f5f9; }
.top .nav-admin { margin-left: auto; padding-left: 14px; border-left: 1px solid rgba(255, 255, 255, 0.18); }
```

- [ ] **Step 2: Verify no CSS syntax errors**

Run: `node -e "require('fs').readFileSync('apps/web/public/css/theme.css','utf8')" && echo "file readable"` (a full CSS parser isn't available in this repo's toolchain; this step only confirms the file is well-formed UTF-8 text — visual/structural review of the CSS above is the real check, already done during planning).

- [ ] **Step 3: Commit**

```bash
git add apps/web/public/css/theme.css
git commit -m "feat(web): add shared theme.css (dark header chrome + vendored fonts)"
```

---

### Task 4: Update `index.html`

**Files:**
- Modify: `apps/web/public/index.html`

- [ ] **Step 1: Link `theme.css` before the other stylesheets**

Old (`index.html:7-8`):
```html
  <link rel="stylesheet" href="/css/markdown.css"/>
  <link rel="stylesheet" href="/css/doclink.css"/>
```

New:
```html
  <link rel="stylesheet" href="/css/theme.css"/>
  <link rel="stylesheet" href="/css/markdown.css"/>
  <link rel="stylesheet" href="/css/doclink.css"/>
```

- [ ] **Step 2: Change `--primary`**

Old (`index.html:15`):
```html
      --primary: #1d4ed8;
```

New:
```html
      --primary: #1428A0;
```

- [ ] **Step 3: Prepend Pretendard to the body font stack**

Old (`index.html:24`):
```html
      font-family: system-ui, "Noto Sans KR", sans-serif;
```

New:
```html
      font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif;
```

- [ ] **Step 4: Delete the local `.top`/`.top a` rules**

Old (`index.html:29-38`):
```html
    .top {
      background: #fff;
      border-bottom: 1px solid var(--border);
      padding: 12px 20px;
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      align-items: center;
    }
    .top a { color: var(--primary); text-decoration: none; font-weight: 600; font-size: 14px; }
    .wrap { max-width: 880px; margin: 0 auto; padding: 28px 16px 72px; }
```

New:
```html
    .wrap { max-width: 880px; margin: 0 auto; padding: 28px 16px 72px; }
```

- [ ] **Step 5: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/index.html
```
Expected: `theme.css` link present once, `--primary: #1428A0;` present, `'Pretendard'` present in the body font-family line, and no `.top {` rule remains in the file's own `<style>` block (the only `.top`-related content left should be `.top` mentioned nowhere — confirm no match for a bare `.top {` block).

- [ ] **Step 6: Commit**

```bash
git add apps/web/public/index.html
git commit -m "feat(web): apply shared theme (header/color/font) to index.html"
```

---

### Task 5: Update `search.html`

**Files:**
- Modify: `apps/web/public/search.html`

- [ ] **Step 1: Link `theme.css` before the other stylesheets**

Old (`search.html:7-8`):
```html
<link rel="stylesheet" href="/css/markdown.css"/>
<link rel="stylesheet" href="/css/doclink.css"/>
```

New:
```html
<link rel="stylesheet" href="/css/theme.css"/>
<link rel="stylesheet" href="/css/markdown.css"/>
<link rel="stylesheet" href="/css/doclink.css"/>
```

- [ ] **Step 2: Change `--primary` and delete `.top`/`.top a`**

Old (`search.html:10-14`):
```html
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/search.html
```
Expected: `theme.css` link present once, `--primary:#1428A0;` present, `'Pretendard'` present, no `.top {` rule remains.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/search.html
git commit -m "feat(web): apply shared theme (header/color/font) to search.html"
```

---

### Task 6: Update `chat.html`

**Files:**
- Modify: `apps/web/public/chat.html`

- [ ] **Step 1: Link `theme.css` before the other stylesheets**

Old (`chat.html:7-8`):
```html
<link rel="stylesheet" href="/css/markdown.css"/>
<link rel="stylesheet" href="/css/doclink.css"/>
```

New:
```html
<link rel="stylesheet" href="/css/theme.css"/>
<link rel="stylesheet" href="/css/markdown.css"/>
<link rel="stylesheet" href="/css/doclink.css"/>
```

- [ ] **Step 2: Change `--primary` and delete `.top`/`.top a`**

Old (`chat.html:10-14`):
```html
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/chat.html
```
Expected: same shape as Task 5's check.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/chat.html
git commit -m "feat(web): apply shared theme (header/color/font) to chat.html"
```

---

### Task 7: Update `si.html`

**Files:**
- Modify: `apps/web/public/si.html`

- [ ] **Step 1: Link `theme.css` before the other stylesheets**

Old (`si.html:7-8`):
```html
<link rel="stylesheet" href="/css/markdown.css"/>
<link rel="stylesheet" href="/css/doclink.css"/>
```

New:
```html
<link rel="stylesheet" href="/css/theme.css"/>
<link rel="stylesheet" href="/css/markdown.css"/>
<link rel="stylesheet" href="/css/doclink.css"/>
```

- [ ] **Step 2: Change `--primary` and delete `.top`/`.top a`**

Old (`si.html:10-14`):
```html
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/si.html
```
Expected: same shape as Task 5's check.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/si.html
git commit -m "feat(web): apply shared theme (header/color/font) to si.html"
```

---

### Task 8: Update `tickets.html`

**Files:**
- Modify: `apps/web/public/tickets.html`

- [ ] **Step 1: Link `theme.css` before `<style>`**

`tickets.html` has no early `<link rel="stylesheet">` — insert `theme.css` right before the `<style>` tag.

Old (`tickets.html:1-8`):
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>기간 지원건 — CI-TEC Knowledge</title>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; --ok:#047857; }
```

New:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>기간 지원건 — CI-TEC Knowledge</title>
<link rel="stylesheet" href="/css/theme.css"/>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; --ok:#047857; }
```

(Note this single old/new block also changes `--primary` at the same time — same line.)

- [ ] **Step 2: Update body font-family and delete `.top`/`.top a`**

Old (`tickets.html:10-12`):
```html
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/tickets.html
```
Expected: `theme.css` link present once, `--primary:#1428A0;` present, `'Pretendard'` present, no `.top {` rule remains.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/tickets.html
git commit -m "feat(web): apply shared theme (header/color/font) to tickets.html"
```

---

### Task 9: Update `analytics.html`

**Files:**
- Modify: `apps/web/public/analytics.html`

- [ ] **Step 1: Link `theme.css` before `<style>` and change `--primary`**

Old (`analytics.html:1-8`):
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>집계 · Analytics — CI-TEC Knowledge</title>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; --ok:#047857; }
```

New:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>집계 · Analytics — CI-TEC Knowledge</title>
<link rel="stylesheet" href="/css/theme.css"/>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; --ok:#047857; }
```

- [ ] **Step 2: Update body font-family and delete `.top`/`.top a`**

Old (`analytics.html:10-12`):
```html
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/analytics.html
```
Expected: same shape as Task 8's check.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/analytics.html
git commit -m "feat(web): apply shared theme (header/color/font) to analytics.html"
```

---

### Task 10: Update `capacity.html`

**Files:**
- Modify: `apps/web/public/capacity.html`

- [ ] **Step 1: Link `theme.css` before `<style>` and change `--primary`**

Old (`capacity.html:1-8`):
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>공수·대수 (Capacity) — CI-TEC Knowledge</title>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; --ok:#047857; }
```

New:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>공수·대수 (Capacity) — CI-TEC Knowledge</title>
<link rel="stylesheet" href="/css/theme.css"/>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; --ok:#047857; }
```

- [ ] **Step 2: Update body font-family and delete `.top`/`.top a`**

Old (`capacity.html:10-12`):
```html
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/capacity.html
```
Expected: same shape as Task 8's check.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/capacity.html
git commit -m "feat(web): apply shared theme (header/color/font) to capacity.html"
```

---

### Task 11: Update `bundles.html`

**Files:**
- Modify: `apps/web/public/bundles.html`

- [ ] **Step 1: Link `theme.css` before `<style>` and change `--primary`**

Old (`bundles.html:1-8`):
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>War-room 번들 — CI-TEC Knowledge</title>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; }
```

New:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>War-room 번들 — CI-TEC Knowledge</title>
<link rel="stylesheet" href="/css/theme.css"/>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; }
```

- [ ] **Step 2: Update body font-family and delete `.top`/`.top a`**

Old (`bundles.html:10-12`):
```html
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/bundles.html
```
Expected: same shape as Task 8's check.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/bundles.html
git commit -m "feat(web): apply shared theme (header/color/font) to bundles.html"
```

---

### Task 12: Update `insights.html`

**Files:**
- Modify: `apps/web/public/insights.html`

- [ ] **Step 1: Link `theme.css` before the existing stylesheet link**

Old (`insights.html:7`):
```html
<link rel="stylesheet" href="/css/markdown.css"/>
```

New:
```html
<link rel="stylesheet" href="/css/theme.css"/>
<link rel="stylesheet" href="/css/markdown.css"/>
```

- [ ] **Step 2: Change `--primary` and update body font-family, delete `.top`/`.top a`**

Old (`insights.html:9-13`):
```html
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; --ok:#047857; --bad:#b91c1c; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; --ok:#047857; --bad:#b91c1c; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/insights.html
```
Expected: `theme.css` link present once, `--primary:#1428A0;` present, `'Pretendard'` present, no `.top {` rule remains.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/insights.html
git commit -m "feat(web): apply shared theme (header/color/font) to insights.html"
```

---

### Task 13: Update `admin.html`

**Files:**
- Modify: `apps/web/public/admin.html`

- [ ] **Step 1: Link `theme.css` before `<style>` and change `--primary`**

Old (`admin.html:1-8`):
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Admin / Ops — CI-TEC Knowledge</title>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; --ok:#047857; --bad:#b91c1c; --warn:#b45309; }
```

New:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Admin / Ops — CI-TEC Knowledge</title>
<link rel="stylesheet" href="/css/theme.css"/>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; --ok:#047857; --bad:#b91c1c; --warn:#b45309; }
```

- [ ] **Step 2: Update body font-family and delete `.top`/`.top a`**

Old (`admin.html:10-12`):
```html
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; align-items:center; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/admin.html
```
Expected: same shape as Task 8's check.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/admin.html
git commit -m "feat(web): apply shared theme (header/color/font) to admin.html"
```

---

### Task 14: Update `doc.html`

**Files:**
- Modify: `apps/web/public/doc.html`

- [ ] **Step 1: Link `theme.css` before the existing stylesheet link**

Old (`doc.html:7`):
```html
  <link rel="stylesheet" href="/css/markdown.css"/>
```

New:
```html
  <link rel="stylesheet" href="/css/theme.css"/>
  <link rel="stylesheet" href="/css/markdown.css"/>
```

- [ ] **Step 2: Change `--primary`, update body font-family, delete `.top`/`.top a`**

Old (`doc.html:9-13`):
```html
    :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); line-height:1.55; }
    .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:14px; flex-wrap:wrap; align-items:center; }
    .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
    :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); line-height:1.55; }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/doc.html
```
Expected: `theme.css` link present once, `--primary:#1428A0;` present, `'Pretendard'` present, no `.top {` rule remains. (`doc.html`'s `#navTitle` sibling `<div>` from the earlier nav-unification work is untouched by this task — do not modify it.)

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/doc.html
git commit -m "feat(web): apply shared theme (header/color/font) to doc.html"
```

---

### Task 15: Update `login.html`

**Files:**
- Modify: `apps/web/public/login.html`

- [ ] **Step 1: Link `theme.css` before `<style>` and change `--primary`**

Old (`login.html:1-8`):
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Login — CI-TEC Knowledge</title>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1d4ed8; --border:#e2e8f0; --ok:#047857; }
```

New:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Login — CI-TEC Knowledge</title>
<link rel="stylesheet" href="/css/theme.css"/>
<style>
  :root { --bg:#f6f8fb; --card:#fff; --text:#0f172a; --muted:#64748b; --primary:#1428A0; --border:#e2e8f0; --ok:#047857; }
```

- [ ] **Step 2: Update body font-family and delete `.top`/`.top a`**

Old (`login.html:10-12`):
```html
  body { margin:0; font-family: system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
  .top { background:#fff; border-bottom:1px solid var(--border); padding:12px 20px; display:flex; gap:16px; flex-wrap:wrap; }
  .top a { color:var(--primary); text-decoration:none; font-weight:600; font-size:14px; }
```

New:
```html
  body { margin:0; font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:var(--bg); color:var(--text); }
```

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css\|--primary\|Pretendard\|\.top {' apps/web/public/login.html
```
Expected: same shape as Task 8's check.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/login.html
git commit -m "feat(web): apply shared theme (header/color/font) to login.html"
```

---

### Task 16: Extend the shared header to the 9 script-generated `/docs/` pages

**Files:**
- Modify: `scripts/render_docs_html.py`
- Regenerate: `apps/web/public/docs/{ai-agent-guide,deploy,external-api,implementation-plan,mcp,oidc-idp-setup,packet-analysis-mcp-guide,phase2-pilot-checklist,query-catalog-analysis}.html`

- [ ] **Step 1: Replace the `TOP_NAV` template**

Old (`scripts/render_docs_html.py:45-51`):
```python
TOP_NAV = """<div class="top">
  <a href="/">홈</a>
  <a href="/docs/">문서 목록</a>
  <a href="/search.html">검색</a>
  <span class="sep">·</span>
  <a href="/docs/{md_name}">Markdown 원본</a>
</div>"""
```

New:
```python
TOP_NAV = """<div class="top" id="topNav" data-page="doc"></div>"""
```

(The `{md_name}`-formatted "Markdown 원본" link is dropped — it was page-specific and doesn't fit the shared nav's fixed menu. Each generated page still links its own `.md` source via the existing `meta-card` "원본" field already rendered in the page body at `render_docs_html.py:129` — `<div class="meta-card"><div class="label">원본</div><div class="value">docs/{md_name}</div></div>` — so the information isn't lost, just no longer duplicated in the header. `.format(md_name=md_name)` is called on this template at `render_docs_html.py:112`; since the new template has no `{...}` placeholders, `.format()` is still safe to call — it simply has nothing to substitute.)

- [ ] **Step 2: Add `theme.css` and `nav.js` to the generated `<head>`/`<body>`**

Old (`scripts/render_docs_html.py:103-112`):
```python
    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{nav_title} — CI-TEC Knowledge</title>
<link rel="stylesheet" href="/css/docs.css" />
</head>
<body>
{TOP_NAV.format(md_name=md_name)}
```

New:
```python
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
```

- [ ] **Step 3: Regenerate the 9 output pages**

```bash
.venv/bin/python scripts/render_docs_html.py
```

Expected: 9 lines of `wrote apps/web/public/docs/<name>.html` output, no errors. (If `markdown` isn't importable, run `.venv/bin/pip install markdown` first — the script's own docstring documents this.)

- [ ] **Step 4: Verify the regenerated pages**

```bash
grep -l 'id="topNav" data-page="doc"' apps/web/public/docs/*.html
grep -l 'js/nav.js' apps/web/public/docs/*.html
grep -l 'css/theme.css' apps/web/public/docs/*.html
```

Expected: each command lists exactly the 9 regenerated files (not `index.html` or `design.html` — those are hand-authored and handled in Tasks 17-18).

- [ ] **Step 5: Confirm the 9 files are still tracked as generated (not accidentally hand-diverged)**

```bash
git status --short apps/web/public/docs/
```

Expected: the 9 regenerated `.html` files show as modified; the corresponding `.md` mirror files (also written by this script, per `render_docs_html.py:80`) may also show as modified if their content changed — confirm any `.md` diffs are trivial/no-op (the script just re-copies the source markdown, so diffs are expected only if source `docs/*.md` changed since last generation, which it shouldn't have here).

- [ ] **Step 6: Commit**

```bash
git add scripts/render_docs_html.py apps/web/public/docs/
git commit -m "feat(web): extend shared theme/nav to the 9 generated docs pages"
```

---

### Task 17: Update `docs/index.html` (hand-authored)

**Files:**
- Modify: `apps/web/public/docs/index.html`

- [ ] **Step 1: Link `theme.css` and `nav.js`, replace the hardcoded nav, retire the local `.top`/`.top a` rules**

Old (`docs/index.html:1-22`):
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>문서 — CI-TEC Knowledge Platform</title>
<style>
  body { font-family: system-ui, "Noto Sans KR", sans-serif; background:#f6f8fb; color:#0f172a; margin:0; }
  .top { background:#fff; border-bottom:1px solid #e2e8f0; padding:12px 20px; }
  .top a { color:#1d4ed8; margin-right:14px; text-decoration:none; font-weight:600; font-size:14px; }
  .wrap { max-width:720px; margin:0 auto; padding:32px 20px; }
  .card { background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:18px 20px; margin-bottom:12px; }
  .card h2 { margin:0 0 6px; font-size:1.1rem; }
  .card p { margin:0; color:#64748b; font-size:14px; }
  a.btn { display:inline-block; margin-top:10px; color:#1d4ed8; font-weight:700; text-decoration:none; }
</style>
</head>
<body>
<div class="top">
  <a href="/">홈</a>
  <a href="/docs/">문서 목록</a>
</div>
```

New:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>문서 — CI-TEC Knowledge Platform</title>
<link rel="stylesheet" href="/css/theme.css"/>
<script src="/js/nav.js"></script>
<style>
  body { font-family: 'Pretendard', system-ui, "Noto Sans KR", sans-serif; background:#f6f8fb; color:#0f172a; margin:0; }
  .wrap { max-width:720px; margin:0 auto; padding:32px 20px; }
  .card { background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:18px 20px; margin-bottom:12px; }
  .card h2 { margin:0 0 6px; font-size:1.1rem; }
  .card p { margin:0; color:#64748b; font-size:14px; }
  a.btn { display:inline-block; margin-top:10px; color:#1428A0; font-weight:700; text-decoration:none; }
</style>
</head>
<body>
<div class="top" id="topNav" data-page="doc"></div>
```

(Note `a.btn`'s hardcoded `#1d4ed8` is also updated to `#1428A0` since it's a visible brand-color use on this page, distinct from the header — this is a local rule, not part of `theme.css`, and must be edited directly to avoid a stray old-blue accent next to the new-blue header/dropdown.)

- [ ] **Step 2: Verify**

```bash
grep -n 'theme.css\|js/nav.js\|id="topNav"\|Pretendard\|#1d4ed8\|\.top {' apps/web/public/docs/index.html
```
Expected: `theme.css` and `js/nav.js` each present once, `id="topNav"` present once, `'Pretendard'` present, no `#1d4ed8` remaining, no `.top {` rule remains.

- [ ] **Step 3: Commit**

```bash
git add apps/web/public/docs/index.html
git commit -m "feat(web): apply shared theme/nav to docs/index.html"
```

---

### Task 18: Add font loading to `docs/design.html` and update `docs.css`'s brand color

**Files:**
- Modify: `apps/web/public/docs/design.html`
- Modify: `apps/web/public/css/docs.css`

- [ ] **Step 1: Add `theme.css` before `design.html`'s inline `<style>` block**

`design.html` has no `<link rel="stylesheet">` at all — it's fully self-contained with its own inline `<style>` block, including its own separate `--primary`/`--bg`/`--border` tokens (independent copy, not `docs.css`).

Old (`docs/design.html:1-6`):
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>CI-TEC 지식기반 검색 플랫폼 설계서 v2.4</title>
<style>
```

New:
```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>CI-TEC 지식기반 검색 플랫폼 설계서 v2.4</title>
<link rel="stylesheet" href="/css/theme.css"/>
<style>
```

Do **not** change anything else in this file — no `.top` div exists here (confirmed during planning), no nav restructuring, no color changes, and specifically leave this file's own inline `--primary: #1d4ed8;` (and the rest of its independent token set) exactly as-is — out of scope per the spec's explicit non-goal. This file's own `--font`/`--mono` tokens (`docs/design.html:35-36`) already name `"Pretendard"`/`"JetBrains Mono"` first in their fallback stacks, exactly like `docs.css` — so linking `theme.css` here is sufficient by itself to make the whole page render in the vendored fonts, with no other edit needed.

- [ ] **Step 2: Update `docs.css`'s `--primary` token**

Old (`apps/web/public/css/docs.css:11`):
```css
    --primary: #1d4ed8;
```

New:
```css
    --primary: #1428A0;
```

(Leave `--primary-soft`/`--primary-dark` and every other token in `docs.css` untouched — only the base `--primary` changes, matching the same single-value change already applied to all 12 app pages' `--primary`.)

- [ ] **Step 3: Verify**

```bash
grep -n 'theme.css' apps/web/public/docs/design.html
grep -n -- '--primary:' apps/web/public/css/docs.css
```
Expected: first command shows the new `theme.css` link; second shows `--primary: #1428A0;`.

- [ ] **Step 4: Commit**

```bash
git add apps/web/public/docs/design.html apps/web/public/css/docs.css
git commit -m "feat(web): load vendored fonts on design.html, update docs.css brand color"
```

---

### Task 19: Final cross-site verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm the `open`-attribute bug is gone and `theme.css` is linked everywhere it should be**

```bash
cd apps/web/public
grep -rn '" open"' js/nav.js; echo "(expect: no output)"

for f in index.html search.html chat.html si.html tickets.html analytics.html capacity.html bundles.html insights.html admin.html doc.html login.html docs/index.html; do
  echo "$f: theme.css=$(grep -c 'css/theme.css' "$f")  local-top=$(grep -c '\.top {' "$f")"
done
```
Expected: every file shows `theme.css=1 local-top=0`. (`docs/design.html` is intentionally excluded from this loop — it never had a `.top` rule to begin with, per Task 18.)

- [ ] **Step 2: Confirm every `--primary` site-wide is now the new brand blue**

```bash
grep -rn -- '--primary:\s*#1d4ed8\|--primary: #1d4ed8' *.html docs/*.html css/*.css
```
Expected: no matches anywhere.

- [ ] **Step 3: Confirm the 9 generated docs pages plus `docs/index.html` all reference `nav.js`**

```bash
grep -L 'js/nav.js' docs/*.html
```
Expected: exactly `docs/design.html` and `docs/pilot-signoff.html` — both are standalone generated-report pages with no `.top` header at all (design.html has its own hero/TOC; pilot-signoff.html is a bare evidence-pack document with no nav of any kind), out of scope for this plan per the spec's non-goals. Neither was touched by any task above. No other `.html` file should appear in this list. (`grep -L` lists files that do NOT match.)

- [ ] **Step 4: Live smoke test via the running Docker stack**

```bash
docker compose restart web
sleep 1
curl -s http://localhost:8572/ | grep -o 'css/theme.css\|id="topNav"'
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8572/css/theme.css
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8572/fonts/Pretendard-Regular.woff2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8572/fonts/JetBrainsMono-Regular.woff2
curl -s http://localhost:8572/docs/ | grep -o 'css/theme.css\|id="topNav"'
curl -s http://localhost:8572/docs/mcp.html | grep -o 'css/theme.css\|id="topNav"\|js/nav.js'
```
Expected: all `grep -o` calls print the expected tokens; both font `curl` status codes are `200`.

- [ ] **Step 5: Manual browser check (report to the user, don't skip)**

No headless browser is available to this session. State explicitly to the user that steps 1-4 confirm the markup/assets are correct and serving, but a human should open `http://<host>:8572/` in a browser and click through search → insight → 문서 → admin to visually confirm: (a) the dropdown never appears pre-expanded, (b) Admin stays pinned to the top-right divider on every page including the `/docs/` hub, (c) the header is the new dark navy bar with Pretendard-rendered Korean text, (d) `/docs/design.html` still looks structurally the same as before (only font swapped).
