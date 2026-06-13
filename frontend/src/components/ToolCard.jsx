import { useMemo, useState } from "react";
import { lineDiff } from "../lib/diff.js";
import { post } from "../api.js";

/** Open a filesystem path in VS Code via the backend launcher (best-effort). */
function openInVscode(path) {
  if (!path) return;
  post("/api/open-in-vscode", { path }).catch(() => {});
}

/**
 * Render an agent tool action as a compact, human-friendly card.
 *
 * The raw tool name is never shown. Instead each action becomes a readable line
 * (e.g. "Read settings.json", "Edited app.py  +4 −1") with an optional expandable
 * panel: a side-by-side diff for writes/edits, the contents for a new file, an MCP
 * call's input→output, or a note for a deletion. File paths can be opened in VS Code.
 *
 * @param {{tool: {name: string, args: object, ok?: boolean, summary?: string,
 *   meta?: object, pending?: boolean}}} props
 */
export default function ToolCard({ tool }) {
  const [open, setOpen] = useState(false);
  const { name = "", args = {}, ok, summary, meta, pending } = tool || {};

  // Derive the display label, icon, an optional inline detail, and what the
  // expandable panel (if any) should contain.
  const view = useMemo(() => describe(name, args, meta), [name, args, meta]);

  const diff = useMemo(() => {
    if (!view.diff) return null;
    return lineDiff(meta?.old || "", meta?.new || "");
  }, [view.diff, meta]);

  const hasPanel = view.diff || view.newFile != null || view.io != null;
  const statusClass = pending ? "pending" : ok === false ? "bad" : "good";

  return (
    <div className={"toolcard " + statusClass}>
      <button
        className="toolcard-head"
        disabled={!hasPanel}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="toolcard-icon">{view.icon}</span>
        <span className="toolcard-label">
          {view.label}
          {view.target && <code className="toolcard-target">{view.target}</code>}
          {view.detail && <span className="toolcard-detail">{view.detail}</span>}
        </span>
        {diff && (
          <span className="toolcard-counts">
            {diff.added > 0 && <span className="add">+{diff.added}</span>}
            {diff.removed > 0 && <span className="del">−{diff.removed}</span>}
          </span>
        )}
        {pending && <span className="spinner" />}
        {ok === false && !pending && <span className="toolcard-err">failed</span>}
        {hasPanel && <span className="toolcard-chevron">{open ? "▾" : "▸"}</span>}
      </button>

      {view.openPath && (
        <button className="toolcard-open" title="Open in VS Code"
          onClick={(e) => { e.stopPropagation(); openInVscode(view.openPath); }}>
          Open in VS Code
        </button>
      )}

      {open && hasPanel && (
        <div className="toolcard-body">
          {view.diff && diff && <DiffView rows={diff.rows} />}
          {view.newFile != null && (
            <pre className="toolcard-newfile">{view.newFile || "(empty file)"}</pre>
          )}
          {view.io != null && <IoView input={view.io.input} output={view.io.output} />}
        </div>
      )}

      {ok === false && !pending && summary && (
        <div className="toolcard-error">{summary}</div>
      )}
    </div>
  );
}

/** Side-by-side old/new diff table. */
function DiffView({ rows }) {
  return (
    <div className="diff">
      {rows.map((r, i) => (
        <div className={"diff-row " + r.type} key={i}>
          <span className="diff-gutter">{r.ln ?? ""}</span>
          <span className="diff-cell left">{r.type !== "add" ? r.left : ""}</span>
          <span className="diff-gutter">{r.rn ?? ""}</span>
          <span className="diff-cell right">{r.type !== "del" ? r.right : ""}</span>
        </div>
      ))}
    </div>
  );
}

/** Side-by-side input→output view for an MCP (or generic) tool call. */
function IoView({ input, output }) {
  const fmt = (v) => {
    if (v == null) return "";
    if (typeof v === "string") return v;
    try { return JSON.stringify(v, null, 2); } catch { return String(v); }
  };
  return (
    <div className="io">
      <div className="io-col">
        <div className="io-head">Input</div>
        <pre className="io-body">{fmt(input) || "(no input)"}</pre>
      </div>
      <div className="io-col">
        <div className="io-head">Output</div>
        <pre className="io-body">{fmt(output) || "(no output)"}</pre>
      </div>
    </div>
  );
}

/** Short base name for a path, tolerating both Windows and POSIX separators. */
function baseName(p) {
  if (!p) return "";
  const parts = String(p).split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

/**
 * Map a tool name + args + meta to a presentation descriptor.
 * Returns { icon, label, target?, detail?, diff?, newFile?, io?, openPath? }.
 */
function describe(name, args, meta) {
  const file = meta?.name || baseName(args?.path);
  const fullPath = meta?.path || args?.path || null;

  // MCP tools are namespaced "server__tool" (and carry action "mcp" meta).
  if (meta?.action === "mcp" || name.includes("__")) {
    const server = meta?.server || name.split("__")[0];
    const tool = meta?.tool || name.split("__").slice(1).join("__");
    return {
      icon: "🧩", label: "Used", target: `${server} · ${tool}`,
      io: { input: meta?.input ?? args, output: meta?.output },
    };
  }

  switch (name) {
    case "read_file": {
      const lo = args?.start_line, hi = args?.end_line;
      const detail = lo || hi ? ` lines ${lo ?? 1}–${hi ?? "end"}` : "";
      return { icon: "📄", label: "Read", target: file, detail, openPath: fullPath };
    }
    case "list_dir":
      return {
        icon: "📂", label: "Listed", target: file ? file + "/" : "folder",
        detail: meta?.count != null ? ` ${meta.count} items` : "", openPath: fullPath,
      };
    case "find":
      return {
        icon: "🔎", label: "Searched files",
        target: args?.pattern || args?.name || args?.path || "",
      };
    case "grep":
      return { icon: "🔎", label: "Searched for", target: args?.pattern || args?.query || "" };
    case "write_file": {
      if (meta?.action === "create" || (meta && !meta.old)) {
        return { icon: "✨", label: "Created", target: file, openPath: fullPath,
          newFile: meta?.new ?? args?.content ?? "" };
      }
      return { icon: "✏️", label: "Wrote", target: file, diff: true, openPath: fullPath };
    }
    case "edit":
      return { icon: "✏️", label: "Edited", target: file, diff: true, openPath: fullPath };
    case "powershell":
      return { icon: "⌨️", label: "Ran", target: args?.command || args?.script || "command" };
    case "parallel": {
      const n = Array.isArray(args?.calls) ? args.calls.length : null;
      return { icon: "⚡", label: n ? `Ran ${n} parallel operations` : "Ran parallel operations" };
    }
    default:
      break;
  }

  // Generic / deletion fallbacks.
  if (meta?.action === "delete") return { icon: "🗑️", label: "Deleted", target: file };
  return { icon: "🔧", label: name || "Tool" };
}
