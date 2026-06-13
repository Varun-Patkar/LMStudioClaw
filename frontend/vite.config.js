import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The React control panel builds into the Python package's static dir so the existing
// FastAPI StaticFiles mount serves it unchanged. In dev, API + WebSocket calls are
// proxied to the running controller on :8765.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../lmstudioclaw/web/static",
    emptyOutDir: true,
  },
  server: {
    port: 5273,
    proxy: {
      "/api": "http://localhost:8765",
      "/ws": { target: "ws://localhost:8765", ws: true },
    },
  },
});
