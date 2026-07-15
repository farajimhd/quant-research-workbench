import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const apiProxyTarget = process.env.VITE_API_PROXY_TARGET
    || env.VITE_API_PROXY_TARGET
    || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    optimizeDeps: {
      include: ["katex"]
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: {
        "/api": {
          target: apiProxyTarget,
          ws: true
        }
      }
    }
  };
});
