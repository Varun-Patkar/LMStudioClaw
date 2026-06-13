import { useEffect, useState } from "react";
import { get } from "../api.js";

/**
 * Collapsible per-run configuration: model, custom-tool toggles, and MCP selection
 * with **per-server and per-tool** granularity (like VS Code's MCP tool picker).
 *
 * Default (built-in) tools are always available and cannot be disabled (shown locked).
 * Hover any tool/server to see its description. Calls `onChange(config|null)` whenever
 * the selection changes — `null` means "use global defaults".
 *
 * Resolution (matches the backend, most-granular-wins): an unchecked server is removed
 * from `mcp_selection` (whole server off for the run); a checked server stays active,
 * and any unchecked tool under it becomes a `tool_overrides["{server}__{tool}"] = false`.
 */
export default function RunConfig({ models, defaultModel, onChange }) {
  const [tools, setTools] = useState({ builtin: [], tools: [], mcp_servers: [], mcp: [] });
  const [model, setModel] = useState("");
  const [disabled, setDisabled] = useState({});   // custom tool name -> true(disabled)
  const [mcpOff, setMcpOff] = useState({});        // server name -> true(de-selected)
  const [toolOff, setToolOff] = useState({});      // mcp tool id  -> true(disabled)
  const [expanded, setExpanded] = useState({});    // server name -> true(expanded)

  useEffect(() => { get("/api/tools").then(setTools).catch(() => {}); }, []);

  useEffect(() => {
    // Custom-tool overrides + per-MCP-tool overrides share the tool_overrides map.
    const tool_overrides = {};
    Object.keys(disabled).forEach((k) => { if (disabled[k]) tool_overrides[k] = false; });
    Object.keys(toolOff).forEach((k) => { if (toolOff[k]) tool_overrides[k] = false; });

    // A server is "selected" unless its box is unchecked. null = all (no change).
    const servers = (tools.mcp || []).map((m) => m.name);
    const offNames = Object.keys(mcpOff).filter((k) => mcpOff[k]);
    const mcp_selection = offNames.length ? servers.filter((s) => !mcpOff[s]) : null;

    const noChange = !model && Object.keys(tool_overrides).length === 0 && mcp_selection === null;
    onChange(noChange ? null : { model: model || null, tool_overrides, mcp_selection });
  }, [model, disabled, mcpOff, toolOff, tools]); // eslint-disable-line react-hooks/exhaustive-deps

  const def = models.find((m) => m.key === defaultModel);
  const mcpServers = tools.mcp && tools.mcp.length ? tools.mcp
    : (tools.mcp_servers || []).map((n) => ({ name: n, tools: [], description: "" }));

  return (
    <details className="run-config">
      <summary>Run configuration (model · tools · MCP)</summary>

      <div className="field">
        <label>Model</label>
        <select value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="">{def ? `Default model (${def.display_name})` : "Default model"}</option>
          {models.filter((m) => m.key !== defaultModel)
            .map((m) => <option key={m.key} value={m.key}>{m.display_name || m.key}</option>)}
        </select>
      </div>

      <div className="field">
        <label>Default tools (always on)</label>
        <div className="tool-grid">
          {tools.builtin.map((t) => (
            <label className="check" key={t.name} title={t.description || "Default tool — always available"}>
              <input type="checkbox" checked disabled readOnly /> {t.name}
            </label>
          ))}
        </div>
      </div>

      {tools.tools.length > 0 && (
        <div className="field">
          <label>Custom tools (uncheck to disable for this run)</label>
          <div className="tool-grid">
            {tools.tools.map((t) => (
              <label className="check" key={t.name} title={t.description || t.name}>
                <input type="checkbox" checked={!disabled[t.name]}
                  onChange={(e) => setDisabled((d) => ({ ...d, [t.name]: !e.target.checked }))} /> {t.name}
              </label>
            ))}
          </div>
        </div>
      )}

      {mcpServers.length > 0 && (
        <div className="field">
          <label>MCP servers &amp; tools for this run</label>
          <div className="mcp-tree">
            {mcpServers.map((srv) => {
              const serverOn = !mcpOff[srv.name];
              const isOpen = !!expanded[srv.name];
              return (
                <div className="mcp-node" key={srv.name}>
                  <div className="mcp-server-row">
                    {srv.tools.length > 0
                      ? <button type="button" className="mcp-twisty"
                          onClick={() => setExpanded((e) => ({ ...e, [srv.name]: !isOpen }))}>
                          {isOpen ? "▾" : "▸"}
                        </button>
                      : <span className="mcp-twisty placeholder" />}
                    <label className="check" title={srv.description || srv.name}>
                      <input type="checkbox" checked={serverOn}
                        onChange={(e) => setMcpOff((m) => ({ ...m, [srv.name]: !e.target.checked }))} />
                      <strong>{srv.name}</strong>
                      {srv.status === "connect_failed" && <span className="mcp-failed"> failed</span>}
                    </label>
                    {srv.tools.length > 0 && <span className="muted mcp-count">{srv.tools.length} tools</span>}
                  </div>
                  {isOpen && srv.tools.length > 0 && (
                    <div className="mcp-tools">
                      {srv.tools.map((t) => (
                        <label className="check" key={t.id} title={t.description || t.name}>
                          <input type="checkbox" disabled={!serverOn}
                            checked={serverOn && !toolOff[t.id]}
                            onChange={(e) => setToolOff((o) => ({ ...o, [t.id]: !e.target.checked }))} />
                          {t.name}
                          {t.description && <span className="mcp-tool-desc"> — {t.description}</span>}
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </details>
  );
}
