// REST helpers + a reconnecting WebSocket hook for the app-wide status channel.
import { useEffect, useRef, useState } from "react";

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
  if (!resp.ok) throw new Error((data && data.detail) || resp.statusText);
  return data;
}

export const get = (p) => api("GET", p);
export const post = (p, b) => api("POST", p, b);
export const patch = (p, b) => api("PATCH", p, b);
export const del = (p) => api("DELETE", p);

/** WebSocket URL for a given path, honoring https → wss. */
export function wsUrl(path) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}

/**
 * Subscribe to the app-wide /ws/status channel. Returns live
 * `{ model, active, queue, connected }`, auto-reconnecting with backoff so the UI
 * recovers state after a dropped channel (FR-007).
 */
export function useStatusSocket() {
  const [state, setState] = useState({
    model: { status: "idle", model: null }, active: null, queue: [], connected: false,
  });
  const retry = useRef(1000);
  const closed = useRef(false);

  useEffect(() => {
    closed.current = false;
    let sock;
    const open = () => {
      if (closed.current) return;
      sock = new WebSocket(wsUrl("/ws/status"));
      sock.onopen = () => { retry.current = 1000; setState((s) => ({ ...s, connected: true })); };
      sock.onmessage = (ev) => {
        let evt;
        try { evt = JSON.parse(ev.data); } catch { return; }
        setState((s) => {
          if (evt.type === "model_status") return { ...s, model: evt };
          if (evt.type === "run_status") return { ...s, active: evt.active };
          if (evt.type === "queue") return { ...s, queue: evt.items || [] };
          return s;
        });
      };
      sock.onclose = () => {
        setState((s) => ({ ...s, connected: false }));
        if (!closed.current) setTimeout(open, (retry.current = Math.min(retry.current * 1.6, 8000)));
      };
      sock.onerror = () => { try { sock.close(); } catch { /* noop */ } };
    };
    open();
    return () => { closed.current = true; try { sock && sock.close(); } catch { /* noop */ } };
  }, []);

  return state;
}
