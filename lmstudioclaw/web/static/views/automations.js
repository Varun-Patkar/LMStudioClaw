// Automations view: list, create (Daily multi-weekday+time or Interval), toggle
// session mode, enable/disable, run-now, delete (US4).
import { get, post, patch, del, el, toast } from "../api.js";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** Render the Automations view. */
export async function renderAutomations(root) {
  const [automations, personas] = await Promise.all([
    get("/api/automations"),
    get("/api/personas").catch(() => []),
  ]);
  root.append(newAutomationCard(personas, root), listCard(automations, root));
}

/** Build the create-automation form card. */
function newAutomationCard(personas, root) {
  const name = el("input", { placeholder: "Name" });
  const task = el("textarea", { placeholder: "Task / instruction for the agent" });

  const scheduleType = el("select", {},
    el("option", { value: "daily" }, "Daily"),
    el("option", { value: "interval" }, "Interval"));

  // Daily controls.
  const dayChecks = WEEKDAYS.map((d, i) =>
    el("label", { class: "row", style: "gap:4px" },
      el("input", { type: "checkbox", value: String(i) }), d));
  const dailyTime = el("input", { type: "time", value: "09:00" });
  const dailyBox = el("div", {},
    el("div", { class: "row wrap" }, ...dayChecks),
    el("div", { class: "row" }, el("span", { class: "muted" }, "Time"), dailyTime));

  // Interval controls.
  const intervalValue = el("input", { type: "number", value: "1", min: "1", style: "width:80px" });
  const intervalUnit = el("select", {},
    el("option", { value: "minutes" }, "minutes"),
    el("option", { value: "hours" }, "hours"),
    el("option", { value: "days" }, "days"));
  const intervalBox = el("div", { style: "display:none" },
    el("div", { class: "row" }, el("span", { class: "muted" }, "Every"), intervalValue, intervalUnit));

  scheduleType.addEventListener("change", () => {
    const daily = scheduleType.value === "daily";
    dailyBox.style.display = daily ? "" : "none";
    intervalBox.style.display = daily ? "none" : "";
  });

  const sessionMode = el("select", {},
    el("option", { value: "new" }, "New session each run"),
    el("option", { value: "persistent" }, "Persistent session (resume)"));
  const personaSel = el("select", {},
    el("option", { value: "" }, "Default persona"),
    ...personas.map((p) => el("option", { value: p.id }, p.name)));

  const createBtn = el("button", { class: "btn green", onclick: async () => {
    const body = {
      name: name.value.trim(), task: task.value.trim(),
      schedule_type: scheduleType.value,
      session_mode: sessionMode.value,
      persona_id: personaSel.value || null,
    };
    if (scheduleType.value === "daily") {
      body.daily_days = dayChecks.filter((l) => l.querySelector("input").checked)
        .map((l) => Number(l.querySelector("input").value));
      body.daily_time = dailyTime.value;
    } else {
      body.interval_unit = intervalUnit.value;
      body.interval_value = Number(intervalValue.value);
    }
    try {
      await post("/api/automations", body);
      root.innerHTML = ""; renderAutomations(root);
    } catch (e) { toast(e.message); }
  } }, "Create automation");

  return el("div", { class: "card" },
    el("h2", {}, "New automation"),
    name, task,
    el("div", { class: "row" }, el("span", { class: "muted" }, "Schedule"), scheduleType),
    dailyBox, intervalBox,
    el("div", { class: "row" }, el("span", { class: "muted" }, "Mode"), sessionMode, personaSel),
    createBtn);
}

/** Build the automations list card. */
function listCard(automations, root) {
  if (!automations.length) {
    return el("div", { class: "card" }, el("h2", {}, "Automations"),
      el("p", { class: "muted" }, "No automations yet."));
  }
  const rows = automations.map((a) =>
    el("tr", {},
      el("td", {}, a.name),
      el("td", {}, describeSchedule(a)),
      el("td", {}, a.session_mode),
      el("td", {}, a.last_run_result || "—"),
      el("td", {}, (a.next_run_at || "").replace("T", " ").slice(0, 16) || "—"),
      el("td", {}, el("div", { class: "row" },
        el("button", { class: "btn ghost", onclick: () => toggle(a, root) },
          a.enabled ? "Disable" : "Enable"),
        el("button", { class: "btn", onclick: () => runNow(a, root) }, "Run now"),
        el("button", { class: "btn red", onclick: () => remove(a, root) }, "Delete")))));
  return el("div", { class: "card" }, el("h2", {}, "Automations"),
    el("table", {},
      el("thead", {}, el("tr", {},
        el("th", {}, "Name"), el("th", {}, "Schedule"), el("th", {}, "Mode"),
        el("th", {}, "Last"), el("th", {}, "Next"), el("th", {}, ""))),
      el("tbody", {}, ...rows)));
}

/** Human-readable schedule summary. */
function describeSchedule(a) {
  if (a.schedule_type === "daily") {
    const days = (a.daily_days || []).map((d) => WEEKDAYS[d]).join(", ");
    return `Daily ${days} at ${a.daily_time}`;
  }
  return `Every ${a.interval_value} ${a.interval_unit}`;
}

async function toggle(a, root) {
  try { await patch(`/api/automations/${a.id}`, { enabled: !a.enabled }); }
  catch (e) { toast(e.message); }
  root.innerHTML = ""; renderAutomations(root);
}

async function runNow(a, root) {
  try { await post(`/api/automations/${a.id}/run`, {}); toast("Queued."); }
  catch (e) { toast(e.message); }
}

async function remove(a, root) {
  try { await del(`/api/automations/${a.id}`); }
  catch (e) { toast(e.message); }
  root.innerHTML = ""; renderAutomations(root);
}
