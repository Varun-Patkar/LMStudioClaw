// Sessions view: list past/active sessions, start a new one, and a live session
// detail panel that streams over the WebSocket with steering / queue / stop and
// inline consent prompts (US1 + US3).
import { get, post, del, el, toast, rerender } from "../api.js";
import { buildRunConfig } from "./runconfig.js";

/** Render the Sessions list + new-session controls. */
export async function renderSessions(root) {
  const hashParts = location.hash.split("/");
  if (hashParts[1] === "detail" && hashParts[2]) {
    return renderSessionDetail(root, hashParts[2]);
  }

  const [sessions, modelsResp, personas, settings] = await Promise.all([
    get("/api/sessions"),
    get("/api/models").catch(() => ({ models: [] })),
    get("/api/personas").catch(() => []),
    get("/api/settings").catch(() => ({})),
  ]);

  const modelSelect = modelSelectEl(modelsResp.models, settings.default_model);
  const personaSelect = personaSelectEl(personas);

  // Per-run configuration form (model override + tool/MCP overrides) — US4.
  const runConfig = await buildRunConfig(null, modelsResp.models, settings.default_model);

  const startBtn = el("button", {
    class: "btn green",
    onclick: async () => {
      startBtn.disabled = true;
      try {
        const res = await post("/api/sessions", {
          model: modelSelect.value || null,
          persona_id: personaSelect.value || null,
          run_config: runConfig.getConfig(),
        });
        location.hash = `sessions/detail/${res.session_id}`;
      } catch (e) { toast(e.message); }
      finally { startBtn.disabled = false; }
    },
  }, "Start session");

  root.append(
    el("div", { class: "card" },
      el("h2", {}, "New session"),
      el("div", { class: "row wrap" }, modelSelect, personaSelect, startBtn),
      runConfig.element),
    el("div", { class: "card" },
      el("h2", {}, "Sessions"),
      sessionsTable(sessions)),
  );
}

/**
 * Model <select> whose default option names the current default model in brackets,
 * and which omits that model from the rest of the list to avoid duplication (issue 7).
 */
function modelSelectEl(models, defaultKey) {
  const def = models.find((m) => m.key === defaultKey);
  const defLabel = def ? `Default model (${def.display_name})` : "Default model";
  return el("select", {},
    el("option", { value: "" }, defLabel),
    ...models.filter((m) => m.key !== defaultKey)
      .map((m) => el("option", { value: m.key }, m.display_name)));
}

/**
 * Persona <select> — the blank option means "use the default persona", so the
 * default persona is not listed again (issue 8: no duplicate "Default").
 */
function personaSelectEl(personas) {
  return el("select", {},
    el("option", { value: "" }, "Default persona"),
    ...personas.filter((p) => !p.is_default)
      .map((p) => el("option", { value: p.id }, p.name)));
}

/** Build the sessions list table with restart/delete actions (US3). */
function sessionsTable(sessions) {
  if (!sessions.length) return el("p", { class: "muted" }, "No sessions yet.");
  const rows = sessions.map((s) => {
    const open = () => { location.hash = `sessions/detail/${s.id}`; };
    const isActive = s.status === "loading" || s.status === "active";
    const actions = el("div", { class: "row" },
      el("button", { class: "btn ghost", onclick: open }, "Open"),
      // Restart a finished session with the same config (issue 4).
      isActive ? null : el("button", {
        class: "btn", onclick: async (e) => {
          e.stopPropagation();
          try {
            const res = await post(`/api/sessions/${s.id}/restart`, {});
            location.hash = `sessions/detail/${res.session_id}`;
          } catch (err) { toast(err.message); }
        },
      }, "Restart"),
      isActive ? null : el("button", {
        class: "btn red", onclick: async (e) => {
          e.stopPropagation();
          if (!confirm("Delete this session and its transcript?")) return;
          try { await del(`/api/sessions/${s.id}`); rerender(); }
          catch (err) { toast(err.message); }
        },
      }, "Delete"));
    return el("tr", {},
      el("td", {}, el("span", { class: "badge " + s.status }, s.status)),
      el("td", {}, s.trigger_type),
      el("td", {}, s.model_key || "—"),
      el("td", {}, (s.started_at || s.created_at || "").replace("T", " ").slice(0, 19)),
      el("td", {}, actions));
  });
  return el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "Status"), el("th", {}, "Trigger"),
      el("th", {}, "Model"), el("th", {}, "Started"), el("th", {}, ""))),
    el("tbody", {}, ...rows));
}

/** Render the live session detail panel with WebSocket streaming. */
async function renderSessionDetail(root, sessionId) {
  const session = await get(`/api/sessions/${sessionId}`);
  const terminal = ["completed", "failed", "stopped"].includes(session.status);
  const statusBadge = el("span", { class: "badge " + session.status }, session.status);
  const ctxTotal = session.context_length || 0;
  const transcript = el("div", { class: "transcript" });
  const consentArea = el("div", {});

  // Seed transcript from stored turns.
  for (const t of session.turns || []) {
    if (t.role === "user" || t.role === "assistant" || t.role === "system") {
      transcript.append(el("div", { class: "msg " + t.role }, t.content || ""));
    }
  }

  const backBtn = el("button", { class: "btn ghost", onclick: () => { location.hash = "sessions"; } }, "← Back");

  if (terminal) {
    // A finished session is read-only: offer Restart (reuse config) + Delete (issue 4).
    const restartBtn = el("button", { class: "btn green", onclick: async () => {
      try {
        const res = await post(`/api/sessions/${sessionId}/restart`, {});
        location.hash = `sessions/detail/${res.session_id}`;
      } catch (e) { toast(e.message); }
    } }, "Restart session");
    const deleteBtn = el("button", { class: "btn red", onclick: async () => {
      if (!confirm("Delete this session and its transcript?")) return;
      try { await del(`/api/sessions/${sessionId}`); location.hash = "sessions"; }
      catch (e) { toast(e.message); }
    } }, "Delete");
    root.append(
      el("div", { class: "card" },
        el("div", { class: "row" }, backBtn, el("h2", {}, "Session"), statusBadge,
          el("span", { class: "spacer" }), restartBtn, deleteBtn),
        session.failure_reason ? el("p", { class: "muted" }, `Reason: ${session.failure_reason}`) : null,
        transcript.children.length ? transcript : el("p", { class: "muted" }, "No transcript recorded.")),
    );
    if (session.trigger_type === "automation" && session.automation_id) {
      root.append(await automationPanel(session.automation_id));
    }
    return;
  }

  const input = el("textarea", { placeholder: "Message…  (Enter = steer / send, Alt+Enter = queue)" });
  const gauge = tokenGauge(ctxTotal);
  const ws = openSocket(sessionId, { statusBadge, gauge, transcript, consentArea });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = input.value.trim();
      if (!text) return;
      ws.send(JSON.stringify({ type: e.altKey ? "queue" : (isGenerating ? "steer" : "message"), text }));
      transcript.append(el("div", { class: "msg user" }, text));
      input.value = "";
    }
  });

  const stopTurn = el("button", { class: "btn", onclick: () => ws.send(JSON.stringify({ type: "stop", scope: "turn" })) }, "Stop turn");
  const endBtn = el("button", { class: "btn red", onclick: () => ws.send(JSON.stringify({ type: "stop", scope: "session" })) }, "End session");

  root.append(
    el("div", { class: "card" },
      el("div", { class: "row" }, backBtn, el("h2", {}, "Session"), statusBadge,
        el("span", { class: "spacer" }), gauge.element, stopTurn, endBtn),
      consentArea,
      transcript,
      el("div", { class: "composer" }, input)),
  );

  // For an automation run, show its definition alongside, with an edit affordance (FR-022).
  if (session.trigger_type === "automation" && session.automation_id) {
    root.append(await automationPanel(session.automation_id));
  }
}

/** Build a read-only automation definition panel with a link to edit it (FR-022). */
async function automationPanel(automationId) {
  try {
    const list = await get("/api/automations");
    const a = list.find((x) => x.id === automationId);
    if (!a) return el("div", {});
    return el("div", { class: "card" },
      el("div", { class: "row" }, el("h2", {}, "Automation"),
        el("span", { class: "spacer" }),
        el("button", { class: "btn ghost", onclick: () => { location.hash = "automations"; } }, "Edit in Automations")),
      el("p", {}, el("strong", {}, a.name)),
      el("p", { class: "muted" }, a.task || ""),
      el("p", { class: "muted" }, `Mode: ${a.session_mode} · ${a.enabled ? "enabled" : "disabled"}`));
  } catch {
    return el("div", {});
  }
}

let isGenerating = false;

/**
 * A circular token gauge that fills as context is used, with a hover tooltip showing
 * the usage breakdown (used/total, %, compaction limit, per-role split). Returns
 * `{ element, update(evt) }` where `evt` is a `budget` event from the engine.
 */
function tokenGauge(ctxTotal) {
  const R = 13, C = 2 * Math.PI * R;
  const track = svgEl("circle", { cx: 16, cy: 16, r: R, class: "gauge-track" });
  const fill = svgEl("circle", {
    cx: 16, cy: 16, r: R, class: "gauge-fill",
    "stroke-dasharray": C.toFixed(1), "stroke-dashoffset": C.toFixed(1),
    transform: "rotate(-90 16 16)",
  });
  const pctText = svgEl("text", { x: 16, y: 16, class: "gauge-text" }, "0%");
  const svg = svgEl("svg", { viewBox: "0 0 32 32", class: "token-gauge", width: 32, height: 32 },
    track, fill, pctText);
  const tip = el("div", { class: "gauge-tip" }, "Token usage will appear here");
  const element = el("div", { class: "gauge-wrap", title: "" }, svg, tip);

  function update(evt) {
    const total = evt.total || ctxTotal || 0;
    const used = evt.used || 0;
    const frac = total ? Math.min(1, used / total) : 0;
    const pct = Math.round(frac * 100);
    fill.setAttribute("stroke-dashoffset", (C * (1 - frac)).toFixed(1));
    fill.classList.toggle("warn", evt.threshold && frac >= evt.threshold);
    pctText.textContent = pct + "%";
    const b = evt.breakdown || {};
    const rows = ["system", "user", "assistant", "tool"]
      .filter((k) => b[k])
      .map((k) => `<div class="grow"><span>${k}</span><span>${b[k].toLocaleString()}</span></div>`)
      .join("");
    const limit = evt.limit ? `<div class="grow muted"><span>compaction at</span><span>${evt.limit.toLocaleString()}</span></div>` : "";
    tip.innerHTML =
      `<div class="grow ghead"><span>${used.toLocaleString()} / ${total.toLocaleString()}</span><span>${pct}%</span></div>${rows}${limit}`;
  }
  return { element, update };
}

/** Create an SVG element with attributes + children (namespaced). */
function svgEl(tag, attrs = {}, ...children) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  for (const c of children) node.append(c.nodeType ? c : document.createTextNode(String(c)));
  return node;
}

/** Open the session WebSocket and wire incoming events to the UI. */
function openSocket(sessionId, ui) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/sessions/${sessionId}`);
  let currentAssistant = null;

  ws.onmessage = (ev) => {
    const evt = JSON.parse(ev.data);
    switch (evt.type) {
      case "status":
        ui.statusBadge.textContent = evt.status;
        ui.statusBadge.className = "badge " + evt.status;
        isGenerating = evt.status === "active";
        break;
      case "token":
        if (!currentAssistant) {
          currentAssistant = el("div", { class: "msg assistant" }, "");
          ui.transcript.append(currentAssistant);
        }
        currentAssistant.textContent += evt.text;
        break;
      case "tool_call":
        currentAssistant = null;
        ui.transcript.append(el("div", { class: "msg tool" }, `🔧 ${evt.name}(${JSON.stringify(evt.args)})`));
        break;
      case "tool_result":
        ui.transcript.append(el("div", { class: "msg tool" }, `→ ${evt.ok ? "ok" : "error"}: ${evt.summary || ""}`));
        break;
      case "budget":
        ui.gauge.update(evt);
        break;
      case "compaction":
        ui.transcript.append(el("div", { class: "msg system" }, `Context compacted: ${evt.tokens_before} → ${evt.tokens_after} tokens`));
        currentAssistant = null;
        break;
      case "consent_request":
        showConsent(ui.consentArea, ws, sessionId, evt);
        break;
      case "error":
        ui.transcript.append(el("div", { class: "msg system" }, `Error: ${evt.reason}`));
        break;
    }
  };
  ws.onclose = () => { isGenerating = false; };
  return ws;
}

/** Render an inline consent prompt and POST the decision to /api/grants. */
function showConsent(area, ws, sessionId, evt) {
  area.innerHTML = "";
  const decide = async (decision) => {
    try {
      await post("/api/grants", {
        request_id: evt.request_id, session_id: sessionId, path: evt.path,
        decision, access: evt.access,
      });
    } catch (e) { toast(e.message); }
    area.innerHTML = "";
  };
  area.append(el("div", { class: "consent" },
    el("div", {}, `Agent requests ${evt.access} access to:`),
    el("code", {}, evt.path),
    el("div", { class: "row" },
      el("button", { class: "btn", onclick: () => decide("session") }, "Allow for session"),
      el("button", { class: "btn green", onclick: () => decide("permanent") }, "Allow permanently"),
      el("button", { class: "btn red", onclick: () => decide("deny") }, "Deny"))));
}
