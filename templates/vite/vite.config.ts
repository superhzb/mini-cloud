import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server on the canonical web port assigned by `mini new`. `/api` proxies to the app's API
// so the frontend uses one origin in dev (config, not hardcoded hosts).
export default defineConfig({
  plugins: [react()],
  server: {
    port: {{web_port}},
    proxy: {
      "/api": {
        target: process.env.VITE_API_URL ?? "http://127.0.0.1:{{api_port}}",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
