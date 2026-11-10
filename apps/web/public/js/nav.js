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
    html += linkHtml("decisions", "사람 확정", "/decisions.html", current);
    html += linkHtml("doc", "문서", "/docs/", current);
    html += '<a href="/api/docs" target="_blank" rel="noopener">API</a>';
    html += '<span class="nav-admin">' + linkHtml("admin", "Admin", "/admin.html", current) + "</span>";

    container.innerHTML = html;
  }

  // AUTH_MODE 가 꺼져 있으면(/v1/auth/me 가 모든 롤의 익명 사용자를 돌려줌) 서버는 원본을 절대 주지 않는다.
  // 그때 토글을 보이면 거짓 안내 띠가 뜨고 모든 호출이 거부 감사 행을 남기므로, 인증이 켜져 있을 때만 보인다.
  var AUTH_OFF = ["", "off", "disabled", "none", "false", "0"];

  function authEnforced(st) {
    var mode = String((st && st.auth_mode) == null ? "" : st.auth_mode).trim().toLowerCase();
    return AUTH_OFF.indexOf(mode) < 0;
  }

  function mountUnmask(container) {
    var A = global.CitecAuth;
    if (!A || !A.me || !A.setUnmask || !A.status) return;
    Promise.all([A.status(), A.me()]).then(function (res) {
      var st = res[0] || {};
      var me = res[1] || {};
      var roles = ((me.data && me.data.principal) || {}).roles || [];
      var allowed = authEnforced(st) && (roles.indexOf("senior") >= 0 || roles.indexOf("admin") >= 0);
      if (!allowed) {
        A.setUnmask(false);
        return;
      }
      var label = document.createElement("label");
      label.className = "unmask-toggle";
      label.innerHTML = '<input type="checkbox"> 원본 보기';
      var box = label.querySelector("input");
      box.checked = A.unmaskOn();
      box.addEventListener("change", function () {
        A.setUnmask(box.checked);
        location.reload();
      });
      container.appendChild(label);
      if (A.unmaskOn()) {
        var bar = document.createElement("div");
        bar.className = "unmask-banner";
        bar.textContent = "원본 표시 중 · 열람이 기록됩니다";
        document.body.insertBefore(bar, document.body.firstChild);
      }
    }).catch(function () {
      A.setUnmask(false);
    });
  }

  function init() {
    var nodes = document.querySelectorAll("#topNav[data-page]");
    for (var i = 0; i < nodes.length; i++) { render(nodes[i]); mountUnmask(nodes[i]); }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(typeof window !== "undefined" ? window : this);
