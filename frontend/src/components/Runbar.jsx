import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { del } from "../api.js";
import { useToast } from "./Toast.jsx";

/**
 * Top-right run indicator + collapsible queue panel. Driven by the live status
 * socket state passed from App: { model, active, queue }.
 */
export default function Runbar({ status }) {
  const [open, setOpen] = useState(false);
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

  return (
    <div className="runbar">
      <div className={cls} onClick={() => { if (active) navigate(`/sessions/${active.id}`); }}
           title={active ? "Open the running session" : "App status"}>
        <span className="dot" />
        <span>{label}</span>
        {queue.length > 0 && (
          <span className="q-count" title="Toggle queue"
                onClick={(e) => { e.stopPropagation(); setOpen((o) => !o); }}>
            {queue.length}
          </span>
        )}
      </div>

      <AnimatePresence>
        {open && queue.length > 0 && (
          <motion.div className="queue-panel"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.16 }}>
            <h3>Queued ({queue.length})</h3>
            {queue.map((it) => (
              <div className="queue-item" key={it.id}>
                <span className="q-type">{it.trigger_type === "automation" ? "Auto" : "Session"}</span>
                <span className="q-label">{it.label || it.id.slice(0, 8)}</span>
                <button className="btn ghost" title="Cancel queued run"
                  onClick={async () => {
                    try { await del(`/api/queue/${it.id}`); toast("Queued run cancelled"); }
                    catch (e) { toast(e.message); }
                  }}>✕</button>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
