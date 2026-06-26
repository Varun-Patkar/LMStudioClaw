import { useNavigate } from "react-router-dom";
import { del } from "../api.js";
import { useToast } from "./Toast.jsx";

/**
 * Sidebar-footer run status: the current active run (or model/idle state) plus a
 * scrollable list of queued runs beneath it. Each queued row opens that run's
 * session (an automation is just a scheduled session, so it opens the same way).
 * Driven by the live status socket state from App: { model, active, queue }.
 */
export default function Runbar({ status }) {
  const navigate = useNavigate();
  const toast = useToast();
  const { active, queue, model } = status;
  const modelStatus = (model && model.status) || "idle";

  let cls = "run-indicator", label = "Idle";
  if (active) {
    cls += active.status === "loading" || modelStatus === "loading" ? " loading" : " active";
    label = `${active.label || "Run"} · ${active.status || "active"}`;
  } else if (modelStatus === "loading") { cls += " loading"; label = "Loading model…"; }
  else if (modelStatus === "ready") { cls += " active"; label = model.model ? `Model ready · ${model.model}` : "Model ready"; }
  else if (modelStatus === "error") { cls += " error"; label = "Load failed"; }

  const cancel = async (id) => {
    try { await del(`/api/queue/${id}`); toast("Queued run cancelled"); }
    catch (e) { toast(e.message); }
  };
  const openRun = (id) => navigate(`/sessions/${id}`);

  return (
    <div className="runbar">
      <div className={cls}
           onClick={() => { if (active) openRun(active.id); }}
           title={active ? "Open the running session" : "App status"}>
        <span className="dot" />
        <span className="run-label">{label}</span>
        {queue.length > 0 && <span className="q-count" title={`${queue.length} queued`}>{queue.length}</span>}
      </div>

      {queue.length > 0 && (
        <div className="queue-list">
          {queue.map((it) => (
            <div className="queue-row" key={it.id} role="button" tabIndex={0}
                 title="Open this queued run"
                 onClick={() => openRun(it.id)}
                 onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openRun(it.id); } }}>
              <span className="q-dot" />
              <span className="q-type">{it.trigger_type === "automation" ? "Auto" : "Session"}</span>
              <span className="q-label">{it.label || it.id.slice(0, 8)}</span>
              <button className="q-cancel" title="Cancel queued run"
                      onClick={(e) => { e.stopPropagation(); cancel(it.id); }}>✕</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
