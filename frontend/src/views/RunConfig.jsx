import { useEffect, useState } from "react";
import { get } from "../api.js";

/**
 * Collapsible per-run configuration: model, custom-tool toggles, MCP selection.
 * Default (built-in) tools are always available and cannot be disabled (shown locked).
 * Calls `onChange(config|null)` whenever the selection changes — null means "use
 * global defaults".
 */
export default function RunConfig({ models, defaultModel, onChange }) {
  const [tools, setTools] = useState({ builtin: [], tools: [], mcp_servers: [] });
  const [model, setModel] = useState("");
  const [disabled, setDisabled] = useState({}); // custom tool name -> true(disabled)
  const [mcpOff, setMcpOff] = useState({});      // server name -> true(de-selected)

  useEffect(() => { get("/api/tools").then(setTools).catch(() => {}); }, []);

  useEffect(() => {
    const tool_overrides = {};
    Object.keys(disabled).forEach((k) => { if (disabled[k]) tool_overrides[k] = false; });
    const offNames = Object.keys(mcpOff).filter((k) => mcpOff[k]);
    const mcp_selection = offNames.length
      ? tools.mcp_servers.filter((s) => !mcpOff[s]) : null;
    const cfg = (!model && Object.keys(tool_overrides).length === 0 && mcp_selection === null)
      ? null : { model: model || null, tool_overrides, mcp_selection };
    onChange(cfg);
  }, [model, disabled, mcpOff, tools]); // eslint-disable-line react-hooks/exhaustive-deps

  const def = models.find((m) => m.key === defaultModel);

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
            <label className="check" key={t.name} title="Default tool — always available">
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
              <label className="check" key={t.name}>
                <input type="checkbox" checked={!disabled[t.name]}
                  onChange={(e) => setDisabled((d) => ({ ...d, [t.name]: !e.target.checked }))} /> {t.name}
              </label>
            ))}
          </div>
        </div>
      )}

      {tools.mcp_servers.length > 0 && (
        <div className="field">
          <label>MCP servers for this run</label>
          <div className="tool-grid">
            {tools.mcp_servers.map((s) => (
              <label className="check" key={s}>
                <input type="checkbox" checked={!mcpOff[s]}
                  onChange={(e) => setMcpOff((m) => ({ ...m, [s]: !e.target.checked }))} /> {s}
              </label>
            ))}
          </div>
        </div>
      )}
    </details>
  );
}
