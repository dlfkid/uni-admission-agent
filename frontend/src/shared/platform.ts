/**
 * Platform abstraction — detects whether this bundle is running as a
 * Chrome extension (popup / side panel) or as a standalone web page
 * served via FastAPI's /ui/ static mount.
 *
 * Use ``isExtensionContext`` to guard any code path that depends on
 * extension-only APIs (`chrome.tabs.*`, `chrome.scripting.*`,
 * `chrome.runtime.*`). When false, the same source file runs as a
 * regular web page — those APIs aren't available, so the calling
 * code must provide a fallback (typically: ask the user to paste a
 * URL instead of auto-detecting).
 */

export const isExtensionContext: boolean =
    typeof (globalThis as { chrome?: unknown }).chrome !== "undefined" &&
    typeof (
        (globalThis as { chrome?: { tabs?: unknown } }).chrome?.tabs
    ) !== "undefined" &&
    typeof (
        (globalThis as { chrome?: { runtime?: unknown } }).chrome?.runtime
    ) !== "undefined";


/** Add the `extension-only` CSS class globally so styles can hide nodes
 *  that don't make sense in web mode. Inverse: add `web-only` class. */
export function applyPlatformBodyClass(): void {
    if (typeof document === "undefined") return;
    if (isExtensionContext) {
        document.body.classList.add("platform-extension");
    } else {
        document.body.classList.add("platform-web");
    }
}
