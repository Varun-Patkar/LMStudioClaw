import { useEffect, useState } from "react";
import { get, post, patch, del } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";
import InfoTip from "../components/InfoTip.jsx";

export default function Settings() {
  const [data, setData] = useState(null);
  const toast = useToast();

  const load = () => Promise.all([
    get("/api/settings"),
    get("/api/models").catch(() => ({ models: [], connected: false })),
    get("/api/personas").catch(() => []),
    get("/api/grants").catch(() => []),
  ]).then(([settings, modelsResp, personas, grants]) =>
    setData({ settings, modelsResp, personas, grants }));

  useEffect(() => { load(); }, []);
  if (!data) return <Skeleton />;

  const { settings, modelsResp, personas, grants } = data;
  const save = async (patchObj) => { try { return await patch("/api/settings", patchObj); } catch (e) { toast(e.message); return null; } };

  const Field = ({ label, hint, children }) => (
    <div className="set-field"><label>{label}{hint && <InfoTip text={hint} />}</label>{children}</div>
  );

  return (
    <>
      <div className="view-head"><h1>Settings</h1><span className="sub">Theme, runtime, permissions, personas &amp; models</span></div>

      {/* General */}
      <div className="card">
        <div className="card-head"><h2>General</h2></div>
        <Field label="Theme" hint="Color scheme for the app. “System” follows your Windows light/dark setting.">
          <select defaultValue={settings.theme || "system"} onChange={(e) => {
            document.documentElement.setAttribute("data-theme", e.target.value); save({ theme: e.target.value });
          }}>
            {["system", "dark", "light"].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </Field>
        <Field label="Default model" hint="The LM Studio model loaded for new sessions and automations unless you pick a different one per run.">
          <select defaultValue={settings.default_model || ""} onChange={(e) => { save({ default_model: e.target.value || null }); }}>
            <option value="">None</option>
            {modelsResp.models.map((m) => <option key={m.key} value={m.key}>{m.display_name}</option>)}
          </select>
        </Field>
        <Field label="Launch on login (start minimized)" hint="Start LMStudioClaw automatically when you sign in to Windows, minimized to the system tray.">
          <StartupToggle initial={settings.startup_launch} save={save} />
        </Field>
      </div>

      <PhoneAccess />

      {/* Runtime */}
      <div className="card">
        <div className="card-head"><h2>Runtime</h2></div>
        <Field label="Unload model on idle" hint="Free your GPU/RAM by unloading the model from LM Studio after a session sits idle for the timeout below. The model reloads automatically on the next message.">
          <input type="checkbox" className="switch" defaultChecked={settings.idle_unload} onChange={(e) => save({ idle_unload: e.target.checked })} />
        </Field>
        <Field label="Summarize large MCP outputs" hint="When an MCP tool returns a very large result, pass a concise AI summary to the model to save context space (the full result is still stored and shown to you). The agent can re-run the tool when it needs the full detail. Recommended on.">
          <input type="checkbox" className="switch" defaultChecked={settings.summarize_mcp_outputs} onChange={(e) => save({ summarize_mcp_outputs: e.target.checked })} />
        </Field>
        <Field label="Run automations even when the app is closed" hint="Uses Windows Task Scheduler to launch LMStudioClaw at each automation's scheduled time, so scheduled tasks run even if the app isn't open. Only works while your PC is on and you're signed in to Windows — nothing runs while the PC is off.">
          <input type="checkbox" className="switch" defaultChecked={settings.use_task_scheduler} onChange={(e) => save({ use_task_scheduler: e.target.checked })} />
        </Field>
        {[
          ["Idle timeout (s)", "session_idle_timeout", 1, "How long a session can sit with no activity before it ends and the model unloads."],
          ["Max run duration (s)", "max_run_duration", 1, "Hard safety cap on a single run's wall-clock time. The run is force-ended if it exceeds this."],
          ["Retention (days)", "retention_days", 1, "How long completed session history is kept before being automatically pruned."],
          ["Compression threshold (0–1)", "compression_threshold", 0.01, "When the conversation fills this fraction of the model's context window, older turns are summarized to make room. 0.9 = compress at 90% full."],
          ["Web port", "web_port", 1, "Local port this web UI is served on. A free port is picked automatically if this one is taken."],
        ].map(([label, key, step, hint]) => (
          <Field label={label} key={key} hint={hint}>
            <input type="number" step={step} defaultValue={settings[key]} onChange={(e) => save({ [key]: Number(e.target.value) })} />
          </Field>
        ))}
      </div>

      {/* Folder permissions */}
      <div className="card">
        <div className="card-head"><h2>Folder permissions</h2><span className="spacer" />
          <InfoTip text="Folders the agent is allowed to read or write. It always has access to your workspace and its LMStudioClaw home; anything else it must ask for, and you can revoke a grant here at any time." /></div>
        {grants.length ? (
          <table>
            <thead><tr><th>Folder</th><th>Scope</th><th>Access</th><th /></tr></thead>
            <tbody>{grants.map((g) => (
              <tr key={g.id}><td><code>{g.path}</code></td><td>{g.scope}</td><td>{g.access}</td>
                <td><button className="btn red" onClick={async () => { try { await del(`/api/grants/${g.id}`); toast("Revoked."); load(); } catch (e) { toast(e.message); } }}>Revoke</button></td></tr>
            ))}</tbody>
          </table>
        ) : <p className="muted">No folder grants. The agent may use the workspace by default; it will ask before accessing anything else.</p>}
      </div>

      <Personas personas={personas} reload={load} />
      <ModelManagement modelsResp={modelsResp} reload={load} />
    </>
  );
}

/**
 * Launch-on-login toggle. Flips optimistically for instant feedback, then reconciles
 * to the real OS state the backend reports (it actually creates/removes a Startup
 * shortcut), so the switch can never silently disagree with Windows.
 */
function StartupToggle({ initial, save }) {
  const toast = useToast();
  const [on, setOn] = useState(!!initial);
  const [busy, setBusy] = useState(false);
  const onChange = async (e) => {
    const want = e.target.checked;
    setOn(want); setBusy(true);
    const res = await save({ startup_launch: want });
    setBusy(false);
    if (res && typeof res.startup_launch === "boolean") {
      setOn(res.startup_launch);
      if (res.startup_launch !== want) toast("Could not update Windows startup. Check permissions.");
    }
  };
  return <input type="checkbox" className="switch" checked={on} disabled={busy} onChange={onChange} />;
}

/**
 * "See this on your phone" — start a public Cloudflare tunnel and show a QR code the
 * user can scan (camera / Google Lens) to open the controller on their phone.
 */
function PhoneAccess() {
  const toast = useToast();
  const [state, setState] = useState({ running: false, url: null, qr_svg: null });
  const [busy, setBusy] = useState(false);

  useEffect(() => { get("/api/tunnel").then(setState).catch(() => {}); }, []);

  const start = async () => {
    setBusy(true);
    try { setState(await post("/api/tunnel/start", {})); }
    catch (e) { toast(e.message); } finally { setBusy(false); }
  };
  const stop = async () => {
    setBusy(true);
    try { setState(await post("/api/tunnel/stop", {})); }
    catch (e) { toast(e.message); } finally { setBusy(false); }
  };

  return (
    <div className="card">
      <div className="card-head"><h2>See this on your phone</h2></div>
      <p className="muted">
        Opens a temporary public link to this control panel via a VS Code dev tunnel.
        Scan the QR code with your phone’s camera or Google Lens to open it. The link
        stays active until you stop it or quit the app. Requires the <code>devtunnel</code>
        CLI (run <code>devtunnel user login</code> once).
      </p>
      {state.running ? (
        <>
          <div className="phone-qr">
            {state.qr_svg
              ? <span className="qr" dangerouslySetInnerHTML={{ __html: state.qr_svg }} />
              : <p className="muted">{state.qr_error || "QR unavailable."}</p>}
            <div className="phone-qr-info">
              <p>Scan to open on your phone, or visit:</p>
              <a href={state.url} target="_blank" rel="noreferrer"><code>{state.url}</code></a>
              <div className="row">
                <button className="btn red" disabled={busy} onClick={stop}>
                  {busy ? <><span className="spinner" /> Stopping…</> : "Stop sharing"}</button>
              </div>
            </div>
          </div>
        </>
      ) : (
        <button className="btn green" disabled={busy} onClick={start}>
          {busy ? <><span className="spinner" /> Starting tunnel…</> : "Show on my phone"}</button>
      )}
    </div>
  );
}

function Personas({ personas, reload }) {
  const toast = useToast();
  const [draft, setDraft] = useState({ name: "", instructions: "" });
  return (
    <div className="card">
      <div className="card-head"><h2>Personas</h2></div>
      {personas.map((p) => <PersonaRow p={p} reload={reload} key={p.id} />)}
      <div className="card">
        <div className="card-head"><h3>New persona</h3></div>
        <input placeholder="New persona name" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <textarea placeholder="Instructions" value={draft.instructions} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} />
        <button className="btn green" onClick={async () => {
          if (!draft.name.trim()) return toast("Persona name is required.");
          if (!draft.instructions.trim()) return toast("Persona instructions are required.");
          try { await post("/api/personas", { name: draft.name.trim(), instructions: draft.instructions.trim() }); setDraft({ name: "", instructions: "" }); reload(); }
          catch (e) { toast(e.message); }
        }}>Create persona</button>
      </div>
    </div>
  );
}

function PersonaRow({ p, reload }) {
  const toast = useToast();
  const [instr, setInstr] = useState(p.instructions);
  return (
    <div className="card">
      <div className="row"><strong>{p.name}</strong>{p.is_default && <span className="badge">default</span>}</div>
      <textarea value={instr} onChange={(e) => setInstr(e.target.value)} />
      <div className="row">
        <button className="btn" onClick={async () => { try { await patch(`/api/personas/${p.id}`, { instructions: instr }); toast("Saved."); } catch (e) { toast(e.message); } }}>Save</button>
        {!p.is_default && <button className="btn red" onClick={async () => { try { await del(`/api/personas/${p.id}`); reload(); } catch (e) { toast(e.message); } }}>Delete</button>}
      </div>
    </div>
  );
}

function ModelManagement({ modelsResp, reload }) {
  const toast = useToast();
  // Seed each input from the saved preference (falling back to model max) so a
  // previously pinned context length survives reloads instead of appearing reset.
  const [ctx, setCtx] = useState(() => Object.fromEntries((modelsResp.models || []).map((m) => [m.key, m.preferred_context_length ?? m.max_context_length])));
  const [loadingKey, setLoadingKey] = useState(null);
  const [unloading, setUnloading] = useState(false);
  const anyLoaded = (modelsResp.models || []).some((m) => m.is_loaded);

  if (!modelsResp.connected) {
    return <div className="card"><div className="card-head"><h2>Advanced → Model Management</h2></div>
      <p className="muted">LM Studio is not reachable.</p></div>;
  }

  const loadModel = async (m) => {
    setLoadingKey(m.key);
    try { await post("/api/models/load", { model_key: m.key, context_length: Number(ctx[m.key]) }); toast(`Loaded ${m.display_name}.`); reload(); }
    catch (e) { toast(e.message); } finally { setLoadingKey(null); }
  };
  const unload = async () => {
    setUnloading(true);
    try { await post("/api/models/unload", {}); toast("Unloaded."); reload(); }
    catch (e) { toast(e.message); } finally { setUnloading(false); }
  };

  return (
    <div className="card">
      <div className="card-head"><h2>Advanced → Model Management</h2><span className="spacer" />
        <button className="btn red" disabled={!anyLoaded || unloading} title={!anyLoaded ? "No model is currently loaded" : ""} onClick={unload}>
          {unloading ? <><span className="spinner" /> Unloading…</> : "Unload current"}
        </button>
      </div>
      <table>
        <thead><tr><th>Model</th><th>State</th><th>Context</th><th /></tr></thead>
        <tbody>
          {modelsResp.models.map((m) => (
            <tr key={m.key}>
              <td>{m.display_name}</td>
              <td>{m.is_loaded ? <span className="badge active">loaded</span> : "—"}</td>
              <td><input type="number" style={{ width: 130 }} value={ctx[m.key]} onChange={(e) => setCtx({ ...ctx, [m.key]: e.target.value })} /></td>
              <td>
                <div className="row">
                  <button className="btn" onClick={async () => { try { await post("/api/models/context-pref", { model_key: m.key, context_length: Number(ctx[m.key]) }); toast("Saved."); } catch (e) { toast(e.message); } }}>Set context</button>
                  <button className="btn green" disabled={loadingKey === m.key} onClick={() => loadModel(m)}>
                    {loadingKey === m.key ? <><span className="spinner" /> Loading…</> : "Load"}
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
