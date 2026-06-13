// Top-right run indicator + collapsible queue panel (US3).
// Driven by live /ws/status events (model_status / run_status / queue) that the SPA
// shell aggregates into a single `state` object and passes here on every change.
// Clicking the indicator navigates to the running session; the queue panel (shown
// only when items wait) lists queued runs and lets the user cancel one (FR-025).
import { el, del, toast } from "../api.js";

let panelOpen = false;

/**
 * Render the run indicator + queue panel into `mount` from the current `state`.
 * @param {HTMLElement} mount  the #runbar container
 * @param {object} state  { model:{status,model}, active:{id,trigger_type,status,label}|null, queue:[] }
 */
export function renderRunbar(mount, state) {
  mount.innerHTML = "";
  const active = state.active;
  const queue = state.queue || [];
  const modelStatus = (state.model && state.model.status) || "idle";

  // Indicator class reflects the most salient live state.
  let cls = "run-indicator";
  let label = "Idle";
  if (active) {
    cls += active.status === "loading" || modelStatus === "loading" ? " loading" : " active";
    label = `${active.label || "Run"} · ${active.status || "active"}`;
  } else if (modelStatus === "loading") {
    cls += " loading";
    label = "Loading model…";
  } else if (modelStatus === "ready") {
    cls += " active";
    label = state.model && state.model.model ? `Model ready · ${state.model.model}` : "Model ready";
  } else if (modelStatus === "error") {
    cls += " error";
    label = "Load failed";
  }

  const indicator = el("div", {
    class: cls,
    title: active ? "Open the running session" : (modelStatus === "error" && state.model && state.model.reason) || "App status",
    onclick: () => {
      if (active) location.hash = `sessions/detail/${active.id}`;
    },
  }, el("span", { class: "dot" }), el("span", {}, label));

  if (queue.length) {
    indicator.append(el("span", {
      class: "q-count",
      title: "Toggle the queue",
      onclick: (e) => { e.stopPropagation(); panelOpen = !panelOpen; renderRunbar(mount, state); },
    }, String(queue.length)));
  } else {
    panelOpen = false; // hide the panel when the queue drains (FR-023)
  }

  mount.append(indicator);

  if (panelOpen && queue.length) {
    mount.append(queuePanel(mount, state, queue));
  }
}

/** Build the collapsible queue panel listing waiting runs in FIFO order. */
function queuePanel(mount, state, queue) {
  const rows = queue.map((item) =>
    el("div", { class: "queue-item" },
      el("span", { class: "q-type" }, item.trigger_type === "automation" ? "Auto" : "Session"),
      el("span", { class: "q-label" }, item.label || item.id.slice(0, 8)),
      el("button", {
        class: "btn ghost", title: "Cancel this queued run",
        onclick: async (e) => {
          e.stopPropagation();
          try {
            await del(`/api/queue/${item.id}`);
            toast("Queued run cancelled");
          } catch (err) { toast(err.message); }
        },
      }, "✕")));
  return el("div", { class: "queue-panel" },
    el("h3", {}, `Queued (${queue.length})`),
    ...rows);
}
