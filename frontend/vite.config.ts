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
                // Content-hashed filenames. Without them the bundle emits
                // stable names (assets/popup.js), and a browser holding a
                // cached copy of one will happily pair it with a freshly
                // fetched popup.html — the two are cached independently and
                // neither carries a Cache-Control header, so heuristic
                // freshness alone decides. That pairing is not a cosmetic
                // problem: JS from before a markup change calls
                // addEventListener on an element the new HTML no longer has,
                // throws on the null, and takes the whole module down with
                // it — no autocomplete, no search, no tab switching, every
                // pane visible at once.
                //
                // A hash makes the mismatch unrepresentable: popup.html only
                // ever names the assets it was built with.
                //
                // background.js is the one exception and must keep its name:
                // public/manifest.json hardcodes "assets/background.js" as
                // the service worker, and Chrome reads that literally.
                entryFileNames: (chunk) =>
                    chunk.name === "background"
                        ? "assets/[name].js"
                        : "assets/[name]-[hash].js",
                chunkFileNames: "assets/[name]-[hash].js",
                assetFileNames: "assets/[name]-[hash].[ext]",
            },
        },
    },
});
