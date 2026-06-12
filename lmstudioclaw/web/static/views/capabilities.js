// Skills, tools, MCP servers, and secrets view (built out in Phases 7-8 / US5-US6).
import { get, post, patch, put, del, el, toast } from "../api.js";

/** Render the Capabilities view: skills, tools, MCP servers, and secrets. */
export async function renderCapabilities(root) {
  const [caps, secrets] = await Promise.all([
    get("/api/capabilities").catch(() => []),
    get("/api/secrets").catch(() => []),
  ]);
  const byKind = { skill: [], tool: [], mcp: [] };
  for (const c of caps) (byKind[c.kind] || (byKind[c.kind] = [])).push(c);

  const refresh = el("button", { class: "btn", onclick: async () => {
    try { await post("/api/capabilities/refresh", {}); } catch (e) { toast(e.message); }
    root.innerHTML = ""; renderCapabilities(root);
  } }, "Rescan");

  root.append(
    el("div", { class: "card" }, el("div", { class: "row" },
      el("h2", {}, "Skills & Tools"), el("span", { class: "spacer" }), refresh)),
    capabilityCard("Skills (SKILL.md)", byKind.skill, root),
    capabilityCard("Custom tools", byKind.tool, root),
    mcpCard(byKind.mcp, root),
    secretsCard(secrets, root),
  );
}

/** Build a capability list card with enable/disable + trust controls. */
function capabilityCard(title, items, root) {
  const body = items.length
    ? el("table", {},
        el("thead", {}, el("tr", {},
          el("th", {}, "Name"), el("th", {}, "Status"),
          el("th", {}, "Description"), el("th", {}, ""))),
        el("tbody", {}, ...items.map((c) => capabilityRow(c, root))))
    : el("p", { class: "muted" }, "None found.");
  return el("div", { class: "card" }, el("h3", {}, title), body);
}

/** One capability row. */
function capabilityRow(c, root) {
  const actions = [];
  if (c.kind === "tool" && !c.trust_confirmed) {
    actions.push(el("button", { class: "btn amber", onclick: async () => {
      if (!confirm("Custom tools run arbitrary code on your machine. Trust this tool?")) return;
      try { await patch(`/api/capabilities/${c.id}`, { trust_confirmed: true }); }
      catch (e) { toast(e.message); }
      root.innerHTML = ""; renderCapabilities(root);
    } }, "Confirm trust"));
  }
  if (c.status === "valid" || c.status === "disabled") {
    actions.push(el("button", { class: "btn ghost", onclick: async () => {
      try { await patch(`/api/capabilities/${c.id}`, { enabled: !c.enabled }); }
      catch (e) { toast(e.message); }
      root.innerHTML = ""; renderCapabilities(root);
    } }, c.enabled ? "Disable" : "Enable"));
  }
  return el("tr", {},
    el("td", {}, c.name),
    el("td", {}, el("span", { class: "badge " + (c.status === "valid" ? "active" : c.status) },
      c.enabled ? "enabled" : c.status)),
    el("td", { class: "muted" }, c.description || ""),
    el("td", {}, el("div", { class: "row" }, ...actions)));
}

/** MCP servers card with an add form. */
function mcpCard(items, root) {
  const name = el("input", { placeholder: "Server name" });
  const command = el("input", { placeholder: "Command (stdio) e.g. npx" });
  const argsInput = el("input", { placeholder: "Args (space-separated)" });
  const url = el("input", { placeholder: "URL (for HTTP servers)" });
  const addBtn = el("button", { class: "btn green", onclick: async () => {
    try {
      await post("/api/capabilities/mcp", {
        name: name.value.trim(), command: command.value.trim() || null,
        args: argsInput.value.trim() ? argsInput.value.trim().split(/\s+/) : null,
        url: url.value.trim() || null,
      });
      root.innerHTML = ""; renderCapabilities(root);
    } catch (e) { toast(e.message); }
  } }, "Add MCP server");
  const list = items.length
    ? el("tbody", {}, ...items.map((c) => capabilityRow(c, root)))
    : el("tbody", {}, el("tr", {}, el("td", { colspan: "4", class: "muted" }, "No MCP servers.")));
  return el("div", { class: "card" }, el("h3", {}, "MCP servers"),
    el("table", {}, el("thead", {}, el("tr", {},
      el("th", {}, "Name"), el("th", {}, "Status"), el("th", {}, "Description"), el("th", {}, ""))),
      list),
    el("div", { class: "row wrap" }, name, command, argsInput, url, addBtn));
}

/** Secrets card: list ref names + owners only; write-only value entry (FR-076-078). */
function secretsCard(secrets, root) {
  const ref = el("input", { placeholder: "Reference name" });
  const value = el("input", { type: "password", placeholder: "Secret value (write-only)" });
  const addBtn = el("button", { class: "btn green", onclick: async () => {
    if (!ref.value.trim()) return;
    try { await put(`/api/secrets/${encodeURIComponent(ref.value.trim())}`, { value: value.value }); }
    catch (e) { toast(e.message); }
    root.innerHTML = ""; renderCapabilities(root);
  } }, "Save secret");
  const rows = secrets.map((s) =>
    el("tr", {},
      el("td", {}, s.ref_name),
      el("td", {}, s.owner),
      el("td", {}, el("button", { class: "btn red", onclick: async () => {
        try { await del(`/api/secrets/${encodeURIComponent(s.ref_name)}`); }
        catch (e) { toast(e.message); }
        root.innerHTML = ""; renderCapabilities(root);
      } }, "Delete"))));
  return el("div", { class: "card" }, el("h3", {}, "Secrets"),
    el("p", { class: "muted" }, "Values are write-only and never shown. The agent cannot read them."),
    el("table", {}, el("thead", {}, el("tr", {},
      el("th", {}, "Reference"), el("th", {}, "Owner"), el("th", {}, ""))),
      el("tbody", {}, ...rows)),
    el("div", { class: "row wrap" }, ref, value, addBtn));
}
