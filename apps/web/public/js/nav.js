/**
 * Shared top navigation, rendered into <div class="top" id="topNav" data-page="...">.
 * Visual styling (colors, dropdown, admin divider, fonts) lives in
 * /css/theme.css — this file only builds the DOM structure.
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

  function linkHtml(id, label, href, current) {
    var cls = id === current ? ' class="nav-current" aria-current="page"' : "";
    return '<a href="' + esc(href) + '"' + cls + ">" + esc(label) + "</a>";
  }

  function render(container) {
    var current = container.getAttribute("data-page") || "";
    var searchCurrent = SEARCH_ITEMS.some(function (it) { return it.id === current; });

    var html = "";
    html += linkHtml("home", "홈", "/", current);

    html += '<details class="nav-dd">';
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
    var nodes = document.querySelectorAll("#topNav[data-page]");
    for (var i = 0; i < nodes.length; i++) render(nodes[i]);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : this);
