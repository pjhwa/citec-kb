# 상단 내비게이션 통합 · MCP 연결 안내 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 12 hardcoded, inconsistent top-nav blocks in `apps/web/public/*.html` with one shared component, collapse the 6 search-related links into a dropdown, move Admin to the right with a divider, drop the Login link, and add a Claude MCP connection guide (Claude Code CLI + Claude Desktop JSON, host auto-filled from `location.hostname`) to the homepage.

**Architecture:** A single new static asset `apps/web/public/js/nav.js` renders the menu into an empty `<div class="top" id="topNav" data-page="...">` placeholder that replaces the old hardcoded `<a>` lists in every page. Each page keeps its own CSS custom properties (`--primary`, `--border`, `--muted`) already defined in its own `:root`, which `nav.js` reads via `var(...)` so no page-specific styling is needed. No backend/API changes; the MCP guide is pure client-side string templating driven by `location.hostname`.

**Tech Stack:** Vanilla JS (no build step), static HTML/CSS served by nginx (`apps/web/public/`). No test runner exists for this app — verification is done via `grep`/`node --check` structural checks plus a manual browser smoke check described in the final task.

---

## File Structure

- **Create** `apps/web/public/js/nav.js` — shared nav renderer (menu data, dropdown markup, active-page highlighting, one-time injected `<style>`).
- **Modify** (one task each): `index.html`, `search.html`, `chat.html`, `si.html`, `tickets.html`, `analytics.html`, `capacity.html`, `bundles.html`, `insights.html`, `admin.html`, `doc.html`, `login.html`
  - Replace the hardcoded `<div class="top">...</div>` with `<div class="top" id="topNav" data-page="<id>"></div>`.
  - Add `<script src="/js/nav.js"></script>` as the **first** `<script>` tag in the page (before `markdown.js`/`doclink.js`/`auth.js`/inline scripts) so the nav renders before other scripts run.
  - Remove any `CitecAuth.mountChip(".top")` call (it re-adds a "Login" chip into `.top`, which must not reappear).
- **Modify** `index.html` additionally — insert the MCP connection guide card + supporting JS.

---

### Task 1: Create the shared nav component

**Files:**
- Create: `apps/web/public/js/nav.js`

- [ ] **Step 1: Write `nav.js`**

```javascript
/**
 * Shared top navigation, rendered into <div class="top" id="topNav" data-page="...">.
 * Reads --primary/--border/--muted from the host page's own :root so no
 * page-specific CSS is required.
 */
(function (global) {
  "use strict";

  var SEARCH_ITEMS = [
    { id: "search", label: "검색", href: "/search.html" },
    { id: "chat", label: "Fast QA", href: "/chat.html" },
    { id: "si", label: "유사장애", href: "/si.html" },
    { id: "tickets", label: "기간 지원건", href: "/tickets.html" },
    { id: "analytics", label: "집계", href: "/analytics.html" },
    { id: "capacity", label: "공수·대수", href: "/capacity.html" },
  ];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

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
    var cls = id === current ? ' class="nav-current" aria-current="page"' : "";
    return '<a href="' + esc(href) + '"' + cls + ">" + esc(label) + "</a>";
  }

  function render(container) {
    var current = container.getAttribute("data-page") || "";
    var searchCurrent = SEARCH_ITEMS.some(function (it) { return it.id === current; });

    var html = "";
    html += linkHtml("home", "홈", "/", current);

    html += '<details class="nav-dd"' + (searchCurrent ? " open" : "") + ">";
    html += '<summary' + (searchCurrent ? ' class="nav-current" aria-current="page"' : "") + ">검색 ▾</summary>";
    html += '<div class="nav-dd-menu">';
    SEARCH_ITEMS.forEach(function (it) {
      html += linkHtml(it.id, it.label, it.href, current);
    });
    html += "</div></details>";

    html += linkHtml("bundles", "번들", "/bundles.html", current);
    html += linkHtml("insights", "Insight", "/insights.html", current);
    html += linkHtml("docs", "문서", "/docs/", current);
    html += '<a href="/api/docs" target="_blank" rel="noopener">API</a>';
    html += '<span class="nav-admin">' + linkHtml("admin", "Admin", "/admin.html", current) + "</span>";

    container.innerHTML = html;
  }

  function init() {
    injectStyle();
    var nodes = document.querySelectorAll("#topNav[data-page]");
    for (var i = 0; i < nodes.length; i++) render(nodes[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : this);
```

- [ ] **Step 2: Syntax-check the file**

Run: `node --check apps/web/public/js/nav.js`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add apps/web/public/js/nav.js
git commit -m "feat(web): add shared top-nav component with search dropdown"
```

---

### Task 2: Wire up `index.html` (home page)

**Files:**
- Modify: `apps/web/public/index.html:175-187`

- [ ] **Step 1: Replace the hardcoded top nav**

Old (`index.html:175-187`):

```html
  <div class="top">
    <a href="/"><strong>홈 · 통합 질의</strong></a>
    <a href="/search.html">검색</a>
    <a href="/chat.html">Fast QA</a>
    <a href="/si.html">유사장애</a>
    <a href="/tickets.html">기간</a>
    <a href="/analytics.html">집계</a>
    <a href="/capacity.html">공수</a>
    <a href="/insights.html">Insight</a>
    <a href="/admin.html">Admin</a>
    <a href="/login.html">Login</a>
    <a href="/docs/">문서</a>
  </div>
```

New:

```html
  <div class="top" id="topNav" data-page="home"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

Old (`index.html:238`):

```html
  <script src="/js/markdown.js"></script>
```

New:

```html
  <script src="/js/nav.js"></script>
  <script src="/js/markdown.js"></script>
```

- [ ] **Step 3: Remove the `mountChip` call**

Old (`index.html:729`):

```html
    CitecAuth.mountChip(".top");
```

New: delete this line entirely (the surrounding lines `renderChips();` and `loadHealth();` stay).

- [ ] **Step 4: Verify structurally**

Run:
```bash
grep -n 'id="topNav"' apps/web/public/index.html
grep -n 'js/nav.js' apps/web/public/index.html
grep -n 'mountChip' apps/web/public/index.html
grep -n 'Login' apps/web/public/index.html
```
Expected: first two greps each print one match; the `mountChip` grep prints nothing; the `Login` grep prints nothing (the pilot-note text about `AUTH_MODE=off` does not contain the word "Login").

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/index.html
git commit -m "feat(web): use shared nav on index.html"
```

---

### Task 3: Wire up `search.html`

**Files:**
- Modify: `apps/web/public/search.html:47-59`, `:94`, `:163`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/si.html">유사장애</a>
  <a href="/tickets.html">기간 지원건</a>
  <a href="/analytics.html">집계</a>
  <a href="/capacity.html">공수·대수</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
  <a href="/api/docs" target="_blank">API</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="search"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

Old (`search.html:94`):

```html
<script src="/js/markdown.js"></script>
```

New:

```html
<script src="/js/nav.js"></script>
<script src="/js/markdown.js"></script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Old (`search.html:163`):

```html
<script src="/js/auth.js"></script>
<script>if(window.CitecAuth)CitecAuth.mountChip(".top");</script>
```

New:

```html
<script src="/js/auth.js"></script>
```

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/search.html
```
Expected: `topNav` and `nav.js` each appear once; `mountChip` and `Login` produce no matches.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/search.html
git commit -m "feat(web): use shared nav on search.html"
```

---

### Task 4: Wire up `chat.html`

**Files:**
- Modify: `apps/web/public/chat.html:56-68`, `:94`, `:216`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/si.html">유사장애</a>
  <a href="/tickets.html">기간 지원건</a>
  <a href="/analytics.html">집계</a>
  <a href="/capacity.html">공수·대수</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
  <a href="/api/docs" target="_blank">API</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="chat"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

Old (`chat.html:94`):

```html
<script src="/js/markdown.js"></script>
```

New:

```html
<script src="/js/nav.js"></script>
<script src="/js/markdown.js"></script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Old (`chat.html:216`):

```html
<script src="/js/auth.js"></script>
<script>if(window.CitecAuth)CitecAuth.mountChip(".top");</script>
```

New:

```html
<script src="/js/auth.js"></script>
```

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/chat.html
```
Expected: same shape as Task 3's check.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/chat.html
git commit -m "feat(web): use shared nav on chat.html"
```

---

### Task 5: Wire up `si.html`

**Files:**
- Modify: `apps/web/public/si.html:40-52`, `:71`, `:149`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/si.html">유사장애</a>
  <a href="/tickets.html">기간 지원건</a>
  <a href="/analytics.html">집계</a>
  <a href="/capacity.html">공수·대수</a>
  <a href="/bundles.html">번들</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="si"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

Old (`si.html:71`):

```html
<script src="/js/markdown.js"></script>
```

New:

```html
<script src="/js/nav.js"></script>
<script src="/js/markdown.js"></script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Old (`si.html:149`):

```html
<script src="/js/auth.js"></script>
<script>if(window.CitecAuth)CitecAuth.mountChip(".top");</script>
```

New:

```html
<script src="/js/auth.js"></script>
```

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/si.html
```
Expected: same shape as Task 3's check.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/si.html
git commit -m "feat(web): use shared nav on si.html"
```

---

### Task 6: Wire up `tickets.html`

**Files:**
- Modify: `apps/web/public/tickets.html:42-54`, `:91`, `:191`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/si.html">유사장애</a>
  <a href="/tickets.html">기간 지원건</a>
  <a href="/analytics.html">집계</a>
  <a href="/capacity.html">공수·대수</a>
  <a href="/bundles.html">번들</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="tickets"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

`tickets.html` does not load `markdown.js`; its first `<script>` is an inline block at line 91. Insert `nav.js` immediately before it.

Old (`tickets.html:91`):

```html
<script>
```

New:

```html
<script src="/js/nav.js"></script>
<script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Old (`tickets.html:189-191`):

```html
<script src="/js/doclink.js"></script>
<script src="/js/auth.js"></script>
<script>if(window.CitecAuth)CitecAuth.mountChip(".top");</script>
```

New:

```html
<script src="/js/doclink.js"></script>
<script src="/js/auth.js"></script>
```

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/tickets.html
```
Expected: `topNav` and `nav.js` each appear once; `mountChip` and `Login` produce no matches.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/tickets.html
git commit -m "feat(web): use shared nav on tickets.html"
```

---

### Task 7: Wire up `analytics.html`

**Files:**
- Modify: `apps/web/public/analytics.html:38-50`, `:88`, `:201`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/si.html">유사장애</a>
  <a href="/tickets.html">기간 지원건</a>
  <a href="/analytics.html">집계</a>
  <a href="/capacity.html">공수·대수</a>
  <a href="/bundles.html">번들</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="analytics"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

`analytics.html`'s first `<script>` is an inline block at line 88.

Old (`analytics.html:88`):

```html
<script>
```

New:

```html
<script src="/js/nav.js"></script>
<script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Old (`analytics.html:199-201`):

```html
<script src="/js/doclink.js"></script>
<script src="/js/auth.js"></script>
<script>if(window.CitecAuth)CitecAuth.mountChip(".top");</script>
```

New:

```html
<script src="/js/doclink.js"></script>
<script src="/js/auth.js"></script>
```

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/analytics.html
```
Expected: same shape as Task 6's check.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/analytics.html
git commit -m "feat(web): use shared nav on analytics.html"
```

---

### Task 8: Wire up `capacity.html`

**Files:**
- Modify: `apps/web/public/capacity.html:35-47`, `:77`, `:184`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/si.html">유사장애</a>
  <a href="/tickets.html">기간 지원건</a>
  <a href="/analytics.html">집계</a>
  <a href="/capacity.html">공수·대수</a>
  <a href="/bundles.html">번들</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="capacity"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

`capacity.html`'s first `<script>` is an inline block at line 77.

Old (`capacity.html:77`):

```html
<script>
```

New:

```html
<script src="/js/nav.js"></script>
<script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Old (`capacity.html:183-184`):

```html
<script src="/js/auth.js"></script>
<script>if(window.CitecAuth)CitecAuth.mountChip(".top");</script>
```

New:

```html
<script src="/js/auth.js"></script>
```

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/capacity.html
```
Expected: same shape as Task 6's check.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/capacity.html
git commit -m "feat(web): use shared nav on capacity.html"
```

---

### Task 9: Wire up `bundles.html`

**Files:**
- Modify: `apps/web/public/bundles.html:29-41`, `:66`, `:143`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/si.html">유사장애</a>
  <a href="/tickets.html">기간 지원건</a>
  <a href="/analytics.html">집계</a>
  <a href="/capacity.html">공수·대수</a>
  <a href="/bundles.html">번들</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="bundles"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

`bundles.html`'s first `<script>` tag is `auth.js` at line 66; there is no `markdown.js`/`doclink.js` load.

Old (`bundles.html:66`):

```html
<script src="/js/auth.js"></script>
```

New:

```html
<script src="/js/nav.js"></script>
<script src="/js/auth.js"></script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Find and delete the line (around `bundles.html:143`):

```html
CitecAuth.mountChip(".top");
```

This line sits inside an existing inline `<script>` block (not on its own `<script>...</script>` tag like other pages) — delete just this statement line, leaving the rest of the block intact.

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/bundles.html
```
Expected: `topNav` and `nav.js` each appear once; `mountChip` and `Login` produce no matches.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/bundles.html
git commit -m "feat(web): use shared nav on bundles.html"
```

---

### Task 10: Wire up `insights.html`

**Files:**
- Modify: `apps/web/public/insights.html:31-39`, `:65-66`, `:154`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/insights.html">Insight</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="insights"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

Old (`insights.html:65-66`):

```html
<script src="/js/markdown.js"></script>
<script src="/js/auth.js"></script>
```

New:

```html
<script src="/js/nav.js"></script>
<script src="/js/markdown.js"></script>
<script src="/js/auth.js"></script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Find and delete the line (around `insights.html:154`):

```html
CitecAuth.mountChip(".top");
```

Same as Task 9 — this is a statement inside an existing inline `<script>` block; delete just the line.

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/insights.html
```
Expected: `topNav` and `nav.js` each appear once; `mountChip` and `Login` produce no matches.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/insights.html
git commit -m "feat(web): use shared nav on insights.html"
```

---

### Task 11: Wire up `admin.html`

**Files:**
- Modify: `apps/web/public/admin.html:41-49`, `:147`, `:316`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/insights.html">Insight</a>
  <a href="/bundles.html">번들</a>
  <a href="/admin.html">Admin</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="admin"></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

`admin.html`'s first `<script>` tag is `auth.js` at line 147.

Old (`admin.html:147`):

```html
<script src="/js/auth.js"></script>
```

New:

```html
<script src="/js/nav.js"></script>
<script src="/js/auth.js"></script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Find and delete the line (around `admin.html:316`):

```html
CitecAuth.mountChip(".top");
```

This is a statement inside an existing inline `<script>` block; delete just the line.

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip\|Login' apps/web/public/admin.html
```
Expected: `topNav` and `nav.js` each appear once; `mountChip` and `Login` produce no matches.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/admin.html
git commit -m "feat(web): use shared nav on admin.html"
```

---

### Task 12: Wire up `doc.html`

`doc.html` is a special case: its `.top` currently has a `<span id="navTitle">` after the links, used by the page's own script to show the current document's title. Keep that span, but move it to be a sibling of `#topNav` rather than a child, since `nav.js` will overwrite `#topNav`'s `innerHTML`.

**Files:**
- Modify: `apps/web/public/doc.html:30-37`, `:50`

- [ ] **Step 1: Replace the hardcoded top nav, keeping `navTitle` as a sibling**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/chat.html">Fast QA</a>
  <a href="/tickets.html">기간 지원건</a>
  <a href="/docs/">문서</a>
  <span id="navTitle" class="meta" style="margin:0"></span>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="doc"></div>
<div style="padding:4px 20px 0"><span id="navTitle" class="meta" style="margin:0"></span></div>
```

- [ ] **Step 2: Add `nav.js` as the first script**

Old (`doc.html:50`):

```html
<script src="/js/markdown.js"></script>
```

New:

```html
<script src="/js/nav.js"></script>
<script src="/js/markdown.js"></script>
```

- [ ] **Step 3: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|id="navTitle"\|Login' apps/web/public/doc.html
```
Expected: `topNav`, `nav.js`, and `navTitle` each appear once; `Login` produces no matches. (`doc.html` never called `mountChip`, so there is nothing to remove there.)

- [ ] **Step 4: Confirm `navTitle` is still populated correctly**

```bash
grep -n 'navTitle' apps/web/public/doc.html
```
Expected: two matches — the new `<span id="navTitle">` markup, and the existing JS that does `document.getElementById("navTitle")` (or `$("navTitle")`) to set its text. Read the surrounding JS to confirm the id/usage is unchanged — no JS edits are needed since the span keeps the same `id`.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/doc.html
git commit -m "feat(web): use shared nav on doc.html"
```

---

### Task 13: Wire up `login.html`

**Files:**
- Modify: `apps/web/public/login.html:26-32`, `:64`, `:125`

- [ ] **Step 1: Replace the hardcoded top nav**

Old:

```html
<div class="top">
  <a href="/">홈</a>
  <a href="/search.html">검색</a>
  <a href="/insights.html">Insight</a>
  <a href="/login.html">Login</a>
  <a href="/docs/">문서</a>
</div>
```

New:

```html
<div class="top" id="topNav" data-page="login"></div>
```

Note: `login.html` remains reachable by direct URL; it simply is not linked from the shared nav (per design — Login has no `nav.js` entry at all).

- [ ] **Step 2: Add `nav.js` as the first script**

`login.html`'s first `<script>` tag is `auth.js` at line 64.

Old (`login.html:64`):

```html
<script src="/js/auth.js"></script>
```

New:

```html
<script src="/js/nav.js"></script>
<script src="/js/auth.js"></script>
```

- [ ] **Step 3: Remove the `mountChip` line**

Find and delete the line (around `login.html:125`):

```html
CitecAuth.mountChip(".top");
```

This is a statement inside an existing inline `<script>` block; delete just the line.

- [ ] **Step 4: Verify**

```bash
grep -n 'id="topNav"\|js/nav.js\|mountChip' apps/web/public/login.html
```
Expected: `topNav` and `nav.js` each appear once; `mountChip` produces no matches.

- [ ] **Step 5: Commit**

```bash
git add apps/web/public/login.html
git commit -m "feat(web): use shared nav on login.html"
```

---

### Task 14: Add the Claude MCP connection guide to `index.html`

**Files:**
- Modify: `apps/web/public/index.html` (ask-card region, around what is now line ~200 after Task 2's edit; and the bottom init script)

- [ ] **Step 1: Insert the MCP guide card after `.ask-card`**

Old:

```html
    <div id="status" class="meta" style="margin-bottom:10px"></div>
    <div id="out"></div>
```

New:

```html
    <div class="card mcp-card">
      <h3 style="margin:0 0 6px">Claude MCP로 연결하기</h3>
      <p class="meta" style="margin:0 0 10px">
        이 서버를 Claude Code / Claude Desktop에 MCP 도구로 등록할 수 있습니다.
        아래 주소는 현재 접속 중인 서버 기준으로 자동 채워집니다.
      </p>
      <div class="meta" style="margin-bottom:4px">Claude Code</div>
      <div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:12px">
        <pre class="answer" id="mcpCliCmd" style="flex:1;margin:0"></pre>
        <button type="button" class="ghost" id="btnCopyCli" style="white-space:nowrap">복사</button>
      </div>
      <div class="meta" style="margin-bottom:4px">Claude Desktop (claude_desktop_config.json)</div>
      <div style="display:flex;gap:8px;align-items:flex-start">
        <pre class="answer" id="mcpDesktopJson" style="flex:1;margin:0"></pre>
        <button type="button" class="ghost" id="btnCopyDesktop" style="white-space:nowrap">복사</button>
      </div>
    </div>

    <div id="status" class="meta" style="margin-bottom:10px"></div>
    <div id="out"></div>
```

- [ ] **Step 2: Add the JS that fills in and copies the commands**

Add this function block right after the existing `function deepLink(intent, q) { ... }` function (search for it in the `<script>` block that starts near the bottom of `index.html`):

```javascript
    function mcpServerUrl() {
      const host = location.hostname || "localhost";
      return `http://${host}:8577/mcp`;
    }

    function copyText(text, btn) {
      navigator.clipboard.writeText(text).then(() => {
        const orig = btn.textContent;
        btn.textContent = "복사됨";
        setTimeout(() => { btn.textContent = orig; }, 1200);
      }).catch(() => {
        btn.textContent = "복사 실패";
        setTimeout(() => { btn.textContent = "복사"; }, 1200);
      });
    }

    function renderMcpGuide() {
      const url = mcpServerUrl();
      const cli = `claude mcp add --scope user --transport http wiki-mcp ${url}`;
      const desktop = JSON.stringify(
        { mcpServers: { "wiki-mcp": { url, transport: "streamable-http" } } },
        null,
        2
      );
      $("mcpCliCmd").textContent = cli;
      $("mcpDesktopJson").textContent = desktop;
      $("btnCopyCli").onclick = () => copyText(cli, $("btnCopyCli"));
      $("btnCopyDesktop").onclick = () => copyText(desktop, $("btnCopyDesktop"));
    }
```

- [ ] **Step 3: Call `renderMcpGuide()` during page init**

Old (end of the bottom `<script>` block):

```html
    renderChips();
    loadHealth();
    if (params.get("q") && params.get("auto") !== "0") ask();
```

New:

```html
    renderChips();
    renderMcpGuide();
    loadHealth();
    if (params.get("q") && params.get("auto") !== "0") ask();
```

- [ ] **Step 4: Syntax-check the page's inline JS**

Run:
```bash
node -e "
const fs = require('fs');
const html = fs.readFileSync('apps/web/public/index.html', 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
for (const s of scripts) new Function(s);
console.log('OK:', scripts.length, 'inline scripts parsed');
"
```
Expected: `OK: N inline scripts parsed` with no thrown error (the function bodies reference `document`/`window`/`fetch` etc. but `new Function` only parses+wraps, it does not execute at module scope in a way that fails — if `--check`-style parsing errors exist, this throws a `SyntaxError` naming the exact issue).

- [ ] **Step 5: Manual browser smoke check**

Since this app has no headless UI test runner, verify by hand:
1. `docker compose up -d web` (or however the web static server is normally started in this repo — check `docker-compose.yml` / `apps/web/nginx.conf` if unsure).
2. Open `http://<dev-host>/` in a browser.
3. Confirm the new "Claude MCP로 연결하기" card appears above the search results area, before scrolling.
4. Confirm the Claude Code command line shows `claude mcp add --scope user --transport http wiki-mcp http://<the-hostname-you-typed-in-the-address-bar>:8577/mcp`.
5. Confirm the JSON block shows a `"wiki-mcp"` key with matching `url` and `"transport": "streamable-http"`.
6. Click both "복사" buttons and confirm the button label briefly changes to "복사됨" (paste somewhere to confirm clipboard content if possible).
7. Confirm the top nav shows: 홈 · 검색▾ (hover/click reveals 6 sub-links) · 번들 · Insight · 문서 · API, with Admin visually separated on the right, and no Login link anywhere.

- [ ] **Step 6: Commit**

```bash
git add apps/web/public/index.html
git commit -m "feat(web): add Claude MCP connection guide to homepage"
```

---

### Task 15: Final cross-page verification

**Files:** none (verification only)

- [ ] **Step 1: Confirm no page still has a hardcoded Login link or mountChip call**

```bash
cd apps/web/public
grep -rln 'Login' *.html
grep -rln 'mountChip' *.html
```
Expected: both commands print nothing (no matches in any of the 12 files).

- [ ] **Step 2: Confirm every page loads `nav.js` and has exactly one `#topNav`**

```bash
for f in index.html search.html chat.html si.html tickets.html analytics.html capacity.html bundles.html insights.html admin.html doc.html login.html; do
  n_nav=$(grep -c 'js/nav.js' "$f")
  n_top=$(grep -c 'id="topNav"' "$f")
  echo "$f: nav.js=$n_nav topNav=$n_top"
done
```
Expected: every line reads `nav.js=1 topNav=1`.

- [ ] **Step 3: Confirm each `data-page` value is unique and matches the intended page id**

```bash
grep -o 'data-page="[a-z]*"' index.html search.html chat.html si.html tickets.html analytics.html capacity.html bundles.html insights.html admin.html doc.html login.html
```
Expected: `home, search, chat, si, tickets, analytics, capacity, bundles, insights, admin, doc, login` — one per file, no duplicates, no typos.

- [ ] **Step 4: Update the design spec's status (optional but recommended)**

No code change — just confirm in conversation with the user that the implementation matches `docs/superpowers/specs/2026-07-29-nav-unification-mcp-guide-design.md`, and flag any deviations found during implementation (e.g. the `mountChip` call existed on all 12 pages, not just 3 as the spec's risk section implied — this plan already corrects for that).
