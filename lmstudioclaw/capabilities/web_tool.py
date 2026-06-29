"""First-party `fetch_url` tool (web fetch with clean text extraction).

Small local models tend to misuse `powershell`+`Invoke-WebRequest` for web reading:
they re-download the same page repeatedly and pull the **entire raw HTML** into
context, then run one regex per turn to pick out a single field. That floods the
token budget and stalls the run (see the audit-log loop that motivated this tool).

`fetch_url` fixes both problems with a single, purpose-built call:

* one HTTP GET (httpx, follow redirects, bounded timeout + response size);
* the HTML is reduced to **readable text** — `<script>`/`<style>`/comments removed,
  tags stripped, entities decoded, whitespace collapsed — so the model gets prose,
  not markup;
* the page ``<title>`` and the in-page links (href + anchor text) are extracted
  separately so the agent does not have to regex them out of raw HTML;
* output is truncated to a sane cap so a huge page cannot flood the transcript.

It is network-only (no filesystem access), so it needs no consent gate. The handler
is dependency-free (no BeautifulSoup) to honour the project's lean-dependency rule.
"""

from __future__ import annotations

import html
import re

import httpx

from .registry import ToolResult

# Bound the request and what we feed back into the model context.
_TIMEOUT = 30.0           # seconds for the whole request
_MAX_BYTES = 3_000_000    # stop reading a response past ~3 MB
_MAX_TEXT = 12_000        # chars of cleaned HTML text returned to the model
# Clean formats (markdown/plain text/JSON) are the real payload — not noisy markup —
# so allow much more of them through before truncating (e.g. an llms.txt / agents.md).
_MAX_TEXT_PLAIN = 60_000
_MAX_LINKS = 60           # most relevant links surfaced

# Browser-ish UA so sites that gate on a missing UA still respond.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) LMStudioClaw/1.0"

# Blocks whose *content* must be dropped before turning HTML into text.
_DROP_BLOCKS = re.compile(r"(?is)<(script|style|noscript|template|svg)\b.*?</\1>")
_COMMENTS = re.compile(r"(?s)<!--.*?-->")
_TAG = re.compile(r"(?s)<[^>]+>")
_WS_RUNS = re.compile(r"[ \t]+")
_BLANK_RUNS = re.compile(r"\n\s*\n\s*\n+")
_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
# href + visible anchor text (text may contain nested tags, which we strip after).
_ANCHOR = re.compile(r'(?is)<a\b[^>]*?href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>')


def _clean_text(body: str) -> str:
    """Reduce an HTML document to readable, whitespace-collapsed plain text."""
    body = _DROP_BLOCKS.sub(" ", body)
    body = _COMMENTS.sub(" ", body)
    # Turn common block boundaries into newlines so structure survives tag removal.
    body = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\s*>", "\n", body)
    body = _TAG.sub(" ", body)
    body = html.unescape(body)
    # Collapse horizontal runs, then squeeze excessive blank lines.
    body = _WS_RUNS.sub(" ", body)
    body = "\n".join(line.strip() for line in body.splitlines())
    body = _BLANK_RUNS.sub("\n\n", body)
    return body.strip()


def _extract_links(body: str) -> list[str]:
    """Return up to ``_MAX_LINKS`` unique ``url — anchor text`` pairs from the HTML."""
    out: list[str] = []
    seen: set[str] = set()
    for href, text in _ANCHOR.findall(body):
        href = href.strip()
        # Skip in-page anchors and javascript: pseudo-links — they carry no info.
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        label = html.unescape(_TAG.sub(" ", text)).strip()
        label = _WS_RUNS.sub(" ", label)
        key = f"{href}\u0000{label}"
        if key in seen:
            continue
        seen.add(key)
        out.append(f"{href} — {label}" if label else href)
        if len(out) >= _MAX_LINKS:
            break
    return out


async def fetch_url(*, url: str) -> ToolResult:
    """Fetch ``url`` and return its title, cleaned text, and links (token-frugal)."""
    if not isinstance(url, str) or not url.strip():
        return ToolResult(False, "", error="fetch_url requires a non-empty 'url'.")
    target = url.strip()
    if not re.match(r"(?i)^https?://", target):
        target = "https://" + target

    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_TIMEOUT, headers={"User-Agent": _UA}
        ) as client:
            resp = await client.get(target)
    except httpx.HTTPError as exc:
        return ToolResult(False, "", error=f"Could not fetch {target}: {exc}")

    raw = resp.content[:_MAX_BYTES]
    ctype = resp.headers.get("content-type", "")
    final_url = str(resp.url)

    # Non-HTML (JSON/text/markdown/etc.): return the body as-is (clean payload), with
    # a generous cap so docs like agents.md / llms.txt come through whole.
    if "html" not in ctype.lower():
        text = raw.decode(resp.encoding or "utf-8", errors="replace")
        truncated = text[:_MAX_TEXT_PLAIN]
        note = "" if len(text) <= _MAX_TEXT_PLAIN else "\n…[truncated]"
        meta = {"action": "fetch", "url": final_url, "content_type": ctype,
                "status": resp.status_code}
        return ToolResult(resp.is_success, f"{final_url} ({resp.status_code})\n\n{truncated}{note}",
                          error=None if resp.is_success else f"HTTP {resp.status_code}", meta=meta)

    body = raw.decode(resp.encoding or "utf-8", errors="replace")
    title_m = _TITLE.search(body)
    title = html.unescape(_TAG.sub("", title_m.group(1)).strip()) if title_m else ""
    text = _clean_text(body)
    links = _extract_links(body)

    truncated = text[:_MAX_TEXT]
    text_note = "" if len(text) <= _MAX_TEXT else "\n…[text truncated]"

    parts = [f"URL: {final_url} (HTTP {resp.status_code})"]
    if title:
        parts.append(f"Title: {title}")
    parts.append(f"\n{truncated}{text_note}")
    if links:
        parts.append("\nLinks:\n" + "\n".join(links))
    output = "\n".join(parts)

    meta = {"action": "fetch", "url": final_url, "title": title,
            "status": resp.status_code, "links": len(links)}
    return ToolResult(resp.is_success, output,
                      error=None if resp.is_success else f"HTTP {resp.status_code}", meta=meta)
