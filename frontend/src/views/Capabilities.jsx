import { useEffect, useState } from "react";
import { get, post, patch, del, api } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";

export default function Capabilities() {
  const [data, setData] = useState(null);
  const [mcp, setMcp] = useState({ name: "", type: "stdio", command: "", args: "", url: "", headers: "" });
  const [secret, setSecret] = useState({ ref: "", value: "" });
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

  const rescan = async () => { try { await post("/api/capabilities/refresh", {}); load(); } catch (e) { toast(e.message); } };
  const trust = async (c) => { if (!confirm("Custom tools run arbitrary code. Trust this tool?")) return; try { await patch(`/api/capabilities/${c.id}`, { trust_confirmed: true }); load(); } catch (e) { toast(e.message); } };
  const toggle = async (c) => { try { await patch(`/api/capabilities/${c.id}`, { enabled: !c.enabled }); load(); } catch (e) { toast(e.message); } };
  const editMcpJson = async () => {
    try {
      const { path } = await get("/api/capabilities/mcp-config-path");
      await post("/api/open-in-vscode", { path });
      toast("Opened mcp.json in VS Code. Save your changes, then Rescan.");
    } catch (e) { toast(e.message); }
  };

  // A connect_failed server must never look healthy just because it is enabled.
  const failed = (c) => c.status === "connect_failed";
  const badgeFor = (c) => failed(c) ? "failed" : (c.enabled ? "enabled" : c.status);
  const badgeClass = (c) => failed(c) ? "failed" : (c.status === "valid" ? "active" : c.status);

  const Row = ({ c }) => (
    <tr>
      <td>{c.name}</td>
      <td><span className={"badge " + badgeClass(c)}>{badgeFor(c)}</span></td>
      <td className="muted">{c.description || ""}</td>
      <td>
        <div className="row">
          {c.kind === "tool" && !c.trust_confirmed && <button className="btn amber" onClick={() => trust(c)}>Confirm trust</button>}
          {c.kind === "mcp" && failed(c) && <button className="btn ghost" onClick={editMcpJson}>Fix in VS Code</button>}
          {(c.status === "valid" || c.status === "disabled") && <button className="btn ghost" onClick={() => toggle(c)}>{c.enabled ? "Disable" : "Enable"}</button>}
          {c.kind === "mcp" && <button className="btn red" onClick={() => delMcp(c.name)}>Delete</button>}
        </div>
      </td>
    </tr>
  );

  const Table = ({ items, empty }) => items.length ? (
    <table>
      <thead><tr><th>Name</th><th>Status</th><th>Description</th><th /></tr></thead>
      <tbody>{items.map((c) => <Row c={c} key={c.id} />)}</tbody>
    </table>
  ) : <p className="muted">{empty}</p>;

  const addMcp = async () => {
    const name = mcp.name.trim();
    if (!name) return toast("Server name is required.");
    const isHttp = mcp.type === "http" || mcp.type === "sse";
    if (isHttp ? !mcp.url.trim() : !mcp.command.trim())
      return toast(isHttp ? "Provide a URL for the HTTP server." : "Provide a command (stdio).");
    // Parse "Key: Value" lines into a headers object (auth keys live here).
    const headers = {};
    for (const line of mcp.headers.split(/\n+/)) {
      const idx = line.indexOf(":");
      if (idx > 0) headers[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
    }
    const hasHeaders = Object.keys(headers).length > 0;
    try {
      await post("/api/capabilities/mcp", isHttp ? {
        name, type: mcp.type, url: mcp.url.trim(),
        headers: hasHeaders ? headers : null,
      } : {
        name, command: mcp.command.trim() || null,
        args: mcp.args.trim() ? mcp.args.trim().split(/\s+/) : null,
      });
      setMcp({ name: "", type: "stdio", command: "", args: "", url: "", headers: "" }); load();
    } catch (e) { toast(e.message); }
  };
  const delMcp = async (name) => {
    if (!confirm(`Delete MCP server “${name}”?`)) return;
    try { await del(`/api/capabilities/mcp/${encodeURIComponent(name)}`); toast("MCP server deleted."); load(); }
    catch (e) { toast(e.message); }
  };
  const saveSecret = async () => {
    const ref = secret.ref.trim();
    if (!ref) return toast("A reference name is required.");
    if (!secret.value) return toast("A secret value is required.");
    try { await api("PUT", `/api/secrets/${encodeURIComponent(ref)}`, { value: secret.value }); setSecret({ ref: "", value: "" }); load(); }
    catch (e) { toast(e.message); }
  };
  const delSecret = async (name) => { try { await del(`/api/secrets/${encodeURIComponent(name)}`); load(); } catch (e) { toast(e.message); } };
  // Rename a secret and/or replace its value (blank value keeps the existing one).
  const updateSecret = async (oldRef, newRef, value) => {
    if (!newRef) { toast("A reference name is required."); return false; }
    if (newRef === oldRef && !value) { toast("Enter a new value or a new name."); return false; }
    try {
      await api("PATCH", `/api/secrets/${encodeURIComponent(oldRef)}`,
        { new_ref: newRef, value: value || null });
      toast("Secret updated.");
      load();
      return true;
    } catch (e) { toast(e.message); return false; }
  };
  // Save a value for a secret ref that mcp.json/tools reference but the vault lacks yet.
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
      <div className="card">
        <div className="card-head"><h2>Skills &amp; Tools</h2><span className="spacer" />
          <button className="btn" onClick={rescan}>Rescan</button></div>
      </div>

      {data.missing.length > 0 && (
        <div className="card warn">
          <div className="card-head"><h3>Secrets needed</h3></div>
          <p className="muted">A configuration references these secrets, but their values
            aren’t set yet. Enter each value (write-only — the agent never sees it), then the
            related MCP server is re-checked automatically.</p>
          {data.missing.map((ref) => <MissingSecret key={ref} refName={ref} onSave={fillMissing} />)}
        </div>
      )}

      <div className="card"><div className="card-head"><h3>Skills (SKILL.md)</h3></div><Table items={byKind.skill} empty="None found." /></div>
      <div className="card"><div className="card-head"><h3>Custom tools</h3></div><Table items={byKind.tool} empty="None found." /></div>

      <div className="card">
        <div className="card-head"><h3>MCP servers</h3><span className="spacer" />
          <button className="btn ghost" onClick={editMcpJson}>Edit mcp.json in VS Code</button></div>
        <Table items={byKind.mcp} empty="No MCP servers." />
        <div className="row wrap">
          <input placeholder="Server name" value={mcp.name} onChange={(e) => setMcp({ ...mcp, name: e.target.value })} />
          <select value={mcp.type} onChange={(e) => setMcp({ ...mcp, type: e.target.value })}>
            <option value="stdio">stdio (command)</option>
            <option value="http">HTTP (streamable)</option>
            <option value="sse">HTTP (SSE)</option>
          </select>
          {mcp.type === "stdio" ? (
            <>
              <input placeholder="Command e.g. npx" value={mcp.command} onChange={(e) => setMcp({ ...mcp, command: e.target.value })} />
              <input placeholder="Args (space-separated)" value={mcp.args} onChange={(e) => setMcp({ ...mcp, args: e.target.value })} />
            </>
          ) : (
            <>
              <input placeholder="URL e.g. https://host/mcp" value={mcp.url} onChange={(e) => setMcp({ ...mcp, url: e.target.value })} />
              <textarea placeholder="Auth headers, one per line e.g. Authorization: Bearer KEY" rows={2}
                value={mcp.headers} onChange={(e) => setMcp({ ...mcp, headers: e.target.value })} />
            </>
          )}
          <button className="btn green" onClick={addMcp}>Add MCP server</button>
        </div>
        <p className="muted">For an API key/token, save it under <strong>Secrets</strong> below, then
          reference it in an env or header value as <code>${"{"}secret:REF_NAME{"}"}</code> — it is
          resolved only when the server connects and is never shown. The same secret can be used by
          custom tools (<code>SECRETS</code>) and skills (front-matter <code>secrets:</code>). If a
          server shows <strong>failed</strong>, use <strong>Edit mcp.json in VS Code</strong> to fix
          it, then Rescan.</p>
      </div>

      <div className="card">
        <div className="card-head"><h3>Secrets</h3></div>
        <p className="muted">Values are write-only and never shown. The agent cannot read them.
          You can rename a secret or replace its value with <strong>Edit</strong>.</p>
        {data.secrets.length > 0 && (
          <table>
            <thead><tr><th>Reference</th><th>Owner</th><th /></tr></thead>
            <tbody>{data.secrets.map((s) => (
              <SecretRow key={s.ref_name} s={s} onUpdate={updateSecret} onDelete={delSecret} />
            ))}</tbody>
          </table>
        )}
        <div className="row wrap">
          <input placeholder="Reference name" value={secret.ref} onChange={(e) => setSecret({ ...secret, ref: e.target.value })} />
          <input type="password" placeholder="Secret value (write-only)" value={secret.value} onChange={(e) => setSecret({ ...secret, value: e.target.value })} />
          <button className="btn green" onClick={saveSecret}>Save secret</button>
        </div>
      </div>
    </>
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

/**
 * A secret row that can be edited in place: rename the reference and/or replace the
 * (write-only) value. Leaving the value blank keeps the existing one.
 */
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
            <button className="btn ghost" onClick={() => { setName(s.ref_name); setValue(""); setEditing(true); }}>Edit</button>
            <button className="btn red" onClick={() => onDelete(s.ref_name)}>Delete</button>
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
          <input type="password" value={value} onChange={(e) => setValue(e.target.value)}
            placeholder="New value (blank = keep)" />
          <button className="btn green" onClick={async () => {
            const ok = await onUpdate(s.ref_name, name.trim(), value);
            if (ok) setEditing(false);
          }}>Save</button>
          <button className="btn ghost" onClick={() => setEditing(false)}>Cancel</button>
        </div>
      </td>
    </tr>
  );
}
