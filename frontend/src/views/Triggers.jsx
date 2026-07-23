import { useEffect, useState } from "react";
import { Zap, Play, Loader2 } from "lucide-react";
import { get, post, del } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";
import HeartbeatTrigger from "../components/HeartbeatTrigger.jsx";
import TriggerHistory from "../components/TriggerHistory.jsx";

/**
 * Triggers page: author, test, and manage standalone heartbeat conditions.
 *
 * A trigger is a reusable Python `trigger() -> bool` generated from a prompt. It is the
 * first node of a heartbeat — a Scheduled automation can then reference a trigger and
 * only run when it returns true. Test output and per-trigger run history are shown here
 * (like Power Automate) so results and errors are always visible.
 */
export default function Triggers() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ name: "", prompt: "", code: "" });
  const [running, setRunning] = useState({});
  const toast = useToast();

  const load = () => Promise.all([
    get("/api/triggers"),
    get("/api/models").catch(() => ({ models: [] })),
    get("/api/settings").catch(() => ({})),
  ]).then(([triggers, modelsResp, settings]) =>
    setData({ triggers, models: modelsResp.models || [], settings }));

  useEffect(() => { load(); }, []);
  if (!data) return <Skeleton />;

  const { triggers, settings } = data;

  const save = async () => {
    if (!form.name.trim()) return toast("Give the trigger a name.");
    if (!form.code.trim()) return toast("Generate and test the trigger code first.");
    try {
      await post("/api/triggers", { name: form.name.trim(), prompt: form.prompt, code: form.code });
      toast("Trigger saved.");
      setForm({ name: "", prompt: "", code: "" });
      load();
    } catch (e) { toast(e.message); }
  };

  const runNow = async (t) => {
    setRunning((r) => ({ ...r, [t.id]: true }));
    try {
      const res = await post(`/api/triggers/${t.id}/run`, {});
      toast(res.error ? "Trigger errored — see history." : res.value ? "Trigger returned true (would fire)." : "Trigger returned false.");
      load();
    } catch (e) { toast(e.message); }
    finally { setRunning((r) => ({ ...r, [t.id]: false })); }
  };

  const remove = async (t) => { try { await del(`/api/triggers/${t.id}`); load(); } catch (e) { toast(e.message); } };

  const lastBadge = (t) => {
    if (t.last_error) return <span className="pill red">Error</span>;
    if (t.last_run_at == null) return <span className="pill grey">Not run</span>;
    return <span className={`pill ${t.last_value ? "green" : "grey"}`}>{t.last_value ? "Fired" : "No fire"}</span>;
  };

  return (
    <>
      <div className="view-head"><h1>Triggers</h1><span className="sub">Python checks that decide when a heartbeat runs</span></div>

      <div className="card">
        <div className="card-head"><h2>New trigger</h2></div>
        <input placeholder="Name  (e.g. 'New VS Code release')" value={form.name}
          onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} />
        <HeartbeatTrigger
          prompt={form.prompt} code={form.code}
          model={settings.default_model}
          onChange={({ prompt, code }) => setForm((f) => ({ ...f, prompt, code }))}
        />
        <button className="btn green" onClick={save}><Zap size={15} /> Save trigger</button>
      </div>

      <div className="card">
        <div className="card-head"><h2>Saved triggers</h2></div>
        {triggers.length === 0 ? (
          <div className="empty-state">
            <span className="ico"><Zap size={28} /></span>
            <strong>No triggers yet</strong>
            Create one above, then attach it to a Scheduled heartbeat.
          </div>
        ) : (
          <div className="trigger-list">
            {triggers.map((t) => (
              <div className="trigger-item" key={t.id}>
                <div className="trigger-item-head">
                  <div className="trigger-item-title">
                    <strong>{t.name}</strong>
                    {lastBadge(t)}
                    {t.last_run_at ? <span className="muted">last run {(t.last_run_at || "").replace("T", " ").slice(0, 19)}</span> : null}
                  </div>
                  <div className="row">
                    <button className="btn" onClick={() => runNow(t)} disabled={running[t.id]}>
                      {running[t.id] ? <Loader2 size={14} className="spin" /> : <Play size={14} />} Run now
                    </button>
                    <button className="btn red" onClick={() => remove(t)}>Delete</button>
                  </div>
                </div>
                {t.prompt ? <div className="muted trigger-prompt">{t.prompt}</div> : null}
                {t.last_error ? <pre className="err trigger-err">{t.last_error}</pre> : null}
                {t.state && Object.keys(t.state).length ? (
                  <div className="trigger-state"><span className="muted">state:</span> <code>{JSON.stringify(t.state)}</code></div>
                ) : null}
                <TriggerHistory triggerId={t.id} />
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
