// Minimal, dependency-free, XSS-safe Markdown → HTML renderer for chat messages.
//
// We deliberately avoid a CDN library: the control panel runs fully offline, so a
// network-loaded markdown lib would fail. This renderer escapes all HTML first, then
// applies a safe subset of Markdown (headings, bold, italic, inline + fenced code,
// links, unordered/ordered lists, blockquotes, paragraphs). Only http(s) links are
// allowed; everything else stays as escaped text.

/** Escape HTML special characters so raw model output can never inject markup. */
function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/** Apply inline formatting (code, bold, italic, links) to an already-escaped line. */
function inline(text) {
  // Inline code first so its contents aren't further formatted.
  text = text.replace(/`([^`]+)`/g, (_, code) => `<code>${code}</code>`);
  // Bold then italic.
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  text = text.replace(/(^|[^_])_([^_]+)_/g, "$1<em>$2</em>");
  // Links [text](http...) — only http/https schemes are permitted.
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, href) => `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`);
  return text;
}

/**
 * Render a Markdown string to a safe HTML string.
 * @param {string} src  the raw markdown (e.g. an assistant message)
 * @returns {string} sanitized HTML
 */
export function renderMarkdown(src) {
  const lines = escapeHtml(src || "").split("\n");
  const out = [];
  let inCode = false, codeBuf = [];
  let listType = null, listBuf = [];

  const flushList = () => {
    if (listType) {
      out.push(`<${listType}>${listBuf.join("")}</${listType}>`);
      listType = null; listBuf = [];
    }
  };

  for (const raw of lines) {
    const line = raw;
    // Fenced code blocks.
    const fence = line.match(/^```(.*)$/);
    if (fence) {
      if (inCode) { out.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`); inCode = false; codeBuf = []; }
      else { flushList(); inCode = true; }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    // Headings.
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { flushList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); continue; }

    // Blockquote.
    const q = line.match(/^>\s?(.*)$/);
    if (q) { flushList(); out.push(`<blockquote>${inline(q[1])}</blockquote>`); continue; }

    // Unordered list item.
    const ul = line.match(/^\s*[-*]\s+(.*)$/);
    if (ul) {
      if (listType && listType !== "ul") flushList();
      listType = "ul"; listBuf.push(`<li>${inline(ul[1])}</li>`); continue;
    }
    // Ordered list item.
    const ol = line.match(/^\s*\d+\.\s+(.*)$/);
    if (ol) {
      if (listType && listType !== "ol") flushList();
      listType = "ol"; listBuf.push(`<li>${inline(ol[1])}</li>`); continue;
    }

    // Blank line ends a paragraph/list; non-blank becomes a paragraph.
    if (line.trim() === "") { flushList(); continue; }
    flushList();
    out.push(`<p>${inline(line)}</p>`);
  }
  if (inCode) out.push(`<pre><code>${codeBuf.join("\n")}</code></pre>`);
  flushList();
  return out.join("");
}

/** Create a DOM element whose content is rendered markdown (safe HTML). */
export function markdownEl(className, src) {
  const node = document.createElement("div");
  node.className = className;
  node.innerHTML = renderMarkdown(src);
  return node;
}
