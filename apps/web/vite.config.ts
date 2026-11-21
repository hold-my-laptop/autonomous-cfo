import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

// Sample bank statement for mock mode: the repo-root data/ folder (gitignored, same file the api reads).
const SAMPLES = process.env.SAMPLES_DIR ?? fileURLToPath(new URL("../../data", import.meta.url));

// services/api from `make up-app` listens on localhost:8000.
const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@samples": SAMPLES } },
  server: {
    port: 5173,
    host: true,
    fs: { allow: [".", SAMPLES] },
    // Same as apps/web/nginx.conf in production: /api/v1/... is forwarded unchanged.
    proxy: {
      "/api/v1": { target: BACKEND, changeOrigin: true },
    },
  },
});
