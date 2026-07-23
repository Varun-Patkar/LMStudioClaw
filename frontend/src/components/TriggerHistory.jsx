import { useState } from "react";
import { History, Loader2 } from "lucide-react";
import { get } from "../api.js";

/**
 * Power-Automate-style run history for a trigger: an expandable list of every
 * evaluation with its result, error, captured stdout, and duration so failures are
 * never hidden. Lazily fetches the first time it is opened.
 */
export default function TriggerHistory({ triggerId }) {
  const [open, setOpen] = useState(false);
  const [runs, setRuns] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadRuns = async () => {
    setLoading(true);
    try { setRuns(await get(`/api/triggers/${triggerId}/history`)); }
    catch { setRuns([]); }
    finally { setLoading(false); }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && runs === null) loadRuns();
  };

  return (
    <div className="trigger-history">
      <button className="btn ghost" onClick={toggle}>
        <History size={14} /> {open ? "Hide history" : "History"}
      </button>
      {open ? (
        <div className="history-body">
          {loading ? <div className="row muted"><Loader2 size={14} className="spin" /> Loading…</div> : null}
          {!loading && runs && runs.length === 0 ? <div className="muted">No trigger runs yet.</div> : null}
          {!loading && runs && runs.length > 0 ? (
            <table className="history-table">
              <thead><tr><th>When</th><th>Result</th><th>Duration</th><th>Details</th></tr></thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className={r.error ? "err" : r.value ? "ok" : ""}>
                    <td>{(r.at || "").replace("T", " ").slice(0, 19)}</td>
                    <td>
                      <span className={`pill ${r.error ? "red" : r.value ? "green" : "grey"}`}>
                        {r.error ? "Error" : r.value ? "Fired" : "No fire"}
                      </span>
                    </td>
                    <td className="muted">{r.duration_ms != null ? `${r.duration_ms} ms` : "—"}</td>
                    <td>
                      {r.error ? <pre className="err">{r.error}</pre> : null}
                      {r.stdout ? <pre className="muted">{r.stdout}</pre> : null}
                      {!r.error && !r.stdout ? <span className="muted">—</span> : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
