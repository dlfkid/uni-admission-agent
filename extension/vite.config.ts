import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
    root: "src",
    base: "",
    publicDir: resolve(__dirname, "public"),
    build: {
        outDir: resolve(__dirname, "dist"),
        emptyOutDir: true,
        rollupOptions: {
            input: {
                popup: resolve(__dirname, "src/popup.html"),
                background: resolve(__dirname, "src/background.ts"),
            },
            output: {
                entryFileNames: "assets/[name].js",
                chunkFileNames: "assets/[name].js",
                assetFileNames: "assets/[name].[ext]",
            },
        },
    },
});
