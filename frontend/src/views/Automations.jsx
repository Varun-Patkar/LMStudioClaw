import { Fragment, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarClock } from "lucide-react";
import { get, post, patch, del } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";
import RunConfig from "./RunConfig.jsx";
import InfoTip from "../components/InfoTip.jsx";
import SkillInput from "../components/SkillInput.jsx";
import TriggerHistory from "../components/TriggerHistory.jsx";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function Automations() {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({
    name: "", task: "", schedule_type: "daily", daily_days: [], daily_time: "09:00",
    interval_unit: "minutes", interval_value: 30, session_mode: "new", persona_id: "",
    trigger_type: "time", trigger_id: "",
  });
  const [runCfg, setRunCfg] = useState(null);
  const [runImmediately, setRunImmediately] = useState(false);
  const toast = useToast();

  const load = () => Promise.all([
    get("/api/automations"),
    get("/api/personas").catch(() => []),
    get("/api/models").catch(() => ({ models: [] })),
    get("/api/settings").catch(() => ({})),
    get("/api/triggers").catch(() => []),
  ]).then(([automations, personas, modelsResp, settings, triggers]) =>
    setData({ automations, personas, models: modelsResp.models || [], settings, triggers }));

  useEffect(() => { load(); }, []);
  if (!data) return <Skeleton />;

  const { automations, personas, models, settings, triggers } = data;
  const set = (patchObj) => setForm((f) => ({ ...f, ...patchObj }));

  const isHeartbeat = form.trigger_type === "python";
  // Heartbeats always run on an interval (the trigger is the condition, not the clock).
  const setTriggerType = (t) => set(t === "python"
    ? { trigger_type: "python", schedule_type: "interval" }
    : { trigger_type: "time" });

  const toggleDay = (i) => set({ daily_days: form.daily_days.includes(i) ? form.daily_days.filter((d) => d !== i) : [...form.daily_days, i] });

  const intervalMinutes = () => {
    const mult = { minutes: 1, hours: 60, days: 1440 }[form.interval_unit] || 0;
    return mult * (form.interval_value || 0);
  };

  const create = async () => {
    if (!form.name.trim()) return toast("Automation name is required.");
    if (!form.task.trim()) return toast("Task / instruction is required.");
    if (isHeartbeat) {
      if (!form.trigger_id) return toast("Select a trigger for this heartbeat.");
      if (intervalMinutes() < 5) return toast("A heartbeat interval must be at least 5 minutes.");
    } else if (form.schedule_type === "daily" && form.daily_days.length === 0) {
      return toast("Pick at least one day for a daily schedule.");
    } else if (form.schedule_type === "interval" && !(form.interval_value > 0)) {
      return toast("Interval value must be greater than 0.");
    }
    const body = {
      ...form, name: form.name.trim(), task: form.task.trim(),
      persona_id: form.persona_id || null, run_config: runCfg,
    };
    if (isHeartbeat) { delete body.daily_days; delete body.daily_time; }
    else {
      delete body.trigger_id;
      if (form.schedule_type === "daily") { delete body.interval_unit; delete body.interval_value; }
      else { delete body.daily_days; delete body.daily_time; }
    }
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

  const describe = (a) => {
    if (a.trigger_type === "python") {
      const t = (triggers || []).find((x) => x.id === a.trigger_id);
      return `Heartbeat${t ? ` · ${t.name}` : ""} · checks every ${a.interval_value} ${a.interval_unit}`;
    }
    return a.schedule_type === "daily"
      ? `Daily ${(a.daily_days || []).map((d) => WEEKDAYS[d]).join(", ")} at ${a.daily_time}`
      : `Every ${a.interval_value} ${a.interval_unit}`;
  };

  return (
    <>
      <div className="view-head"><h1>Scheduled</h1><span className="sub">Run the agent automatically on a schedule</span></div>

      <div className="card">
        <div className="card-head"><h2>New scheduled task</h2></div>
        <input placeholder="Name" value={form.name} onChange={(e) => set({ name: e.target.value })} />
        <SkillInput rows={3} value={form.task} placeholder="Task / instruction for the agent  (type / to call a skill)"
          onChange={(v) => set({ task: v })} />
        <div className="row"><span className="muted">
          Trigger type<InfoTip text="Schedule runs the agent at fixed times. Heartbeat runs a saved Python trigger on an interval and only launches the agent when the trigger returns true (e.g. 'a new version was released')." />
        </span>
          <div className="seg" role="tablist" aria-label="Trigger type">
            <button type="button" className={"seg-btn" + (!isHeartbeat ? " on" : "")}
              onClick={() => setTriggerType("time")}>Schedule</button>
            <button type="button" className={"seg-btn" + (isHeartbeat ? " on" : "")}
              onClick={() => setTriggerType("python")}>Heartbeat</button>
          </div>
        </div>
        {isHeartbeat ? (
          <div className="row"><span className="muted">Trigger<InfoTip text="The Python check that decides when this heartbeat runs. Create and test triggers on the Triggers page." /></span>
            <select value={form.trigger_id} onChange={(e) => set({ trigger_id: e.target.value })}>
              <option value="">Select a trigger…</option>
              {(triggers || []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
            <Link className="btn ghost" to="/triggers">Manage triggers</Link>
          </div>
        ) : null}
        {!isHeartbeat ? (
          <div className="row"><span className="muted">Schedule<InfoTip text="How often this task runs on its own. Daily runs on the days/time you pick; Interval runs every N minutes/hours/days." /></span>
            <select value={form.schedule_type} onChange={(e) => set({ schedule_type: e.target.value })}>
              <option value="daily">Daily</option><option value="interval">Interval</option>
            </select>
          </div>
        ) : null}
        {!isHeartbeat && form.schedule_type === "daily" ? (
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
          <div className="row"><span className="muted">{isHeartbeat ? "Check every" : "Every"}</span>
            <input type="number" min="1" style={{ width: 90 }} value={form.interval_value} onChange={(e) => set({ interval_value: Number(e.target.value) })} />
            <select value={form.interval_unit} onChange={(e) => set({ interval_unit: e.target.value })}>
              <option value="minutes">minutes</option><option value="hours">hours</option><option value="days">days</option>
            </select>
            {isHeartbeat ? <span className="muted">(min 5 minutes)</span> : null}
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
            <thead><tr><th>Name</th><th>Schedule</th><th>Type</th><th>Mode</th><th>Last</th><th>Next</th><th /></tr></thead>
            <tbody>
              {automations.map((a) => (
                <Fragment key={a.id}>
                  <tr>
                    <td>{a.name}</td>
                    <td>{describe(a)}</td>
                    <td>{a.trigger_type === "python" ? "Heartbeat" : "Schedule"}</td>
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
                  {a.trigger_type === "python" && a.trigger_id ? (
                    <tr className="history-row"><td colSpan={7}><TriggerHistory triggerId={a.trigger_id} /></td></tr>
                  ) : null}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
