import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Python server runs on 8765 (`radar serve`) and Vite proxies to it.
// In production FastAPI serves the built files from web/dist itself.
// Relative asset paths, so the same build works at "/" (served by the helper) and under
// "/wifi-motion-radar/" (the hosted website); routing is by hash, never by path.
export default defineConfig({
  base: "./",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/ws": { target: "ws://127.0.0.1:8765", ws: true },
    },
  },
  build: { outDir: "dist", emptyOutDir: true, target: "es2021" },
});
