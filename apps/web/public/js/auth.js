/** Shared auth helper — Bearer token in localStorage (set by login.html). */
(function (global) {
  "use strict";
  var KEY = "citec_kb_token";

  function getToken() {
    try {
      return localStorage.getItem(KEY) || "";
    } catch (e) {
      return "";
    }
  }

  function setToken(t) {
    try {
      if (t) localStorage.setItem(KEY, t);
      else localStorage.removeItem(KEY);
    } catch (e) {
      /* ignore */
    }
  }

  function clear() {
    setToken("");
  }

  function authHeaders(extra) {
    var h = {};
    var k;
    if (extra) {
      for (k in extra) {
        if (Object.prototype.hasOwnProperty.call(extra, k)) h[k] = extra[k];
      }
    }
    var t = getToken();
    if (t) h["Authorization"] = "Bearer " + t;
    return h;
  }

  function apiFetch(url, opts) {
    opts = opts || {};
    var headers = authHeaders(opts.headers || {});
    var body = opts.body;
    if (
      body != null &&
      typeof body === "object" &&
      !(body instanceof FormData) &&
      !(typeof Blob !== "undefined" && body instanceof Blob)
    ) {
      if (!headers["Content-Type"] && !headers["content-type"]) {
        headers["Content-Type"] = "application/json";
      }
      body = JSON.stringify(body);
    }
    var next = {};
    var k;
    for (k in opts) {
      if (Object.prototype.hasOwnProperty.call(opts, k)) next[k] = opts[k];
    }
    next.headers = headers;
    next.body = body;
    return fetch(url, next);
  }

  function me() {
    return apiFetch("/v1/auth/me").then(function (r) {
      return r
        .json()
        .catch(function () {
          return {};
        })
        .then(function (d) {
          return { ok: r.ok, status: r.status, data: d };
        });
    });
  }

  function status() {
    return fetch("/v1/auth/status").then(function (r) {
      return r.json();
    });
  }

  /** Inject a small auth chip into .top nav if present. */
  function mountChip(selector) {
    var top = document.querySelector(selector || ".top");
    if (!top || top.querySelector("[data-citec-auth-chip]")) return;
    var el = document.createElement("span");
    el.setAttribute("data-citec-auth-chip", "1");
    el.style.cssText =
      "margin-left:auto;font-size:12px;color:#64748b;display:flex;gap:8px;align-items:center;flex-wrap:wrap;";
    el.innerHTML =
      '<a href="/login.html" style="color:#1d4ed8;font-weight:600;text-decoration:none">Login</a><span data-role>—</span>';
    top.appendChild(el);
    me()
      .then(function (res) {
        var p = (res.data && res.data.principal) || {};
        var role = (p.roles || []).join(",") || (res.ok ? "anon" : "?");
        var label = p.sub ? p.sub + " (" + role + ")" : "anonymous";
        var span = el.querySelector("[data-role]");
        if (span) span.textContent = label;
      })
      .catch(function () {});
  }

  var api = {
    KEY: KEY,
    getToken: getToken,
    setToken: setToken,
    clear: clear,
    authHeaders: authHeaders,
    apiFetch: apiFetch,
    me: me,
    status: status,
    mountChip: mountChip,
  };
  global.CitecAuth = api;
})(typeof window !== "undefined" ? window : this);
