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
    html += linkHtml("doc", "문서", "/docs/", current);
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
