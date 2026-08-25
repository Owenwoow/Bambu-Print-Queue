import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 代理到 daemon。同源，所以后端不需要配 CORS。
      "/api": {
        target: "http://127.0.0.1:8710",
        changeOrigin: true,
        // SSE 必须关掉缓冲，否则事件会攒着不发，界面看起来像是死的
        configure: (proxy) => {
          proxy.on("proxyRes", (res) => {
            res.headers["cache-control"] = "no-cache";
          });
        },
      },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
