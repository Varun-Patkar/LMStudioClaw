import { useEffect, useState } from "react";
import { CalendarClock } from "lucide-react";
import { get, post, patch, del } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";
import RunConfig from "./RunConfig.jsx";
import InfoTip from "../components/InfoTip.jsx";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function Automations() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({
    name: "", task: "", schedule_type: "daily", daily_days: [], daily_time: "09:00",
    interval_unit: "minutes", interval_value: 30, session_mode: "new", persona_id: "",
  });
  const [runCfg, setRunCfg] = useState(null);
  const [runImmediately, setRunImmediately] = useState(false);
  const toast = useToast();

  const load = () => Promise.all([
    get("/api/automations"),
    get("/api/personas").catch(() => []),
    get("/api/models").catch(() => ({ models: [] })),
    get("/api/settings").catch(() => ({})),
  ]).then(([automations, personas, modelsResp, settings]) =>
    setData({ automations, personas, models: modelsResp.models || [], settings }));

  useEffect(() => { load(); }, []);
  if (!data) return <Skeleton />;

  const { automations, personas, models, settings } = data;
  const set = (patchObj) => setForm((f) => ({ ...f, ...patchObj }));

  const toggleDay = (i) => set({ daily_days: form.daily_days.includes(i) ? form.daily_days.filter((d) => d !== i) : [...form.daily_days, i] });

  const create = async () => {
    if (!form.name.trim()) return toast("Automation name is required.");
    if (!form.task.trim()) return toast("Task / instruction is required.");
    if (form.schedule_type === "daily" && form.daily_days.length === 0)
      return toast("Pick at least one day for a daily schedule.");
    if (form.schedule_type === "interval" && !(form.interval_value > 0))
      return toast("Interval value must be greater than 0.");
    const body = { ...form, name: form.name.trim(), task: form.task.trim(), persona_id: form.persona_id || null, run_config: runCfg };
    if (form.schedule_type === "daily") { delete body.interval_unit; delete body.interval_value; }
    else { delete body.daily_days; delete body.daily_time; }
    try {
      const { id } = await post("/api/automations", body);
      if (runImmediately && id) {
        await post(`/api/automations/${id}/run`, {});
        toast("Scheduled task created and queued to run now.");
      } else {
        toast("Scheduled task created.");
      }
      load();
    }
    catch (e) { toast(e.message); }
  };

  const toggle = async (a) => {
    // Flip immediately for snappy feedback, then reconcile from the server.
    setData((d) => ({ ...d, automations: d.automations.map((x) => x.id === a.id ? { ...x, enabled: !x.enabled } : x) }));
    try { await patch(`/api/automations/${a.id}`, { enabled: !a.enabled }); load(); }
    catch (e) { toast(e.message); load(); }
  };
  const runNow = async (a) => { try { await post(`/api/automations/${a.id}/run`, {}); toast("Queued."); } catch (e) { toast(e.message); } };
  const remove = async (a) => { try { await del(`/api/automations/${a.id}`); load(); } catch (e) { toast(e.message); } };

  const describe = (a) => a.schedule_type === "daily"
    ? `Daily ${(a.daily_days || []).map((d) => WEEKDAYS[d]).join(", ")} at ${a.daily_time}`
    : `Every ${a.interval_value} ${a.interval_unit}`;

  return (
    <>
      <div className="view-head"><h1>Scheduled</h1><span className="sub">Run the agent automatically on a schedule</span></div>

      <div className="card">
        <div className="card-head"><h2>New scheduled task</h2></div>
        <input placeholder="Name" value={form.name} onChange={(e) => set({ name: e.target.value })} />
        <textarea placeholder="Task / instruction for the agent" value={form.task} onChange={(e) => set({ task: e.target.value })} />
        <div className="row"><span className="muted">Schedule<InfoTip text="How often this task runs on its own. Daily runs on the days/time you pick; Interval runs every N minutes/hours/days." /></span>
          <select value={form.schedule_type} onChange={(e) => set({ schedule_type: e.target.value })}>
            <option value="daily">Daily</option><option value="interval">Interval</option>
          </select>
        </div>
        {form.schedule_type === "daily" ? (
          <>
            <div className="row wrap">
              {WEEKDAYS.map((d, i) => (
                <label className="check" key={d} style={{ display: "flex", gap: 4, alignItems: "center" }}>
                  <input type="checkbox" checked={form.daily_days.includes(i)} onChange={() => toggleDay(i)} /> {d}
                </label>
              ))}
            </div>
            <div className="row"><span className="muted">Time</span>
              <input type="time" value={form.daily_time} onChange={(e) => set({ daily_time: e.target.value })} /></div>
          </>
        ) : (
          <div className="row"><span className="muted">Every</span>
            <input type="number" min="1" style={{ width: 90 }} value={form.interval_value} onChange={(e) => set({ interval_value: Number(e.target.value) })} />
            <select value={form.interval_unit} onChange={(e) => set({ interval_unit: e.target.value })}>
              <option value="minutes">minutes</option><option value="hours">hours</option><option value="days">days</option>
            </select>
          </div>
        )}
        <div className="row"><span className="muted">Mode<InfoTip text="New session each run starts fresh every time. Persistent session resumes the same conversation so the agent remembers previous runs." /></span>
          <select value={form.session_mode} onChange={(e) => set({ session_mode: e.target.value })}>
            <option value="new">New session each run</option><option value="persistent">Persistent session (resume)</option>
          </select>
          <select value={form.persona_id} onChange={(e) => set({ persona_id: e.target.value })}>
            <option value="">Default persona</option>
            {personas.filter((p) => !p.is_default).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <RunConfig models={models} defaultModel={settings.default_model} onChange={setRunCfg} />
        <label className="check" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={runImmediately} onChange={(e) => setRunImmediately(e.target.checked)} />
          Run now (otherwise it starts on the next scheduled trigger)
          <InfoTip text="Also queue the task to run once right away, in addition to its schedule. Leave unchecked to wait for the next scheduled trigger." />
        </label>
        <button className="btn green" onClick={create}>{runImmediately ? "Create and run automation" : "Create automation"}</button>
      </div>

      <div className="card">
        <div className="card-head"><h2>Scheduled tasks</h2></div>
        {automations.length === 0 ? (
          <div className="empty-state">
            <span className="ico"><CalendarClock size={28} /></span>
            <strong>No scheduled tasks yet</strong>
            Create one above to run the agent automatically on a schedule.
          </div>
        ) : (
          <table>
            <thead><tr><th>Name</th><th>Schedule</th><th>Mode</th><th>Last</th><th>Next</th><th /></tr></thead>
            <tbody>
              {automations.map((a) => (
                <tr key={a.id}>
                  <td>{a.name}</td>
                  <td>{describe(a)}</td>
                  <td>{a.session_mode}</td>
                  <td>{a.last_run_result || "—"}</td>
                  <td>{(a.next_run_at || "").replace("T", " ").slice(0, 16) || "—"}</td>
                  <td>
                    <div className="row">
                      <button className="btn ghost" onClick={() => toggle(a)}>{a.enabled ? "Disable" : "Enable"}</button>
                      <button className="btn" onClick={() => runNow(a)}>Run now</button>
                      <button className="btn red" onClick={() => remove(a)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
