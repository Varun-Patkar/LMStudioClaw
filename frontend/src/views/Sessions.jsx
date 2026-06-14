import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { get, post, del } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";
import RunConfig from "./RunConfig.jsx";

export default function Sessions() {
  const [data, setData] = useState(null);
  const [model, setModel] = useState("");
  const [persona, setPersona] = useState("");
  const [prompt, setPrompt] = useState("");
  const [runCfg, setRunCfg] = useState(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const toast = useToast();

  const load = () => Promise.all([
    get("/api/sessions"),
    get("/api/models").catch(() => ({ models: [] })),
    get("/api/personas").catch(() => []),
    get("/api/settings").catch(() => ({})),
  ]).then(([sessions, modelsResp, personas, settings]) =>
    setData({ sessions, models: modelsResp.models || [], personas, settings }));

  useEffect(() => { load(); }, []);
  if (!data) return <Skeleton />;

  const { sessions, models, personas, settings } = data;
  const def = models.find((m) => m.key === settings.default_model);

  const start = async () => {
    setBusy(true);
    try {
      const res = await post("/api/sessions", {
        model: model || null, persona_id: persona || null, run_config: runCfg,
        initial_message: prompt.trim() || null,
      });
      navigate(`/sessions/${res.session_id}`);
    } catch (e) { toast(e.message); } finally { setBusy(false); }
  };

  const restart = async (id) => {
    try { const res = await post(`/api/sessions/${id}/restart`, {}); navigate(`/sessions/${res.session_id}`); }
    catch (e) { toast(e.message); }
  };
  const remove = async (id) => {
    if (!confirm("Delete this session and its transcript?")) return;
    try { await del(`/api/sessions/${id}`); load(); } catch (e) { toast(e.message); }
  };

  return (
    <>
      <div className="card">
        <div className="card-head"><h2>New session</h2></div>
        <div className="row wrap">
          <select value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">{def ? `Default model (${def.display_name})` : "Default model"}</option>
            {models.filter((m) => m.key !== settings.default_model)
              .map((m) => <option key={m.key} value={m.key}>{m.display_name}</option>)}
          </select>
          <select value={persona} onChange={(e) => setPersona(e.target.value)}>
            <option value="">Default persona</option>
            {personas.filter((p) => !p.is_default).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <button className="btn green" disabled={busy} onClick={start}>
            {busy ? <><span className="spinner" /> Starting…</> : "Start session"}
          </button>
        </div>
        <textarea className="session-prompt" value={prompt} rows={3}
          placeholder="Optional: first message — the agent starts on it as soon as the session runs"
          onChange={(e) => setPrompt(e.target.value)} />
        <RunConfig models={models} defaultModel={settings.default_model} onChange={setRunCfg} />
      </div>

      <div className="card">
        <div className="card-head"><h2>Sessions</h2></div>
        {sessions.length === 0 ? <p className="muted">No sessions yet.</p> : (
          <table>
            <thead><tr><th>Status</th><th>Trigger</th><th>Model</th><th>Started</th><th /></tr></thead>
            <tbody>
              {sessions.map((s) => {
                const activeRun = s.status === "loading" || s.status === "active";
                return (
                  <tr key={s.id}>
                    <td><span className={"badge " + s.status}>{s.status}</span></td>
                    <td>{s.trigger_type}</td>
                    <td>{s.model_key || "—"}</td>
                    <td>{(s.started_at || s.created_at || "").replace("T", " ").slice(0, 19)}</td>
                    <td>
                      <div className="row">
                        <button className="btn ghost" onClick={() => navigate(`/sessions/${s.id}`)}>Open</button>
                        {!activeRun && <button className="btn" onClick={() => restart(s.id)}>Restart</button>}
                        {!activeRun && <button className="btn red" onClick={() => remove(s.id)}>Delete</button>}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
