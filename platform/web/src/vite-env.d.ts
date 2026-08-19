/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Tool（人工环节工作台）的基址，如 `https://tool.example.com`。
   * 不配置时退回本地开发端口 `http://localhost:5174`。
   */
  readonly VITE_TOOL_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
