import { useState } from "react";
import { Zap, FlaskConical, Loader2, Info } from "lucide-react";
import { post } from "../api.js";
import { useToast } from "./Toast.jsx";

/**
 * Trigger authoring editor: describe a condition in plain language, generate a Python
 * `trigger()` function, edit it, and test-run it to see the boolean result, captured
 * stdout, any error, and the resulting persisted `state`. Used on the Triggers page.
 *
 * Props:
 *   prompt, code  — controlled values (lifted to the parent form).
 *   model         — model key to use for generation (optional).
 *   onChange({ prompt, code }) — emit edits back to the parent.
 */
export default function HeartbeatTrigger({ prompt, code, model, onChange }) {
  const [generating, setGenerating] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState(null);
  const toast = useToast();

  const generate = async () => {
    if (!prompt.trim()) return toast("Describe what should make this trigger fire.");
    setGenerating(true);
    setResult(null);
    try {
      const { code: generated } = await post("/api/triggers/generate", { prompt, model: model || null });
      onChange({ prompt, code: generated });
      toast("Trigger code generated. Review and test it below.");
    } catch (e) { toast(e.message); }
    finally { setGenerating(false); }
  };

  const test = async () => {
    if (!code.trim()) return toast("Generate or write trigger code first.");
    setTesting(true);
    try {
      setResult(await post("/api/triggers/test", { code }));
    } catch (e) { toast(e.message); }
    finally { setTesting(false); }
  };

  return (
    <div className="hb-trigger">
      <textarea rows={2} value={prompt} placeholder="When should this fire?  e.g. 'a new release of vscode is published on GitHub since I last checked'"
        onChange={(e) => onChange({ prompt: e.target.value, code })} />
      <div className="row">
        <button className="btn" onClick={generate} disabled={generating}>
          {generating ? <Loader2 size={14} className="spin" /> : <Zap size={14} />} {generating ? "Generating…" : "Generate code"}
        </button>
        <button className="btn ghost" onClick={test} disabled={testing || !code.trim()}>
          {testing ? <Loader2 size={14} className="spin" /> : <FlaskConical size={14} />} Test run
        </button>
      </div>
      <div className="state-hint">
        <Info size={13} />
        <span>
          A dict named <code>state</code> persists between runs — read/write it to remember
          values and detect changes, e.g. <code>{'last = state.get("version"); state["version"] = current'}</code>.
          Return <code>True</code> to fire. The last saved <code>state</code> appears on the trigger after each run.
        </span>
      </div>
      {code ? (
        <textarea className="mono" rows={11} value={code} spellCheck={false}
          onChange={(e) => onChange({ prompt, code: e.target.value })} />
      ) : null}
      {result ? (
        <div className={`trigger-result ${result.error ? "err" : result.value ? "ok" : "no"}`}>
          <strong>
            {result.error ? "Error" : result.value ? "Would fire (returned true)" : "Would not fire (returned false)"}
            {result.timed_out ? " · timed out" : ""}
          </strong>
          {result.error ? <pre>{result.error}</pre> : null}
          {result.stdout ? <div><span className="muted">stdout:</span><pre className="muted">{result.stdout}</pre></div> : null}
          {result.state && Object.keys(result.state).length ? (
            <div><span className="muted">state after run:</span><pre className="muted">{JSON.stringify(result.state, null, 2)}</pre></div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

