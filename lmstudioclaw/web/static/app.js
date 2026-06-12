// Control-panel SPA shell: theme, navigation, and a tiny hash router.
import { get } from "./api.js";
import { renderSessions } from "./views/sessions.js";
import { renderAutomations } from "./views/automations.js";
import { renderCapabilities } from "./views/capabilities.js";
import { renderSettings } from "./views/settings.js";

const ROUTES = [
  { id: "sessions", label: "Sessions", render: renderSessions },
  { id: "automations", label: "Automations", render: renderAutomations },
  { id: "capabilities", label: "Skills & Tools", render: renderCapabilities },
  { id: "settings", label: "Settings", render: renderSettings },
];

/** Apply the saved theme to the document root. */
async function applyTheme() {
  try {
    const settings = await get("/api/settings");
    document.documentElement.setAttribute("data-theme", settings.theme || "system");
  } catch {
    document.documentElement.setAttribute("data-theme", "system");
  }
}

/** Render the top navigation buttons. */
function renderNav(active) {
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  for (const route of ROUTES) {
    const btn = document.createElement("button");
    btn.textContent = route.label;
    btn.className = route.id === active ? "active" : "";
    btn.addEventListener("click", () => { location.hash = route.id; });
    nav.append(btn);
  }
}

/** Resolve the current route from the URL hash and render its view. */
async function route() {
  const id = (location.hash.replace("#", "") || "sessions").split("/")[0];
  const match = ROUTES.find((r) => r.id === id) || ROUTES[0];
  renderNav(match.id);
  const view = document.getElementById("view");
  view.innerHTML = "";
  try {
    await match.render(view);
  } catch (err) {
    view.append(Object.assign(document.createElement("div"), {
      className: "card", textContent: "Error: " + err.message,
    }));
  }
}

window.addEventListener("hashchange", route);
applyTheme().then(route);
