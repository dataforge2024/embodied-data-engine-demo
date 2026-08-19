import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@contract": resolve(__dirname, "../../contract/types/contract.ts"),
    },
  },
  server: {
    port: 5173,
    // ws: true —— /api/v1/ws/console 走同一前缀，代理必须放行 Upgrade
    proxy: { "/api": { target: "http://127.0.0.1:8000", ws: true } },
  },
});
