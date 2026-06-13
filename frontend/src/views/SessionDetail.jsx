import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { get, post, del, wsUrl } from "../api.js";
import { useToast } from "../components/Toast.jsx";
import Skeleton from "../components/Skeleton.jsx";
import TokenGauge from "../components/TokenGauge.jsx";

const uid = () => Math.random().toString(36).slice(2);
const TERMINAL = ["completed", "failed", "stopped"];

/** One transcript bubble — markdown for assistant/system, plain text for user/tool. */
function Bubble({ m }) {
  const md = m.role === "assistant" || m.role === "system";
  return (
    <motion.div className={"msg " + m.role}
      initial={{ opacity: 0, y: 8, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.18 }}>
      {md
        ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content || ""}</ReactMarkdown>
        : m.content}
      {m.streaming && <span className="cursor" />}
    </motion.div>
  );
}

export default function SessionDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [session, setSession] = useState(null);
  const [automation, setAutomation] = useState(null);
  const [messages, setMessages] = useState([]);
  const [status, setStatus] = useState("");
  const [generating, setGenerating] = useState(false);
  const [budget, setBudget] = useState(null);
  const [consent, setConsent] = useState(null);
  const [ended, setEnded] = useState(false);
  const [ending, setEnding] = useState(false);
  const [draft, setDraft] = useState("");

  const ws = useRef(null);
  const streamingId = useRef(null);
  const generatingRef = useRef(false);
  const scroller = useRef(null);

  // Load the session, seed transcript, and (if live) open the WebSocket.
  useEffect(() => {
    let active = true;
    setSession(null); setMessages([]); setAutomation(null);
    get(`/api/sessions/${id}`).then(async (s) => {
      if (!active) return;
      setSession(s);
      setStatus(s.status);
      const terminal = TERMINAL.includes(s.status);
      setEnded(terminal);
      setMessages((s.turns || [])
        .filter((t) => ["user", "assistant", "system"].includes(t.role) && t.content)
        .map((t) => ({ id: uid(), role: t.role, content: t.content })));
      if (s.trigger_type === "automation" && s.automation_id) {
        get("/api/automations").then((list) => active && setAutomation(list.find((a) => a.id === s.automation_id))).catch(() => {});
      }
      if (!terminal) openSocket();
    }).catch((e) => toast(e.message));
    return () => { active = false; try { ws.current && ws.current.close(); } catch { /* noop */ } };
  }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the transcript scrolled to the newest message.
  useEffect(() => { if (scroller.current) scroller.current.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages]);

  function setGen(v) { generatingRef.current = v; setGenerating(v); }

  function openSocket() {
    const sock = new WebSocket(wsUrl(`/ws/sessions/${id}`));
    ws.current = sock;
    sock.onmessage = (ev) => {
      let evt; try { evt = JSON.parse(ev.data); } catch { return; }
      switch (evt.type) {
        case "turn":
          if (evt.state === "start") { streamingId.current = null; setGen(true); }
          else { streamingId.current = null; setGen(false); }
          break;
        case "status":
          setStatus(evt.status);
          if (TERMINAL.includes(evt.status)) { setEnded(true); setGen(false); toast(`Session ${evt.status}.`); }
          break;
        case "token":
          setMessages((ms) => {
            if (streamingId.current) {
              return ms.map((m) => m.id === streamingId.current ? { ...m, content: m.content + evt.text } : m);
            }
            const nid = uid(); streamingId.current = nid;
            return [...ms, { id: nid, role: "assistant", content: evt.text, streaming: true }];
          });
          break;
        case "tool_call":
          streamingId.current = null;
          finalizeStreaming();
          setMessages((ms) => [...ms, { id: uid(), role: "tool", content: `🔧 ${evt.name}(${JSON.stringify(evt.args)})` }]);
          break;
        case "tool_result":
          setMessages((ms) => [...ms, { id: uid(), role: "tool", content: `→ ${evt.ok ? "ok" : "error"}: ${evt.summary || ""}` }]);
          break;
        case "budget": setBudget(evt); break;
        case "compaction":
          finalizeStreaming();
          setMessages((ms) => [...ms, { id: uid(), role: "system", content: `Context compacted: ${evt.tokens_before} → ${evt.tokens_after} tokens` }]);
          break;
        case "consent_request": setConsent(evt); break;
        case "error":
          setMessages((ms) => [...ms, { id: uid(), role: "system", content: `Error: ${evt.reason}` }]);
          break;
        default: break;
      }
    };
    sock.onclose = () => { setGen(false); };
  }

  function finalizeStreaming() {
    setMessages((ms) => ms.map((m) => (m.streaming ? { ...m, streaming: false } : m)));
  }

  function send() {
    const text = draft.trim();
    if (!text || !ws.current) return;
    const kind = generatingRef.current ? "steer" : "message";
    ws.current.send(JSON.stringify({ type: kind, text }));
    setMessages((ms) => [...ms, { id: uid(), role: "user", content: text }]);
    setDraft("");
  }

  function endSession() {
    setEnding(true);
    try { ws.current.send(JSON.stringify({ type: "stop", scope: "session" })); }
    catch (e) { toast(e.message); }
  }

  async function decideConsent(decision) {
    try {
      await post("/api/grants", { request_id: consent.request_id, session_id: id, path: consent.path, decision, access: consent.access });
    } catch (e) { toast(e.message); }
    setConsent(null);
  }

  async function restartContinue() {
    try { const res = await post(`/api/sessions/${id}/restart`, {}); navigate(`/sessions/${res.session_id}`); }
    catch (e) { toast(e.message); }
  }
  async function remove() {
    if (!confirm("Delete this session and its transcript?")) return;
    try { await del(`/api/sessions/${id}`); navigate("/sessions"); } catch (e) { toast(e.message); }
  }

  if (!session) return <Skeleton cards={2} />;

  const onKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  };

  return (
    <>
      <div className="card">
        <div className="card-head">
          <button className="btn ghost" onClick={() => navigate("/sessions")}>← Back</button>
          <h2>Session</h2>
          <span className={"badge " + status}>{status}</span>
          <span className="spacer" />
          {!ended && <TokenGauge budget={budget} />}
          {!ended && <button className="btn" onClick={() => ws.current.send(JSON.stringify({ type: "stop", scope: "turn" }))}>Stop turn</button>}
          {!ended
            ? <button className="btn red" disabled={ending} onClick={endSession}>{ending ? <><span className="spinner" /> Ending…</> : "End session"}</button>
            : <>
                <button className="btn green" onClick={restartContinue}>Restart &amp; continue</button>
                <button className="btn red" onClick={remove}>Delete</button>
              </>}
        </div>

        {consent && (
          <div className="consent">
            <div>Agent requests <strong>{consent.access}</strong> access to:</div>
            <code>{consent.path}</code>
            <div className="row">
              <button className="btn" onClick={() => decideConsent("session")}>Allow for session</button>
              <button className="btn green" onClick={() => decideConsent("permanent")}>Allow permanently</button>
              <button className="btn red" onClick={() => decideConsent("deny")}>Deny</button>
            </div>
          </div>
        )}

        <div className="transcript">
          <AnimatePresence initial={false}>
            {messages.map((m) => <Bubble key={m.id} m={m} />)}
          </AnimatePresence>
          <div ref={scroller} />
        </div>

        {ended
          ? <p className="muted">Session ended. Use “Restart &amp; continue” to resume this conversation.</p>
          : (
            <div className="composer">
              <textarea value={draft} placeholder="Message…  (Enter = send / steer, Shift+Enter = newline)"
                onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} />
              <button className="btn green" onClick={send}>Send</button>
            </div>
          )}
      </div>

      {automation && (
        <div className="card">
          <div className="card-head">
            <h2>Automation</h2><span className="spacer" />
            <button className="btn ghost" onClick={() => navigate("/automations")}>Edit in Automations</button>
          </div>
          <p><strong>{automation.name}</strong></p>
          <p className="muted">{automation.task}</p>
          <p className="muted">Mode: {automation.session_mode} · {automation.enabled ? "enabled" : "disabled"}</p>
        </div>
      )}
    </>
  );
}
