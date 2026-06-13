// Settings, model management, and personas view (US7).
import { get, post, patch, del, el, toast } from "../api.js";

/** Render the Settings view with all configuration sections. */
export async function renderSettings(root) {
  const [settings, modelsResp, personas] = await Promise.all([
    get("/api/settings"),
    get("/api/models").catch(() => ({ models: [], connected: false })),
    get("/api/personas").catch(() => []),
  ]);
  root.append(
    generalCard(settings, modelsResp.models),
    timeoutsCard(settings),
    personasCard(personas, root),
    advancedCard(modelsResp, root),
  );
}

/** General settings: theme, default model, startup, notifications. */
function generalCard(settings, models) {
  const theme = el("select", {},
    ...["system", "dark", "light"].map((t) =>
      el("option", { value: t, ...(t === settings.theme ? { selected: "" } : {}) }, t)));
  theme.addEventListener("change", async () => {
    document.documentElement.setAttribute("data-theme", theme.value);
    await save({ theme: theme.value });
  });

  const defaultModel = el("select", {},
    el("option", { value: "" }, "None"),
    ...models.map((m) => el("option",
      { value: m.key, ...(m.key === settings.default_model ? { selected: "" } : {}) },
      m.display_name)));
  defaultModel.addEventListener("change", () => save({ default_model: defaultModel.value || null }));

  const startup = checkbox(settings.startup_launch, (v) => save({ startup_launch: v }));

  return el("div", { class: "card" },
    el("h2", {}, "General"),
    field("Theme", theme),
    field("Default model", defaultModel),
    field("Launch on login (start minimized)", startup));
}

/** Timeouts, retention, and compression. */
function timeoutsCard(settings) {
  const idleUnload = checkbox(settings.idle_unload, (v) => save({ idle_unload: v }));
  const idleTimeout = numberInput(settings.session_idle_timeout, (v) => save({ session_idle_timeout: v }));
  const maxRun = numberInput(settings.max_run_duration, (v) => save({ max_run_duration: v }));
  const retention = numberInput(settings.retention_days, (v) => save({ retention_days: v }));
  const threshold = numberInput(settings.compression_threshold, (v) => save({ compression_threshold: v }), 0.01);
  const port = numberInput(settings.web_port, (v) => save({ web_port: v }));
  return el("div", { class: "card" },
    el("h2", {}, "Runtime"),
    field("Unload model on idle", idleUnload),
    field("Idle timeout (s)", idleTimeout),
    field("Max run duration (s)", maxRun),
    field("Retention (days)", retention),
    field("Compression threshold (0–1)", threshold),
    field("Web port", port));
}

/** Personas library: edit default, create, delete. */
function personasCard(personas, root) {
  const rows = personas.map((p) => {
    const instr = el("textarea", {}, p.instructions);
    instr.value = p.instructions;
    const saveBtn = el("button", { class: "btn", onclick: async () => {
      try { await patch(`/api/personas/${p.id}`, { instructions: instr.value }); toast("Saved."); }
      catch (e) { toast(e.message); }
    } }, "Save");
    const delBtn = p.is_default ? null : el("button", { class: "btn red", onclick: async () => {
      try { await del(`/api/personas/${p.id}`); } catch (e) { toast(e.message); }
      root.innerHTML = ""; renderSettings(root);
    } }, "Delete");
    return el("div", { class: "card" },
      el("div", { class: "row" }, el("strong", {}, p.name),
        p.is_default ? el("span", { class: "badge" }, "default") : null),
      instr, el("div", { class: "row" }, saveBtn, delBtn));
  });

  const newName = el("input", { placeholder: "New persona name" });
  const newInstr = el("textarea", { placeholder: "Instructions" });
  const createBtn = el("button", { class: "btn green", onclick: async () => {
    if (!newName.value.trim()) return;
    try { await post("/api/personas", { name: newName.value.trim(), instructions: newInstr.value }); }
    catch (e) { toast(e.message); }
    root.innerHTML = ""; renderSettings(root);
  } }, "Create persona");

  return el("div", { class: "card" }, el("h2", {}, "Personas"), ...rows,
    el("div", { class: "card" }, el("h3", {}, "New persona"), newName, newInstr, createBtn));
}

/** Advanced → Model Management: per-model context + manual load/unload/warmup. */
function advancedCard(modelsResp, root) {
  if (!modelsResp.connected) {
    return el("div", { class: "card" }, el("h2", {}, "Advanced → Model Management"),
      el("p", { class: "muted" }, "LM Studio is not reachable."));
  }
  const rows = modelsResp.models.map((m) => {
    const ctx = el("input", { type: "number", value: String(m.max_context_length), style: "width:120px" });
    return el("tr", {},
      el("td", {}, m.display_name),
      el("td", {}, m.is_loaded ? el("span", { class: "badge active" }, "loaded") : "—"),
      el("td", {}, ctx),
      el("td", {}, el("div", { class: "row" },
        el("button", { class: "btn", onclick: async () => {
          try { await post("/api/models/context-pref", { model_key: m.key, context_length: Number(ctx.value) }); toast("Saved."); }
          catch (e) { toast(e.message); }
        } }, "Set context"),
        loadButton(m, ctx, root))));
  });
  const unloadBtn = el("button", { class: "btn red", onclick: async () => {
    unloadBtn.disabled = true;
    try { await post("/api/models/unload", {}); toast("Unloaded."); reloadSettings(root); }
    catch (e) { toast(e.message); unloadBtn.disabled = false; }
  } }, "Unload current");
  // Nothing loaded → nothing to unload (FR: disabled when no active model).
  const anyLoaded = modelsResp.models.some((m) => m.is_loaded);
  if (!anyLoaded) { unloadBtn.disabled = true; unloadBtn.title = "No model is currently loaded"; }
  return el("div", { class: "card" },
    el("div", { class: "row" }, el("h2", {}, "Advanced → Model Management"),
      el("span", { class: "spacer" }), unloadBtn),
    el("table", {}, el("thead", {}, el("tr", {},
      el("th", {}, "Model"), el("th", {}, "State"), el("th", {}, "Context"), el("th", {}, ""))),
      el("tbody", {}, ...rows)));
}

/** Re-render the whole Settings view (used after a load/unload to refresh state). */
function reloadSettings(root) {
  root.innerHTML = "";
  renderSettings(root);
}

// -- helpers ----------------------------------------------------------------

/**
 * A "Load" button that gives immediate non-blocking feedback (FR-004): on click it
 * shows a spinner and disables itself. The request awaits the actual load result, then
 * refreshes the table so the State column + Unload button reflect the new state; the
 * top-right indicator also updates live via /ws/status (no page reload needed).
 */
function loadButton(m, ctx, root) {
  const btn = el("button", { class: "btn green" }, "Load");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.innerHTML = "";
    btn.append(el("span", { class: "spinner" }), document.createTextNode(" Loading…"));
    try {
      await post("/api/models/load", { model_key: m.key, context_length: Number(ctx.value) });
      toast(`Loaded ${m.display_name}.`);
      reloadSettings(root);
    } catch (e) {
      toast(e.message);
      btn.disabled = false;
      btn.textContent = "Load";
    }
  });
  return btn;
}

async function save(patchBody) {
  try { await patch("/api/settings", patchBody); } catch (e) { toast(e.message); }
}

function field(label, control) {
  return el("div", { class: "set-field" },
    el("label", {}, label), control);
}

function checkbox(checked, onChange) {
  const box = el("input", { type: "checkbox", class: "switch" });
  box.checked = !!checked;
  box.addEventListener("change", () => onChange(box.checked));
  return box;
}

function numberInput(value, onChange, step) {
  const input = el("input", { type: "number", value: String(value) });
  if (step) input.step = String(step);
  input.addEventListener("change", () => onChange(Number(input.value)));
  return input;
}
