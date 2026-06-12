// REST + helper utilities for the control panel SPA.
// Thin wrappers over fetch with JSON handling and clear error propagation.

/** Perform a JSON request and return the parsed body (throws on non-2xx). */
export async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  const text = await resp.text();
  const data = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const detail = data && data.detail ? data.detail : resp.statusText;
    throw new Error(detail);
  }
  return data;
}

export const get = (p) => api("GET", p);
export const post = (p, b) => api("POST", p, b);
export const patch = (p, b) => api("PATCH", p, b);
export const del = (p) => api("DELETE", p);
export const put = (p, b) => api("PUT", p, b);

/** Build a DOM element from a tag, attributes, and children. */
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else if (v !== undefined && v !== null) {
      node.setAttribute(k, v);
    }
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** Show a transient toast message in the corner. */
export function toast(message) {
  let list = document.querySelector(".toast-list");
  if (!list) {
    list = el("div", { class: "toast-list" });
    document.body.append(list);
  }
  const item = el("div", { class: "card" }, message);
  list.append(item);
  setTimeout(() => item.remove(), 4000);
}
