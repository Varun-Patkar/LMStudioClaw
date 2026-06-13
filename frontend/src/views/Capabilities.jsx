import { useEffect, useState } from "react";
import { get, post, patch, del, api } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";

export default function Capabilities() {
  const [data, setData] = useState(null);
  const [mcp, setMcp] = useState({ name: "", command: "", args: "", url: "" });
  const [secret, setSecret] = useState({ ref: "", value: "" });
  const toast = useToast();

  const load = () => Promise.all([
    get("/api/capabilities").catch(() => []),
    get("/api/secrets").catch(() => []),
  ]).then(([caps, secrets]) => setData({ caps, secrets }));

  useEffect(() => { load(); }, []);
  if (!data) return <Skeleton />;

  const byKind = { skill: [], tool: [], mcp: [] };
  for (const c of data.caps) (byKind[c.kind] || (byKind[c.kind] = [])).push(c);

  const rescan = async () => { try { await post("/api/capabilities/refresh", {}); load(); } catch (e) { toast(e.message); } };
  const trust = async (c) => { if (!confirm("Custom tools run arbitrary code. Trust this tool?")) return; try { await patch(`/api/capabilities/${c.id}`, { trust_confirmed: true }); load(); } catch (e) { toast(e.message); } };
  const toggle = async (c) => { try { await patch(`/api/capabilities/${c.id}`, { enabled: !c.enabled }); load(); } catch (e) { toast(e.message); } };

  const Row = ({ c }) => (
    <tr>
      <td>{c.name}</td>
      <td><span className={"badge " + (c.status === "valid" ? "active" : c.status)}>{c.enabled ? "enabled" : c.status}</span></td>
      <td className="muted">{c.description || ""}</td>
      <td>
        <div className="row">
          {c.kind === "tool" && !c.trust_confirmed && <button className="btn amber" onClick={() => trust(c)}>Confirm trust</button>}
          {(c.status === "valid" || c.status === "disabled") && <button className="btn ghost" onClick={() => toggle(c)}>{c.enabled ? "Disable" : "Enable"}</button>}
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
    try {
      await post("/api/capabilities/mcp", {
        name: mcp.name.trim(), command: mcp.command.trim() || null,
        args: mcp.args.trim() ? mcp.args.trim().split(/\s+/) : null, url: mcp.url.trim() || null,
      });
      setMcp({ name: "", command: "", args: "", url: "" }); load();
    } catch (e) { toast(e.message); }
  };
  const saveSecret = async () => {
    if (!secret.ref.trim()) return;
    try { await api("PUT", `/api/secrets/${encodeURIComponent(secret.ref.trim())}`, { value: secret.value }); setSecret({ ref: "", value: "" }); load(); }
    catch (e) { toast(e.message); }
  };
  const delSecret = async (name) => { try { await del(`/api/secrets/${encodeURIComponent(name)}`); load(); } catch (e) { toast(e.message); } };

  return (
    <>
      <div className="card">
        <div className="card-head"><h2>Skills &amp; Tools</h2><span className="spacer" />
          <button className="btn" onClick={rescan}>Rescan</button></div>
      </div>
      <div className="card"><div className="card-head"><h3>Skills (SKILL.md)</h3></div><Table items={byKind.skill} empty="None found." /></div>
      <div className="card"><div className="card-head"><h3>Custom tools</h3></div><Table items={byKind.tool} empty="None found." /></div>

      <div className="card">
        <div className="card-head"><h3>MCP servers</h3></div>
        <Table items={byKind.mcp} empty="No MCP servers." />
        <div className="row wrap">
          <input placeholder="Server name" value={mcp.name} onChange={(e) => setMcp({ ...mcp, name: e.target.value })} />
          <input placeholder="Command (stdio) e.g. npx" value={mcp.command} onChange={(e) => setMcp({ ...mcp, command: e.target.value })} />
          <input placeholder="Args (space-separated)" value={mcp.args} onChange={(e) => setMcp({ ...mcp, args: e.target.value })} />
          <input placeholder="URL (for HTTP servers)" value={mcp.url} onChange={(e) => setMcp({ ...mcp, url: e.target.value })} />
          <button className="btn green" onClick={addMcp}>Add MCP server</button>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Secrets</h3></div>
        <p className="muted">Values are write-only and never shown. The agent cannot read them.</p>
        {data.secrets.length > 0 && (
          <table>
            <thead><tr><th>Reference</th><th>Owner</th><th /></tr></thead>
            <tbody>{data.secrets.map((s) => (
              <tr key={s.ref_name}><td>{s.ref_name}</td><td>{s.owner}</td>
                <td><button className="btn red" onClick={() => delSecret(s.ref_name)}>Delete</button></td></tr>
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
