// Control-panel SPA shell: theme, navigation, live status, and a tiny hash router.
import { get, connectStatus } from "./api.js";
import { renderRunbar } from "./views/runbar.js";
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

// App-wide live status, aggregated from /ws/status events and shared with the runbar.
const statusState = { model: { status: "idle", model: null }, active: null, queue: [] };

/** Apply the saved theme to the document root. */
async function applyTheme() {
  try {
    const settings = await get("/api/settings");
    document.documentElement.setAttribute("data-theme", settings.theme || "system");
  } catch {
    document.documentElement.setAttribute("data-theme", "system");
  }
}

/** Re-render the top-right run indicator + queue panel from the shared state. */
function paintRunbar() {
  const mount = document.getElementById("runbar");
  if (mount) renderRunbar(mount, statusState);
}

/** Subscribe once to the live-status channel and keep the runbar current (FR-005/FR-007). */
function startStatus() {
  connectStatus((event) => {
    if (event.type === "model_status") statusState.model = event;
    else if (event.type === "run_status") statusState.active = event.active;
    else if (event.type === "queue") statusState.queue = event.items || [];
    else return;
    paintRunbar();
    // Notify the active view (if it wants live updates, e.g. the session detail).
    window.dispatchEvent(new CustomEvent("status", { detail: { ...statusState, event } }));
  });
}

/** Render the top navigation buttons. */
function renderNav(active) {
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  for (const route of ROUTES) {
    const btn = document.createElement("button");
    btn.textContent = route.label;
    btn.className = route.id === active ? "active" : "";
    btn.addEventListener("click", () => { location.hash = route.id; nav.classList.remove("open"); });
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
  view.append(loadingSkeleton());
  // Build the view in a detached container so the page never flashes a half-built
  // state, then swap it in atomically. Views fire "app:rerender" to refresh in place.
  const container = document.createElement("div");
  container.style.display = "contents";
  try {
    await match.render(container);
    view.replaceChildren(container);
  } catch (err) {
    view.replaceChildren(Object.assign(document.createElement("div"), {
      className: "card", textContent: "Error: " + err.message,
    }));
  }
}

/** A placeholder shown while a view's API calls are in flight (no blank page). */
function loadingSkeleton() {
  const wrap = document.createElement("div");
  wrap.className = "skeleton-wrap";
  for (let i = 0; i < 3; i++) {
    const card = document.createElement("div");
    card.className = "card skeleton-card";
    const bar = document.createElement("div");
    bar.className = "skeleton-bar";
    const line1 = document.createElement("div");
    line1.className = "skeleton-line";
    const line2 = document.createElement("div");
    line2.className = "skeleton-line short";
    card.append(bar, line1, line2);
    wrap.append(card);
  }
  return wrap;
}

// Compact-nav toggle for narrow viewports.
window.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("nav-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => document.getElementById("nav").classList.toggle("open"));
  }
});

window.addEventListener("hashchange", route);
// In-place refresh hook: views fire this after a mutation to re-render the current page.
window.addEventListener("app:rerender", route);
applyTheme().then(() => { route(); startStatus(); paintRunbar(); });
