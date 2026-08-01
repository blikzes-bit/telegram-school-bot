import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The frontend talks to the FastAPI backend over same-origin ``/api`` so the
// session cookie is sent automatically. In development Vite proxies that prefix
// to the local API (default http://localhost:8000).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
});
