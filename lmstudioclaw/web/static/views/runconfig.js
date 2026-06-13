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
 * @returns {Promise<{element: HTMLElement, getConfig: function}>}
 *   `getConfig()` returns a run_config object, or null when nothing was customized.
 */
export async function buildRunConfig(initial, models) {
  const cfg = initial || {};
  let tools = { builtin: [], tools: [], mcp_servers: [] };
  try { tools = await get("/api/tools"); } catch { /* tools optional */ }

  // Model selector (blank = default).
  const modelSel = el("select", {},
    el("option", { value: "" }, "Default model"),
    ...models.map((m) => el("option", { value: m.key }, m.display_name || m.key)));
  if (cfg.model) modelSel.value = cfg.model;

  // Per-tool override checkboxes (tri-state collapsed to checked/unchecked; unchecked
  // means "disabled for this run"). Default reflects the saved override or enabled.
  const overrides = cfg.tool_overrides || {};
  const allTools = [
    ...tools.builtin.map((t) => t.name),
    ...tools.tools.map((t) => t.name),
  ];
  const toolBoxes = allTools.map((name) => {
    const box = el("input", { type: "checkbox" });
    box.checked = overrides[name] !== false; // default on unless explicitly disabled
    box.dataset.tool = name;
    return el("label", { class: "check" }, box, name);
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
    el("div", { class: "field" }, el("label", {}, "Tools (uncheck to disable for this run)"),
      el("div", { class: "tool-grid" }, ...toolBoxes)),
    ...(mcpBoxes.length
      ? [el("div", { class: "field" }, el("label", {}, "MCP servers for this run"),
          el("div", { class: "tool-grid" }, ...mcpBoxes))]
      : []));

  function getConfig() {
    const tool_overrides = {};
    for (const lbl of toolBoxes) {
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
