import { defineConfig } from "vite";
import { resolve } from "path";

// Source layout:
//   src/shared/      — UI shared by extension + web (popup.html, popup.ts, ...)
//   src/extension/   — extension-only entries (background service worker, ...)
//   src/web/         — web-only entries (placeholder)
//
// Vite root is src/shared so popup.html (the entry HTML) outputs to dist/
// root, which is required for both Chrome-extension loading and FastAPI's
// /ui/ static mount.

export default defineConfig({
    root: resolve(__dirname, "src/shared"),
    base: "",
    publicDir: resolve(__dirname, "public"),
    build: {
        outDir: resolve(__dirname, "dist"),
        emptyOutDir: true,
        rollupOptions: {
            input: {
                popup: resolve(__dirname, "src/shared/popup.html"),
                background: resolve(__dirname, "src/extension/background.ts"),
            },
            output: {
                entryFileNames: "assets/[name].js",
                chunkFileNames: "assets/[name].js",
                assetFileNames: "assets/[name].[ext]",
            },
        },
    },
});
