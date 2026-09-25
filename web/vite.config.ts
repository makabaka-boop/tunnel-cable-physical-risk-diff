import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// 验收时 Vitest 通过 VITE_API_BASE 直连真实运行中的 API（见 verify 服务）。
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_PROXY_TARGET ?? "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
