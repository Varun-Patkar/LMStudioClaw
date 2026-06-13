// Sessions view: list past/active sessions, start a new one, and a live session
// detail panel that streams over the WebSocket with steering / queue / stop and
// inline consent prompts (US1 + US3).
import { get, post, del, el, toast } from "../api.js";
import { buildRunConfig } from "./runconfig.js";

/** Render the Sessions list + new-session controls. */
export async function renderSessions(root) {
  const hashParts = location.hash.split("/");
  if (hashParts[1] === "detail" && hashParts[2]) {
    return renderSessionDetail(root, hashParts[2]);
  }

  const [sessions, modelsResp, personas, grants] = await Promise.all([
    get("/api/sessions"),
    get("/api/models").catch(() => ({ models: [] })),
    get("/api/personas").catch(() => []),
    get("/api/grants").catch(() => []),
  ]);

  const modelSelect = el("select", {},
    el("option", { value: "" }, "Default model"),
    ...modelsResp.models.map((m) => el("option", { value: m.key }, m.display_name)));
  const personaSelect = el("select", {},
    el("option", { value: "" }, "Default persona"),
    ...personas.map((p) => el("option", { value: p.id }, p.name)));

  // Per-run configuration form (model override + tool/MCP overrides) — US4.
  const runConfig = await buildRunConfig(null, modelsResp.models);

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
    grantsCard(grants, root),
  );
}

/** Build the folder-permissions (grants) management card (US2). */
function grantsCard(grants, root) {
  const body = grants.length
    ? el("table", {},
        el("thead", {}, el("tr", {},
          el("th", {}, "Folder"), el("th", {}, "Scope"),
          el("th", {}, "Access"), el("th", {}, ""))),
        el("tbody", {}, ...grants.map((g) =>
          el("tr", {},
            el("td", {}, el("code", {}, g.path)),
            el("td", {}, g.scope),
            el("td", {}, g.access),
            el("td", {}, el("button", {
              class: "btn red", onclick: async () => {
                try { await del(`/api/grants/${g.id}`); } catch (e) { toast(e.message); }
                root.innerHTML = ""; renderSessions(root);
              },
            }, "Revoke"))))))
    : el("p", { class: "muted" }, "No folder grants. The agent may use the workspace by default.");
  return el("div", { class: "card" }, el("h2", {}, "Folder permissions"), body);
}

/** Build the sessions list table. */
function sessionsTable(sessions) {
  if (!sessions.length) return el("p", { class: "muted" }, "No sessions yet.");
  const rows = sessions.map((s) =>
    el("tr", { onclick: () => { location.hash = `sessions/detail/${s.id}`; },
               style: "cursor:pointer" },
      el("td", {}, el("span", { class: "badge " + s.status }, s.status)),
      el("td", {}, s.trigger_type),
      el("td", {}, s.model_key || "—"),
      el("td", {}, (s.started_at || s.created_at || "").replace("T", " ").slice(0, 19))));
  return el("table", {},
    el("thead", {}, el("tr", {},
      el("th", {}, "Status"), el("th", {}, "Trigger"),
      el("th", {}, "Model"), el("th", {}, "Started"))),
    el("tbody", {}, ...rows));
}

/** Render the live session detail panel with WebSocket streaming. */
async function renderSessionDetail(root, sessionId) {
  const session = await get(`/api/sessions/${sessionId}`);
  const statusBadge = el("span", { class: "badge " + session.status }, session.status);
  const budgetFill = el("div", { style: "width:0%" });
  const budgetLabel = el("span", { class: "muted" }, "");
  const transcript = el("div", { class: "transcript" });
  const consentArea = el("div", {});

  // Seed transcript from stored turns.
  for (const t of session.turns || []) {
    if (t.role === "user" || t.role === "assistant" || t.role === "system") {
      transcript.append(el("div", { class: "msg " + t.role }, t.content || ""));
    }
  }

  const input = el("textarea", { placeholder: "Message…  (Enter = steer / send, Alt+Enter = queue)" });
  const ws = openSocket(sessionId, { statusBadge, budgetFill, budgetLabel, transcript, consentArea });

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
  const backBtn = el("button", { class: "btn ghost", onclick: () => { location.hash = "sessions"; } }, "← Back");

  root.append(
    el("div", { class: "card" },
      el("div", { class: "row" }, backBtn, el("h2", {}, "Session"), statusBadge, el("span", { class: "spacer" }), stopTurn, endBtn),
      el("div", { class: "budget-bar" }, budgetFill), budgetLabel,
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
      case "budget": {
        const pct = Math.min(100, Math.round((evt.used / evt.total) * 100));
        ui.budgetFill.style.width = pct + "%";
        ui.budgetLabel.textContent = `${evt.used} / ${evt.total} tokens (${pct}%)`;
        break;
      }
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
