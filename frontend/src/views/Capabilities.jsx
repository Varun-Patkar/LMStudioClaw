import { useEffect, useState } from "react";
import {
  Blocks, Wrench, Server, Upload, FileText, KeyRound, FileCode, X, ExternalLink,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, patch, del, api } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";

/**
 * Skills & Tools — a two-tab "Customize" surface (Cowork-style):
 *  - Skills: discovered SKILL.md skills + custom Python tools, plus an "add a skill"
 *    panel (write a form, upload a file, or import from a URL; template download).
 *  - MCP & Servers: MCP servers as toggle rows + an add panel (paste JSON with
 *    automatic secret extraction, or fill fields) + the write-only secrets store.
 */
export default function Capabilities() {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState("skills");
  const [preview, setPreview] = useState(null);   // open skill preview (markdown + scripts)
  const toast = useToast();

  const load = () => Promise.all([
    get("/api/capabilities").catch(() => []),
    get("/api/secrets").catch(() => []),
    get("/api/secrets/missing").catch(() => ({ missing: [] })),
  ]).then(([caps, secrets, miss]) => setData({ caps, secrets, missing: miss.missing || [] }));

  useEffect(() => { load(); }, []);
  if (!data) return <Skeleton />;

  const byKind = { skill: [], tool: [], mcp: [] };
  for (const c of data.caps) (byKind[c.kind] || (byKind[c.kind] = [])).push(c);

  // A connect_failed server must never look healthy just because it is enabled.
  const failed = (c) => c.status === "connect_failed";
  const badgeFor = (c) => failed(c) ? "failed" : (c.enabled ? "enabled" : c.status);
  const badgeClass = (c) => failed(c) ? "failed" : (c.status === "valid" ? "active" : c.status);

  const rescan = async () => { try { await post("/api/capabilities/refresh", {}); load(); } catch (e) { toast(e.message); } };
  // Open a skill's preview: its SKILL.md rendered as markdown + the scripts it contains.
  const openSkill = async (c) => {
    try { setPreview(await get(`/api/capabilities/skill/${c.id}`)); }
    catch (e) { toast(e.message); }
  };
  // Open any file (a skill script) in VS Code — contents are never shown in-app.
  const openInCode = async (path) => {
    try { await post("/api/open-in-vscode", { path }); toast("Opening in VS Code…"); }
    catch (e) { toast(e.message); }
  };
  const trust = async (c) => {
    if (!confirm("Custom tools run arbitrary code. Trust this tool?")) return;
    try { await patch(`/api/capabilities/${c.id}`, { trust_confirmed: true }); load(); } catch (e) { toast(e.message); }
  };
  const toggle = async (c) => {
    // Optimistically flip, then reconcile from the server.
    setData((d) => ({ ...d, caps: d.caps.map((x) => x.id === c.id ? { ...x, enabled: !x.enabled } : x) }));
    try { await patch(`/api/capabilities/${c.id}`, { enabled: !c.enabled }); load(); } catch (e) { toast(e.message); load(); }
  };
  const editMcpJson = async () => {
    try {
      const { path } = await get("/api/capabilities/mcp-config-path");
      await post("/api/open-in-vscode", { path });
      toast("Opened mcp.json in VS Code. Save your changes, then Rescan.");
    } catch (e) { toast(e.message); }
  };
  const delMcp = async (name) => {
    if (!confirm(`Delete MCP server “${name}”?`)) return;
    try { await del(`/api/capabilities/mcp/${encodeURIComponent(name)}`); toast("MCP server deleted."); load(); }
    catch (e) { toast(e.message); }
  };
  const addMcp = async (body, reset) => {
    try { await post("/api/capabilities/mcp", body); toast("MCP server added."); reset && reset(); load(); }
    catch (e) { toast(e.message); }
  };
  const importMcp = async (jsonText) => {
    try {
      const r = await post("/api/capabilities/mcp/import", { config: jsonText });
      const secrets = r.secrets?.length ? ` · ${r.secrets.length} secret(s) stored` : "";
      toast(`Added ${(r.added || []).join(", ") || "server"}${secrets}.`);
      load();
      return true;
    } catch (e) { toast(e.message); return false; }
  };
  const createSkill = async (payload) => {
    try { const r = await post("/api/capabilities/skill", payload); toast(`Skill “${r.name}” created.`); load(); return true; }
    catch (e) { toast(e.message); return false; }
  };
  const skillFromUrl = async (url) => {
    try { const r = await post("/api/capabilities/skill/from-url", { url }); toast(`Skill “${r.name}” added.`); load(); return true; }
    catch (e) { toast(e.message); return false; }
  };
  const downloadTemplate = async () => {
    try {
      const t = await get("/api/capabilities/skill/template");
      const blob = new Blob([t.content], { type: "text/markdown" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = t.filename; a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) { toast(e.message); }
  };
  const saveSecret = async (ref, value) => {
    try { await api("PUT", `/api/secrets/${encodeURIComponent(ref)}`, { value }); load(); return true; }
    catch (e) { toast(e.message); return false; }
  };
  const delSecret = async (name) => { try { await del(`/api/secrets/${encodeURIComponent(name)}`); load(); } catch (e) { toast(e.message); } };
  const updateSecret = async (oldRef, newRef, value) => {
    if (!newRef) { toast("A reference name is required."); return false; }
    if (newRef === oldRef && !value) { toast("Enter a new value or a new name."); return false; }
    try {
      await api("PATCH", `/api/secrets/${encodeURIComponent(oldRef)}`, { new_ref: newRef, value: value || null });
      toast("Secret updated."); load(); return true;
    } catch (e) { toast(e.message); return false; }
  };
  const fillMissing = async (ref, value) => {
    if (!value) return toast("Enter a value for " + ref + ".");
    try {
      await api("PUT", `/api/secrets/${encodeURIComponent(ref)}`, { value, owner: "user" });
      toast(`Secret “${ref}” saved. Rescanning…`);
      await post("/api/capabilities/refresh", {});
      load();
    } catch (e) { toast(e.message); }
  };

  return (
    <>
      <div className="view-head">
        <h1>Skills &amp; Tools</h1>
        <span className="sub">Everything the agent can use</span>
        <span className="spacer" />
        <div className="segmented">
          <button className={tab === "skills" ? "on" : ""} onClick={() => setTab("skills")}>Skills</button>
          <button className={tab === "tools" ? "on" : ""} onClick={() => setTab("tools")}>Custom Tools</button>
          <button className={tab === "mcp" ? "on" : ""} onClick={() => setTab("mcp")}>MCP &amp; Servers</button>
        </div>
      </div>

      {tab === "skills" && (
        <>
          <AddSkill onCreate={createSkill} onUrl={skillFromUrl} onTemplate={downloadTemplate} />

          <div className="card">
            <div className="card-head"><h3>Skills</h3><span className="spacer" />
              <button className="btn ghost sm" onClick={rescan}>Rescan</button></div>
            <CapList items={byKind.skill} icon={<Blocks size={18} />} empty="No skills yet — add one above."
              badgeFor={badgeFor} badgeClass={badgeClass} onRowClick={openSkill}
              actions={(c) => (c.status === "valid" || c.status === "disabled") &&
                <input type="checkbox" className="switch" checked={!!c.enabled} onChange={() => toggle(c)} title={c.enabled ? "Enabled" : "Disabled"} />} />
          </div>
        </>
      )}

      {tab === "tools" && (
        <div className="card">
          <div className="card-head"><h3>Custom tools</h3><span className="spacer" />
            <button className="btn ghost sm" onClick={rescan}>Rescan</button></div>
          <p className="muted">Custom Python tools discovered in your home <code>tools/</code> folder.
            They run arbitrary code, so each must be trusted before it can be enabled.</p>
          <CapList items={byKind.tool} icon={<Wrench size={18} />} empty="No custom tools found."
            badgeFor={badgeFor} badgeClass={badgeClass}
            actions={(c) => <>
              {!c.trust_confirmed && <button className="btn amber sm" onClick={() => trust(c)}>Confirm trust</button>}
              {(c.status === "valid" || c.status === "disabled") &&
                <input type="checkbox" className="switch" checked={!!c.enabled} onChange={() => toggle(c)} title={c.enabled ? "Enabled" : "Disabled"} />}
            </>} />
        </div>
      )}

      {tab === "mcp" && (
        <>
          {data.missing.length > 0 && (
            <div className="card warn">
              <div className="card-head"><h3>Secrets needed</h3></div>
              <p className="muted">A configuration references these secrets but their values aren’t
                set yet. Enter each value (write-only — the agent never sees it); the related server
                is re-checked automatically.</p>
              {data.missing.map((ref) => <MissingSecret key={ref} refName={ref} onSave={fillMissing} />)}
            </div>
          )}

          <AddMcp onImport={importMcp} onAdd={addMcp} />

          <div className="card">
            <div className="card-head"><h3>MCP servers</h3><span className="spacer" />
              <button className="btn ghost sm" onClick={rescan}>Rescan</button>
              <button className="btn ghost sm" onClick={editMcpJson}>Edit mcp.json</button></div>
            <CapList items={byKind.mcp} icon={<Server size={18} />} empty="No MCP servers yet — add one above."
              badgeFor={badgeFor} badgeClass={badgeClass}
              actions={(c) => <>
                {failed(c) && <button className="btn ghost sm" onClick={editMcpJson}>Fix in VS Code</button>}
                {(c.status === "valid" || c.status === "disabled") &&
                  <input type="checkbox" className="switch" checked={!!c.enabled} onChange={() => toggle(c)} title={c.enabled ? "Enabled" : "Disabled"} />}
                <button className="btn red sm" onClick={() => delMcp(c.name)}>Delete</button>
              </>} />
          </div>

          <Secrets secrets={data.secrets} onSave={saveSecret} onUpdate={updateSecret} onDelete={delSecret} />
        </>
      )}

      {preview && <SkillPreview skill={preview} onClose={() => setPreview(null)} onOpen={openInCode} />}
    </>
  );
}

/** A list of capability rows (icon · name · description · actions), or an empty note. */
function CapList({ items, icon, empty, actions, badgeFor, badgeClass, onRowClick }) {
  if (!items.length) return <div className="cap-empty">{empty}</div>;
  return (
    <div className="cap-list">
      {items.map((c) => (
        <div className="cap-row" key={c.id}>
          <div className="cap-ico">{icon}</div>
          <div className={"cap-main" + (onRowClick ? " clickable" : "")}
            onClick={onRowClick ? () => onRowClick(c) : undefined}
            title={onRowClick ? "Open preview" : undefined}>
            <div className="cap-name">{c.name}<span className={"badge " + badgeClass(c)}>{badgeFor(c)}</span></div>
            {c.description && <div className="cap-desc" title={c.description}>{c.description}</div>}
          </div>
          <div className="cap-actions">{actions(c)}</div>
        </div>
      ))}
    </div>
  );
}

/**
 * Skill preview modal: renders the SKILL.md as markdown and lists the skill's scripts.
 * Script contents are intentionally not shown — clicking a script opens it in VS Code.
 */
function SkillPreview({ skill, onClose, onOpen }) {
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal skill-preview" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{skill.name}</h3>
          <span className="spacer" />
          <button className="btn ghost sm" onClick={() => onOpen(skill.path)} title="Open the skill folder in VS Code">
            <ExternalLink size={14} /> Open folder</button>
          <button className="icon-btn" onClick={onClose} title="Close"><X size={16} /></button>
        </div>
        {skill.scripts && skill.scripts.length > 0 && (
          <div className="skill-scripts">
            <div className="skill-scripts-head">Scripts <span className="muted">({skill.scripts.length}) — click to open in VS Code</span></div>
            <div className="skill-script-list">
              {skill.scripts.map((s) => (
                <button className="skill-script" key={s.path} onClick={() => onOpen(s.path)} title={s.path}>
                  <FileCode size={14} /> <span className="ss-name">{s.name}</span>
                  <ExternalLink size={12} className="ss-ext" />
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="skill-md markdown">
          {skill.markdown
            ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{skill.markdown}</ReactMarkdown>
            : <p className="muted">This skill has no SKILL.md content.</p>}
        </div>
      </div>
    </div>
  );
}

/** Add-a-skill panel: write a form, upload a SKILL.md, or import from a URL. */
function AddSkill({ onCreate, onUrl, onTemplate }) {
  const toast = useToast();
  const [mode, setMode] = useState("form");
  const [form, setForm] = useState({ name: "", description: "", content: "" });
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  const onFile = (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = async () => { await onCreate({ name: f.name.replace(/\.(md|markdown|txt)$/i, ""), content: String(r.result) }); };
    r.readAsText(f); e.target.value = "";
  };

  return (
    <div className="card">
      <div className="card-head"><h3>Add a skill</h3><span className="spacer" />
        <button className="btn ghost sm" onClick={onTemplate}><FileText size={14} /> Template</button></div>
      <div className="segmented sm">
        <button className={mode === "form" ? "on" : ""} onClick={() => setMode("form")}>Write</button>
        <button className={mode === "upload" ? "on" : ""} onClick={() => setMode("upload")}>Upload</button>
        <button className={mode === "url" ? "on" : ""} onClick={() => setMode("url")}>From URL</button>
      </div>

      {mode === "form" && (
        <>
          <input placeholder="Skill name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="When to call this skill (description the agent matches on)"
            value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <textarea placeholder="Skill contents — what the agent should do when this applies…" style={{ minHeight: 130 }}
            value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} />
          <button className="btn green" disabled={busy} onClick={async () => {
            if (!form.name.trim()) return toast("A skill name is required.");
            setBusy(true);
            const ok = await onCreate(form);
            setBusy(false);
            if (ok) setForm({ name: "", description: "", content: "" });
          }}>{busy ? <><span className="spinner" /> Creating…</> : "Create skill"}</button>
        </>
      )}

      {mode === "upload" && (
        <>
          <p className="muted">Upload a <code>SKILL.md</code> (or <code>.md</code>/<code>.txt</code>) file — it’s installed as a new skill.</p>
          <label className="btn ghost" style={{ cursor: "pointer", alignSelf: "flex-start" }}>
            <Upload size={15} /> Choose file…
            <input type="file" accept=".md,.markdown,.txt" hidden onChange={onFile} />
          </label>
        </>
      )}

      {mode === "url" && (
        <>
          <input placeholder="https://…/SKILL.md" value={url} onChange={(e) => setUrl(e.target.value)} />
          <p className="muted">Point to a hosted skill file. Common SKILL.md formats (YAML front-matter or a leading heading) are detected automatically.</p>
          <button className="btn green" disabled={busy} onClick={async () => {
            if (!url.trim()) return toast("Enter a URL.");
            setBusy(true);
            const ok = await onUrl(url.trim());
            setBusy(false);
            if (ok) setUrl("");
          }}>{busy ? <><span className="spinner" /> Importing…</> : "Import from URL"}</button>
        </>
      )}
    </div>
  );
}

/** Add-an-MCP-server panel: paste standard JSON (auto-secrets) or fill the fields. */
function AddMcp({ onImport, onAdd }) {
  const toast = useToast();
  const [mode, setMode] = useState("json");
  const [jsonText, setJson] = useState("");
  const [mcp, setMcp] = useState({ name: "", type: "stdio", command: "", args: "", url: "", headers: "" });
  const [busy, setBusy] = useState(false);

  const submitFields = () => {
    const name = mcp.name.trim();
    if (!name) return toast("Server name is required.");
    const isHttp = mcp.type === "http" || mcp.type === "sse";
    if (isHttp ? !mcp.url.trim() : !mcp.command.trim())
      return toast(isHttp ? "Provide a URL for the HTTP server." : "Provide a command (stdio).");
    const headers = {};
    for (const line of mcp.headers.split(/\n+/)) {
      const idx = line.indexOf(":");
      if (idx > 0) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    const body = isHttp
      ? { name, type: mcp.type, url: mcp.url.trim(), headers: Object.keys(headers).length ? headers : null }
      : { name, command: mcp.command.trim() || null, args: mcp.args.trim() ? mcp.args.trim().split(/\s+/) : null };
    onAdd(body, () => setMcp({ name: "", type: "stdio", command: "", args: "", url: "", headers: "" }));
  };

  return (
    <div className="card">
      <div className="card-head"><h3>Add an MCP server</h3></div>
      <div className="segmented sm">
        <button className={mode === "json" ? "on" : ""} onClick={() => setMode("json")}>Paste JSON</button>
        <button className={mode === "fields" ? "on" : ""} onClick={() => setMode("fields")}>Fill fields</button>
      </div>

      {mode === "json" ? (
        <>
          <textarea style={{ minHeight: 150, fontFamily: '"Cascadia Code", "Consolas", monospace', fontSize: 12.5 }}
            placeholder={'{\n  "mcpServers": {\n    "my-server": {\n      "command": "npx",\n      "args": ["-y", "some-mcp"],\n      "env": { "API_KEY": "sk-..." }\n    }\n  }\n}'}
            value={jsonText} onChange={(e) => setJson(e.target.value)} />
          <p className="muted">Paste standard MCP JSON. Any token / key / authorization value in
            <code> env</code> or <code>headers</code> is moved into a write-only secret automatically.</p>
          <button className="btn green" disabled={busy} onClick={async () => {
            if (!jsonText.trim()) return toast("Paste some JSON.");
            setBusy(true);
            const ok = await onImport(jsonText);
            setBusy(false);
            if (ok) setJson("");
          }}>{busy ? <><span className="spinner" /> Importing…</> : "Import"}</button>
        </>
      ) : (
        <>
          <div className="row wrap">
            <input placeholder="Server name" value={mcp.name} onChange={(e) => setMcp({ ...mcp, name: e.target.value })} />
            <select value={mcp.type} onChange={(e) => setMcp({ ...mcp, type: e.target.value })}>
              <option value="stdio">stdio (command)</option>
              <option value="http">HTTP (streamable)</option>
              <option value="sse">HTTP (SSE)</option>
            </select>
          </div>
          {mcp.type === "stdio" ? (
            <div className="row wrap">
              <input placeholder="Command e.g. npx" value={mcp.command} onChange={(e) => setMcp({ ...mcp, command: e.target.value })} />
              <input placeholder="Args (space-separated)" value={mcp.args} onChange={(e) => setMcp({ ...mcp, args: e.target.value })} />
            </div>
          ) : (
            <>
              <input placeholder="URL e.g. https://host/mcp" value={mcp.url} onChange={(e) => setMcp({ ...mcp, url: e.target.value })} />
              <textarea placeholder="Auth headers, one per line e.g. Authorization: Bearer KEY" rows={2}
                value={mcp.headers} onChange={(e) => setMcp({ ...mcp, headers: e.target.value })} />
            </>
          )}
          <button className="btn green" onClick={submitFields}>Add MCP server</button>
        </>
      )}
    </div>
  );
}

/** Write-only secrets store (values never returned). */
function Secrets({ secrets, onSave, onUpdate, onDelete }) {
  const toast = useToast();
  const [draft, setDraft] = useState({ ref: "", value: "" });
  return (
    <div className="card">
      <div className="card-head"><h3>Secrets</h3></div>
      <p className="muted">Values are write-only and never shown — the agent cannot read them.
        Reference one anywhere as <code>${"{"}secret:REF{"}"}</code>.</p>
      {secrets.length > 0 && (
        <table>
          <thead><tr><th>Reference</th><th>Owner</th><th /></tr></thead>
          <tbody>{secrets.map((s) => <SecretRow key={s.ref_name} s={s} onUpdate={onUpdate} onDelete={onDelete} />)}</tbody>
        </table>
      )}
      <div className="row wrap">
        <input placeholder="Reference name" value={draft.ref} onChange={(e) => setDraft({ ...draft, ref: e.target.value })} />
        <input type="password" placeholder="Secret value (write-only)" value={draft.value} onChange={(e) => setDraft({ ...draft, value: e.target.value })} />
        <button className="btn green" onClick={async () => {
          if (!draft.ref.trim()) return toast("A reference name is required.");
          if (!draft.value) return toast("A secret value is required.");
          const ok = await onSave(draft.ref.trim(), draft.value);
          if (ok) setDraft({ ref: "", value: "" });
        }}><KeyRound size={14} /> Save secret</button>
      </div>
    </div>
  );
}

/** Inline input to provide the value for a referenced-but-unset secret. */
function MissingSecret({ refName, onSave }) {
  const [value, setValue] = useState("");
  return (
    <div className="row wrap">
      <code className="secret-ref">{refName}</code>
      <input type="password" placeholder={`Value for ${refName} (write-only)`}
        value={value} onChange={(e) => setValue(e.target.value)} />
      <button className="btn green" onClick={() => onSave(refName, value)}>Save</button>
    </div>
  );
}

/** A secret row editable in place: rename and/or replace its (write-only) value. */
function SecretRow({ s, onUpdate, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(s.ref_name);
  const [value, setValue] = useState("");

  if (!editing) {
    return (
      <tr>
        <td>{s.ref_name}</td>
        <td>{s.owner}</td>
        <td>
          <div className="row">
            <button className="btn ghost sm" onClick={() => { setName(s.ref_name); setValue(""); setEditing(true); }}>Edit</button>
            <button className="btn red sm" onClick={() => onDelete(s.ref_name)}>Delete</button>
          </div>
        </td>
      </tr>
    );
  }
  return (
    <tr>
      <td><input value={name} onChange={(e) => setName(e.target.value)} placeholder="Reference name" /></td>
      <td>{s.owner}</td>
      <td>
        <div className="row wrap">
          <input type="password" value={value} onChange={(e) => setValue(e.target.value)} placeholder="New value (blank = keep)" />
          <button className="btn green sm" onClick={async () => {
            const ok = await onUpdate(s.ref_name, name.trim(), value);
            if (ok) setEditing(false);
          }}>Save</button>
          <button className="btn ghost sm" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </td>
    </tr>
  );
}
