import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const target = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    // "frontend" is the compose service name the end-to-end tests use to reach this server.
    allowedHosts: ["frontend"],
    proxy: { "/api": target, "/health": target },
  },
});
