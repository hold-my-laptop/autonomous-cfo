import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import { fileURLToPath } from "node:url";

// Sample bank statement for mock mode: the repo-root data/ folder (gitignored, same file the api reads).
const SAMPLES = process.env.SAMPLES_DIR ?? fileURLToPath(new URL("../../data", import.meta.url));

// services/api from `make up-app` listens on localhost:8000.
const BACKEND = process.env.BACKEND_URL ?? "http://localhost:8000";
// services/ai run locally: the api expects it on localhost:8001 (its default AI_URL).
const AI = process.env.AI_URL ?? "http://localhost:8001";
const toAi = (path: string) => ({ target: AI, changeOrigin: true, rewrite: () => path });

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@samples": SAMPLES } },
  server: {
    port: 5173,
    host: true,
    fs: { allow: [".", SAMPLES] },
    // Same as apps/web/nginx.conf in production. The first matching key wins, so the AI route
    // comes before the catch-all; /api/v1/... goes to the api unchanged. (Document extraction is
    // internal to the backend now: uploads go to the api's POST /api/v1/documents.)
    proxy: {
      "^/api/v1/chat$": toAi("/v1/chat"),
      "/api/v1": { target: BACKEND, changeOrigin: true },
    },
  },
});
