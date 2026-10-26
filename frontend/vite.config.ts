import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // 개발 중에는 /api 요청을 FastAPI로 넘겨 CORS 설정 없이 동작하게 한다.
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
});
