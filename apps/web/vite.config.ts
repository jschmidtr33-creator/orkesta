import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// La app vive en /app y la sirve el backend (mismo origen que la API).
export default defineConfig({
  base: "/app/",
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../api/orkesta/static/web",
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
