import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves on your network (host: true) so you can open it on your phone
// with hot reload; API calls are proxied to the FastAPI server (`oyt serve`) on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
  build: { outDir: "dist", sourcemap: true },
});
