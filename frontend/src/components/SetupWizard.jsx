import { useEffect, useState } from "react";
import { CheckCircle2, AlertTriangle, Loader2, ServerCog, KeyRound } from "lucide-react";
import { get, post } from "../api.js";

/**
 * First-run / per-start onboarding wizard.
 *
 * Shown as a full-screen overlay whenever the controller reports that the LM Studio
 * connection needs setup (server unreachable, or reachable but our API key is missing
 * or rejected). It walks a non-technical user through pointing at their LM Studio
 * instance and entering an API key only when the instance is protected.
 *
 * The key is sent to the backend (which stores it in the isolated vault) and is never
 * read back — this component holds it only in local state while the user types.
 *
 * Props:
 *   onComplete() — called once the connection is verified so the app can render.
 */
export default function SetupWizard({ onComplete }) {
  // `status` is the latest /api/connection result; null while the first check runs.
  const [status, setStatus] = useState(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [testResult, setTestResult] = useState(null); // last test_connection result
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  /** Re-run the connection check; close the wizard the moment it succeeds. */
  const check = () =>
    get("/api/connection")
      .then((s) => {
        setStatus(s);
        setBaseUrl((b) => b || s.base_url || "http://localhost:1234");
        if (!s.needs_setup) onComplete && onComplete();
        return s;
      })
      .catch((e) => setError(e.message));

  useEffect(() => { check(); /* eslint-disable-next-line */ }, []);

  /** Probe the entered URL + key without saving so the user can verify first. */
  async function test() {
    setBusy(true); setError(null); setTestResult(null);
    try {
      setTestResult(await post("/api/connection/test", { base_url: baseUrl, api_key: apiKey }));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  /** Save the URL + key (key → vault) and continue once the connection works. */
  async function save() {
    setBusy(true); setError(null);
    try {
      const s = await post("/api/connection/save", { base_url: baseUrl, api_key: apiKey });
      setStatus(s);
      setTestResult({ reachable: s.reachable, authorized: s.authorized, auth_required: s.auth_required });
      if (!s.needs_setup) onComplete && onComplete();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  // While the very first check is in flight, show nothing (avoids a flash).
  if (status === null) return null;

  const unreachable = !status.reachable;
  const protectedInstance = status.auth_required || status.has_key;

  return (
    <div className="modal-overlay">
      <div className="modal setup-modal">
        <div className="modal-head">
          <ServerCog size={18} />
          <h3>Connect to LM Studio</h3>
        </div>

        <div className="setup-body">
          {unreachable ? (
            <p className="setup-lead">
              We couldn&apos;t reach LM Studio. Open the <b>LM Studio</b> app, start its{" "}
              <b>local server</b> (Developer tab → Start Server), then check the address below.
            </p>
          ) : (
            <p className="setup-lead">
              {protectedInstance
                ? "Your LM Studio server is protected with an API key. Enter it below so the agent can connect."
                : "Almost there — confirm the address below and continue."}
            </p>
          )}

          <label className="setup-field">
            <span>LM Studio address</span>
            <input
              type="text"
              value={baseUrl}
              placeholder="http://localhost:1234"
              onChange={(e) => setBaseUrl(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </label>

          <label className="setup-field">
            <span><KeyRound size={13} /> API key {protectedInstance ? "" : "(only if your server requires one)"}</span>
            <input
              type="password"
              value={apiKey}
              placeholder={status.has_key ? "•••••••• (saved — leave blank to keep)" : "Paste your LM Studio API key"}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
            />
          </label>

          {testResult && (
            <div className={"setup-result " + (testResult.authorized ? "ok" : "bad")}>
              {testResult.authorized ? (
                <><CheckCircle2 size={16} /> Connected — your settings work.</>
              ) : testResult.reachable ? (
                <><AlertTriangle size={16} /> Reached LM Studio, but the key was rejected. Check the key and try again.</>
              ) : (
                <><AlertTriangle size={16} /> Couldn&apos;t reach LM Studio at that address.</>
              )}
            </div>
          )}

          {error && <div className="setup-result bad"><AlertTriangle size={16} /> {error}</div>}
        </div>

        <div className="setup-actions">
          <button className="btn ghost" disabled={busy} onClick={check}>Re-check</button>
          <span className="spacer" />
          <button className={"btn ghost" + (busy ? " working" : "")} disabled={busy} onClick={test}>
            {busy ? <Loader2 size={15} className="spin" /> : null} Test connection
          </button>
          <button className={"btn green" + (busy ? " working" : "")} disabled={busy} onClick={save}>
            Save &amp; continue
          </button>
        </div>
      </div>
    </div>
  );
}
