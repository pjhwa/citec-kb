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
      return r.text().then(function (t) {
        if (!t || t.trim().charAt(0) === "<") {
          throw new Error("auth/status 가 HTML을 반환했습니다 (HTTP " + r.status + "). API가 내려갔거나 프록시 오류입니다.");
        }
        return JSON.parse(t);
      });
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

  // ---- 원본 보기 토글 (senior/admin 전용 UI). 상태는 탭 단위 sessionStorage. ----
  // 이 토글은 UI/헤더 힌트일 뿐이며, 원본 허용 여부는 서버가 롤로 결정한다.
  var UNMASK_KEY = "citec_kb_unmask";

  function unmaskOn() {
    try {
      return sessionStorage.getItem(UNMASK_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function setUnmask(on) {
    try {
      if (on) sessionStorage.setItem(UNMASK_KEY, "1");
      else sessionStorage.removeItem(UNMASK_KEY);
    } catch (e) {
      /* ignore */
    }
  }

  // 모든 페이지의 fetch(/v1|/api …)에 헤더를 붙인다. 페이지마다 apiFetch를 쓰지 않으므로 전역 래퍼로 처리.
  if (typeof global.fetch === "function" && !global.fetch.__citecUnmask) {
    var origFetch = global.fetch.bind(global);
    var wrapped = function (url, opts) {
      try {
        var u = typeof url === "string" ? url : (url && url.url) || "";
        // 상대 경로(/v1, /api) 또는 현재 origin의 절대 URL에만 붙인다. 외부 호스트·"//host/…"는 제외.
        var origin = (global.location && global.location.origin) || "";
        var path = u.indexOf(origin + "/") === 0 && origin ? u.slice(origin.length) : u;
        if (unmaskOn() && /^\/(v1|api)\//.test(path)) {
          opts = opts || {};
          var h = new Headers(opts.headers || {});
          h.set("X-Citec-Unmask", "1");
          opts.headers = h;
        }
      } catch (e) {
        /* fall through to the plain call */
      }
      return origFetch(url, opts);
    };
    wrapped.__citecUnmask = true;
    global.fetch = wrapped;
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
    unmaskOn: unmaskOn,
    setUnmask: setUnmask,
  };
  global.CitecAuth = api;
})(typeof window !== "undefined" ? window : this);
