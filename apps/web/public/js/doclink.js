/**
 * Shared helpers: always offer a link to full document body (원문).
 * Usage:
 *   CitecDoc.href({ external_id, source_type })
 *   CitecDoc.linkHtml(doc, { label: "원문" })
 *   CitecDoc.open(doc)  // navigate
 */
(function (global) {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function pick(doc) {
    doc = doc || {};
    var eid =
      doc.external_id ||
      doc.externalId ||
      doc.code ||
      doc.id ||
      doc.slug ||
      "";
    var st =
      doc.source_type ||
      doc.sourceType ||
      doc.section ||
      "support_history";
    var path = doc.path || "";
    if (!path && eid) {
      var e = String(eid);
      path = st + "/" + (e.endsWith(".md") ? e : e + ".md");
    }
    return {
      external_id: String(eid || ""),
      source_type: String(st || "support_history"),
      path: String(path || ""),
      title: doc.title || doc.query || eid || "",
    };
  }

  function href(doc) {
    var p = pick(doc);
    var q = new URLSearchParams();
    if (p.external_id) q.set("eid", p.external_id);
    if (p.source_type) q.set("st", p.source_type);
    if (p.path) q.set("path", p.path);
    if (p.title) q.set("title", p.title);
    return "/doc.html?" + q.toString();
  }

  /**
   * Inline HTML: text link + optional expand button attrs for host page.
   * @param {object} doc
   * @param {{label?: string, className?: string, newTab?: boolean}} opts
   */
  function linkHtml(doc, opts) {
    opts = opts || {};
    var p = pick(doc);
    if (!p.external_id && !p.path) return "";
    var label = opts.label || "원문";
    var cls = opts.className || "doc-link";
    var target = opts.newTab === false ? "" : ' target="_blank" rel="noopener"';
    return (
      '<a class="' +
      esc(cls) +
      '" href="' +
      esc(href(p)) +
      '"' +
      target +
      ' title="원문 전체 보기">' +
      esc(label) +
      "</a>"
    );
  }

  /** Compact badge used next to titles */
  function badgeHtml(doc, opts) {
    opts = opts || {};
    opts.label = opts.label || "원문 보기";
    opts.className = opts.className || "doc-link-badge";
    return linkHtml(doc, opts);
  }

  function open(doc) {
    global.location.href = href(doc);
  }

  global.CitecDoc = {
    pick: pick,
    href: href,
    linkHtml: linkHtml,
    badgeHtml: badgeHtml,
    open: open,
    esc: esc,
  };
})(typeof window !== "undefined" ? window : this);
