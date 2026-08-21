import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

// @contract 指向 contract 库生成的类型。真实拆仓后改为 npm 包引用。
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@contract": resolve(__dirname, "../contract/types/contract.ts"),
    },
  },
  server: {
    port: 5178,
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
