import { createContext, useContext } from "react";

/**
 * App-wide live status (model load state, active run, queue) shared from the
 * single `/ws/status` socket opened in App. Lets nested views — notably the
 * chat in SessionDetail — surface "Loading model…" and queue position without
 * opening a second socket.
 */
export const StatusContext = createContext({
  model: { status: "idle", model: null },
  active: null,
  queue: [],
  connected: false,
});

/** Hook to read the shared app status from any component under <App>. */
export function useAppStatus() {
  return useContext(StatusContext);
}
