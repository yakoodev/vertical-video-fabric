import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies every backend path to uvicorn so the browser sees a single
// origin (cookie auth works, no CORS). Override the backend with VVF_BACKEND.
const backend = process.env.VVF_BACKEND ?? "http://127.0.0.1:8097";
const proxy = { target: backend, changeOrigin: true };

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": proxy,
      "/media": proxy,
      "/ui": proxy,
      "/login": proxy,
      "/logout": proxy,
      "/docs": proxy,
      "/openapi.json": proxy,
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
