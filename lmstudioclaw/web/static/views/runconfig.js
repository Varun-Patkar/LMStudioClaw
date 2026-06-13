// Shared per-run configuration form (US4): model, per-tool enable/disable overrides,
// and MCP server selection. Used by both the session-start controls and the automation
// editor. Skills are intentionally NOT shown — they are always globally available and
// are never per-run toggles (FR-031).
import { get, el } from "../api.js";

/**
 * Build a collapsible run-config form.
 *
 * @param {object|null} initial  an existing run_config to pre-fill ({model, tool_overrides, mcp_selection})
 * @param {Array} models  the available models ([{key, display_name}])
 * @param {string|null} defaultKey  the current default model key (named in the blank option, excluded below)
 * @returns {Promise<{element: HTMLElement, getConfig: function}>}
 *   `getConfig()` returns a run_config object, or null when nothing was customized.
 */
export async function buildRunConfig(initial, models, defaultKey) {
  const cfg = initial || {};
  let tools = { builtin: [], tools: [], mcp_servers: [] };
  try { tools = await get("/api/tools"); } catch { /* tools optional */ }

  // Model selector — the blank option names the current default model and that model
  // is excluded from the rest of the list (issue 7).
  const def = models.find((m) => m.key === defaultKey);
  const modelSel = el("select", {},
    el("option", { value: "" }, def ? `Default model (${def.display_name})` : "Default model"),
    ...models.filter((m) => m.key !== defaultKey)
      .map((m) => el("option", { value: m.key }, m.display_name || m.key)));
  if (cfg.model) modelSel.value = cfg.model;

  // Default (built-in) tools are always available and CANNOT be deselected (issue 6):
  // they render as checked + disabled. Only custom/MCP tools are toggleable per run.
  const overrides = cfg.tool_overrides || {};
  const defaultBoxes = tools.builtin.map((t) => {
    const box = el("input", { type: "checkbox" });
    box.checked = true; box.disabled = true; box.dataset.tool = t.name;
    return el("label", { class: "check", title: "Default tool — always available" }, box, t.name);
  });
  const customBoxes = tools.tools.map((t) => {
    const box = el("input", { type: "checkbox" });
    box.checked = overrides[t.name] !== false;
    box.dataset.tool = t.name;
    return el("label", { class: "check" }, box, t.name);
  });

  // MCP server multi-select (none selected = all enabled by default).
  const mcpBoxes = tools.mcp_servers.map((name) => {
    const box = el("input", { type: "checkbox" });
    box.checked = !cfg.mcp_selection || cfg.mcp_selection.includes(name);
    box.dataset.mcp = name;
    return el("label", { class: "check" }, box, name);
  });

  const element = el("details", { class: "run-config" },
    el("summary", {}, "Run configuration (model · tools · MCP)"),
    el("div", { class: "field" }, el("label", {}, "Model"), modelSel),
    el("div", { class: "field" }, el("label", {}, "Default tools (always on)"),
      el("div", { class: "tool-grid" }, ...defaultBoxes)),
    ...(customBoxes.length
      ? [el("div", { class: "field" }, el("label", {}, "Custom tools (uncheck to disable for this run)"),
          el("div", { class: "tool-grid" }, ...customBoxes))]
      : []),
    ...(mcpBoxes.length
      ? [el("div", { class: "field" }, el("label", {}, "MCP servers for this run"),
          el("div", { class: "tool-grid" }, ...mcpBoxes))]
      : []));

  function getConfig() {
    const tool_overrides = {};
    for (const lbl of customBoxes) {
      const box = lbl.querySelector("input");
      if (!box.checked) tool_overrides[box.dataset.tool] = false; // only record disables
    }
    let mcp_selection = null;
    if (mcpBoxes.length) {
      const selected = mcpBoxes
        .map((l) => l.querySelector("input"))
        .filter((b) => b.checked)
        .map((b) => b.dataset.mcp);
      // Only send a selection when it differs from "all".
      if (selected.length !== mcpBoxes.length) mcp_selection = selected;
    }
    const model = modelSel.value || null;
    if (!model && Object.keys(tool_overrides).length === 0 && mcp_selection === null) {
      return null; // nothing customized → use global defaults (FR-032)
    }
    return { model, tool_overrides, mcp_selection };
  }

  return { element, getConfig };
}
