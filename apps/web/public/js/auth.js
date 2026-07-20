/** Shared auth helper — Bearer token in localStorage (set by login.html). */
(function (global) {
  const KEY = "citec_kb_token";

  function getToken() {
    try {
      return localStorage.getItem(KEY) || "";
    } catch (_) {
      return "";
    }
  }

  function setToken(t) {
    try {
      if (t) localStorage.setItem(KEY, t);
      else localStorage.removeItem(KEY);
    } catch (_) {
      /* ignore */
    }
  }

  function clear() {
    setToken("");
  }

  function authHeaders(extra) {
    const h = Object ...(extra || {}) };
    const t = getToken();
    if (t) h["Authorization"] = "Bearer " + t;
    return h;
  }

  async function apiFetch(url, opts) {
    opts = opts || {};
    const headers = authHeaders(opts.headers || {});
    let body = opts.body;
    if (body != null && typeof body === "object" && !(body instanceof FormData) && !(body instanceof Blob)) {
      if (!headers["Content-Type"] && !headers["content-type"]) {
        headers["Content-Type"] = "application/json";
      }
      body = JSON.stringify(body);
    }
    const r = await fetch(url, { ...opts, headers, body });
    return r;
  }

  async function me() {
    const r = await apiFetch("/v1/auth/me");
    const d = await r.json().catch(() => ({}));
    return { ok: r.ok, status: r.status, data: d };
  }

  async function status() {
    const r = await fetch("/v1/auth/status");
    return r.json();
  }

  /** Inject a small auth chip into .top nav if present. */
  function mountChip(selector) {
    const top = document.querySelector(selector || ".top");
    if (!top || top.querySelector("[data-citec-auth-chip]")) return;
    const el = document.createElement("span");
    el.setAttribute("data-citec-auth-chip", "1");
    el.style.cssText = "margin-left:auto;font-size:12px;color:#64748b;display:flex;gap:8px;align-items:center;flex-wrap:wrap;";
    el.innerHTML = '<a href="/login.html" style="color:#1d4ed8;font-weight:600;text-decoration:none">Login</a><span data-role>—</span>';
    top.appendChild(el);
    me().then(({ ok, data }) => {
      const p = (data && data.principal) || {};
      const role = (p.roles || []).join(",") || (ok ? "anon" : "?");
      const label = p.sub ? p.sub + " (" + role + ")" : "anonymous";
      const span = el.querySelector("[data-role]");
      if (span) span.textContent = label;
    }).catch(() => {});
  }

  global.CitecAuth = {
    KEY,
    getToken,
    setToken,
    clear,
    authHeaders,
    apiFetch,
    me,
    status,
    mountChip,
  };
})(window);
