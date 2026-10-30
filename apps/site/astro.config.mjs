import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "astro/config";

// Sitio estático servido por el backend en "/"; sin scripts ni estilos en línea (CSP estricta).
export default defineConfig({
  outDir: "../api/orkesta/static/site",
  build: { inlineStylesheets: "never", assets: "_astro" },
  vite: { plugins: [tailwindcss()], build: { assetsInlineLimit: 0 } },
});
