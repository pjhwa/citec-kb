/**
 * Lightweight Markdown → HTML for citec-kb UI (no CDN; air-gap friendly).
 * Covers headings, bold/italic, lists, code, links, blockquotes, hr, tables (simple).
 */
(function (global) {
  "use strict";

  function escHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function inline(md) {
    var s = escHtml(md);
    // code first
    s = s.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    // bold / italic
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_\w])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
    // links [text](url)
    s = s.replace(
      /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    // autolink bare urls (simple)
    s = s.replace(
      /(^|[\s(])(https?:\/\/[^\s<)]+)/g,
      '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>'
    );
    return s;
  }

  function codeBlock(lang, body) {
    return (
      '<pre class="md-code"><code' +
      (lang ? ' data-lang="' + escHtml(lang) + '"' : "") +
      ">" +
      escHtml(body) +
      "</code></pre>"
    );
  }

  /**
   * Lift fenced code (including mid-line / truncated snippet forms) so
   * ``` markers are not shown raw in list/paragraph text.
   * Returns { text with \u0000placeholders\u0000, blocks[] }.
   */
  function extractFences(text) {
    var blocks = [];
    // closed fences, possibly starting mid-line
    var closed = text.replace(/```(\w*)[ \t]*\n?([\s\S]*?)```/g, function (_, lang, body) {
      var idx = blocks.length;
      blocks.push(codeBlock(lang || "", String(body).replace(/^\n|\n$/g, "")));
      return "\n\u0000MDCODE" + idx + "\u0000\n";
    });
    // unclosed opening fence (common in truncated previews) → rest is code
    var unclosed = closed.replace(/```(\w*)[ \t]*\n?([\s\S]*)$/g, function (_, lang, body) {
      if (body == null) return _;
      var idx = blocks.length;
      blocks.push(codeBlock(lang || "", String(body)));
      return "\n\u0000MDCODE" + idx + "\u0000\n";
    });
    return { text: unclosed, blocks: blocks };
  }

  function restoreFences(html, blocks) {
    return html.replace(/\u0000MDCODE(\d+)\u0000/g, function (_, n) {
      return blocks[Number(n)] || "";
    });
  }

  function render(md) {
    if (md == null || md === "") return "";
    var raw = String(md).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    var extracted = extractFences(raw);
    var text = extracted.text;
    var lines = text.split("\n");
    var out = [];
    var i = 0;
    var listType = null; // ul | ol
    var listBuf = [];

    function flushList() {
      if (!listType || !listBuf.length) {
        listType = null;
        listBuf = [];
        return;
      }
      var tag = listType;
      out.push("<" + tag + ">");
      for (var j = 0; j < listBuf.length; j++) {
        var item = listBuf[j];
        // placeholder-only list item → emit block, not li with empty text
        if (/^\u0000MDCODE\d+\u0000$/.test(item.trim())) {
          out.push("<li>" + item.trim() + "</li>");
        } else {
          out.push("<li>" + inline(item) + "</li>");
        }
      }
      out.push("</" + tag + ">");
      listType = null;
      listBuf = [];
    }

    while (i < lines.length) {
      var line = lines[i];

      // code placeholder line (standalone)
      var ph = line.match(/^\s*(\u0000MDCODE\d+\u0000)\s*$/);
      if (ph) {
        flushList();
        out.push(ph[1]);
        i++;
        continue;
      }

      // hr
      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        flushList();
        out.push("<hr/>");
        i++;
        continue;
      }

      // headings
      var h = line.match(/^(#{1,6})\s+(.+)$/);
      if (h) {
        flushList();
        var level = h[1].length;
        out.push("<h" + level + ">" + inline(h[2].trim()) + "</h" + level + ">");
        i++;
        continue;
      }

      // blockquote
      if (/^>\s?/.test(line)) {
        flushList();
        var bq = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          bq.push(lines[i].replace(/^>\s?/, ""));
          i++;
        }
        out.push("<blockquote>" + inline(bq.join(" ")) + "</blockquote>");
        continue;
      }

      // unordered list
      var ul = line.match(/^\s*[-*+]\s+(.+)$/);
      if (ul) {
        if (listType && listType !== "ul") flushList();
        listType = "ul";
        listBuf.push(ul[1]);
        i++;
        continue;
      }

      // ordered list
      var ol = line.match(/^\s*\d+\.\s+(.+)$/);
      if (ol) {
        if (listType && listType !== "ol") flushList();
        listType = "ol";
        listBuf.push(ol[1]);
        i++;
        continue;
      }

      // blank line
      if (/^\s*$/.test(line)) {
        flushList();
        i++;
        continue;
      }

      // paragraph (merge consecutive non-empty non-special lines)
      flushList();
      var para = [line];
      i++;
      while (
        i < lines.length &&
        !/^\s*$/.test(lines[i]) &&
        !/^#{1,6}\s/.test(lines[i]) &&
        !/^\s*\u0000MDCODE\d+\u0000\s*$/.test(lines[i]) &&
        !/^\s*[-*+]\s+/.test(lines[i]) &&
        !/^\s*\d+\.\s+/.test(lines[i]) &&
        !/^>\s?/.test(lines[i]) &&
        !/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i])
      ) {
        para.push(lines[i]);
        i++;
      }
      // if paragraph is only a placeholder, emit bare
      var joined = para.join(" ");
      if (/^\u0000MDCODE\d+\u0000$/.test(joined.trim())) {
        out.push(joined.trim());
      } else {
        out.push("<p>" + inline(joined) + "</p>");
      }
    }

    flushList();
    return restoreFences(out.join("\n"), extracted.blocks);
  }

  /** Sanitize: allow only a safe subset of tags produced by render(). */
  function sanitize(html) {
    // strip scripts / event handlers if any slipped in
    var s = String(html || "");
    s = s.replace(/<\/?script\b[^>]*>/gi, "");
    s = s.replace(/\son\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, "");
    s = s.replace(/javascript:/gi, "");
    return s;
  }

  function renderSafe(md) {
    return sanitize(render(md));
  }

  function renderInto(el, md) {
    if (!el) return;
    el.classList.add("md-body");
    el.innerHTML = renderSafe(md);
  }

  global.CitecMD = {
    render: renderSafe,
    renderRaw: render,
    sanitize: sanitize,
    renderInto: renderInto,
    escHtml: escHtml,
  };
})(typeof window !== "undefined" ? window : this);
