import type { DetailPageBatchItem } from "./types";

const DEFAULT_CONCURRENCY = 5;
const MIN_CONCURRENCY = 1;
const MAX_CONCURRENCY = 5;
const DEFAULT_TAB_TIMEOUT_MS = 25_000;
const DEFAULT_SETTLE_DELAY_MS = 1_000;
const DEFAULT_MIN_HTML_LENGTH = 32;

export interface CaptureCandidate {
    url: string;
    selectedAnchorText?: string;
}

export interface CaptureFailure {
    url: string;
    error: string;
}

export interface CaptureProgress {
    current: number;
    total: number;
    url: string;
    status: "started" | "succeeded" | "failed";
    error?: string;
}

export interface CaptureBatchOptions {
    concurrency?: number;
    timeoutMs?: number;
    settleDelayMs?: number;
    minHtmlLength?: number;
    onProgress?: (event: CaptureProgress) => void;
}

export interface CaptureBatchResult {
    successes: DetailPageBatchItem[];
    failures: CaptureFailure[];
}

export function chunkUrls(urls: string[], size: number): string[][] {
    const chunkSize = Math.max(1, Math.floor(size));
    if (urls.length === 0) {
        return [];
    }
    const chunks: string[][] = [];
    for (let index = 0; index < urls.length; index += chunkSize) {
        chunks.push(urls.slice(index, index + chunkSize));
    }
    return chunks;
}

export function clampAutomationConcurrency(value?: number | null): number {
    if (!Number.isFinite(value as number)) {
        return DEFAULT_CONCURRENCY;
    }
    const parsed = Math.floor(Number(value));
    return Math.max(MIN_CONCURRENCY, Math.min(MAX_CONCURRENCY, parsed));
}

export async function runWorkerPool<T, R>(
    items: T[],
    worker: (item: T, index: number) => Promise<R>,
    concurrency: number
): Promise<R[]> {
    if (items.length === 0) {
        return [];
    }

    const maxWorkers = Math.max(1, Math.min(items.length, clampAutomationConcurrency(concurrency)));
    const results: R[] = new Array(items.length);
    let nextIndex = 0;

    async function runWorker() {
        while (true) {
            const current = nextIndex;
            nextIndex += 1;
            if (current >= items.length) {
                return;
            }
            results[current] = await worker(items[current], current);
        }
    }

    const workers = Array.from({ length: maxWorkers }, () => runWorker());
    await Promise.all(workers);
    return results;
}

export async function captureDetailPagesBatch(
    candidates: CaptureCandidate[],
    options: CaptureBatchOptions = {}
): Promise<CaptureBatchResult> {
    const total = candidates.length;
    if (total === 0) {
        return { successes: [], failures: [] };
    }

    const onProgress = options.onProgress;
    const timeoutMs = Math.max(1_000, Math.floor(options.timeoutMs ?? DEFAULT_TAB_TIMEOUT_MS));
    const settleDelayMs = Math.max(0, Math.floor(options.settleDelayMs ?? DEFAULT_SETTLE_DELAY_MS));
    const minHtmlLength = Math.max(0, Math.floor(options.minHtmlLength ?? DEFAULT_MIN_HTML_LENGTH));
    const concurrency = clampAutomationConcurrency(options.concurrency);

    const outputs = await runWorkerPool(
        candidates,
        async (candidate, index) => {
            onProgress?.({
                current: index + 1,
                total,
                url: candidate.url,
                status: "started",
            });
            const result = await captureSingleDetailPage(candidate, {
                timeoutMs,
                settleDelayMs,
                minHtmlLength,
            });
            onProgress?.({
                current: index + 1,
                total,
                url: candidate.url,
                status: result.ok ? "succeeded" : "failed",
                error: result.ok ? undefined : result.error,
            });
            return result;
        },
        concurrency
    );

    const successes: DetailPageBatchItem[] = [];
    const failures: CaptureFailure[] = [];
    for (const output of outputs) {
        if (output.ok) {
            successes.push(output.page);
        } else {
            failures.push({
                url: output.url,
                error: output.error,
            });
        }
    }

    return {
        successes,
        failures,
    };
}

interface SingleCaptureOptions {
    timeoutMs: number;
    settleDelayMs: number;
    minHtmlLength: number;
}

type CaptureOutput =
    | {
        ok: true;
        url: string;
        page: DetailPageBatchItem;
    }
    | {
        ok: false;
        url: string;
        error: string;
    };

async function captureSingleDetailPage(
    candidate: CaptureCandidate,
    options: SingleCaptureOptions
): Promise<CaptureOutput> {
    let tabId: number | null = null;
    const url = String(candidate.url || "").trim();
    if (!url) {
        return {
            ok: false,
            url: "",
            error: "Missing detail URL",
        };
    }

    try {
        const tab = await createBackgroundTab(url);
        if (!tab.id) {
            return {
                ok: false,
                url,
                error: "Failed to create browser tab",
            };
        }
        tabId = tab.id;

        await waitTabLoaded(tabId, options.timeoutMs);
        if (options.settleDelayMs > 0) {
            await sleep(options.settleDelayMs);
        }
        const htmlContent = await readDocumentOuterHtml(tabId);
        if (htmlContent.length < options.minHtmlLength) {
            return {
                ok: false,
                url,
                error: `HTML too short (${htmlContent.length})`,
            };
        }

        const page: DetailPageBatchItem = {
            url,
            html_content: htmlContent,
        };
        const anchorText = String(candidate.selectedAnchorText || "").trim();
        if (anchorText) {
            page.selected_anchor_text = anchorText;
        }

        return {
            ok: true,
            url,
            page,
        };
    } catch (error) {
        return {
            ok: false,
            url,
            error: error instanceof Error ? error.message : String(error),
        };
    } finally {
        if (tabId !== null) {
            await closeTab(tabId);
        }
    }
}

function createBackgroundTab(url: string): Promise<chrome.tabs.Tab> {
    return new Promise((resolve, reject) => {
        chrome.tabs.create(
            {
                url,
                active: false,
            },
            (tab) => {
                if (chrome.runtime.lastError) {
                    reject(new Error(chrome.runtime.lastError.message));
                    return;
                }
                resolve(tab);
            }
        );
    });
}

function readDocumentOuterHtml(tabId: number): Promise<string> {
    return new Promise((resolve, reject) => {
        chrome.scripting.executeScript(
            {
                target: { tabId },
                func: () => document.documentElement?.outerHTML ?? "",
            },
            (results) => {
                if (chrome.runtime.lastError) {
                    reject(new Error(chrome.runtime.lastError.message));
                    return;
                }
                if (!results || results.length === 0) {
                    reject(new Error("No script execution result"));
                    return;
                }
                resolve(String(results[0].result || ""));
            }
        );
    });
}

function waitTabLoaded(tabId: number, timeoutMs: number): Promise<void> {
    return new Promise((resolve, reject) => {
        let completed = false;
        const timer = window.setTimeout(() => {
            cleanup();
            reject(new Error(`Timeout waiting for tab load (${timeoutMs}ms)`));
        }, timeoutMs);

        function cleanup() {
            if (completed) {
                return;
            }
            completed = true;
            window.clearTimeout(timer);
            chrome.tabs.onUpdated.removeListener(onUpdated);
            chrome.tabs.onRemoved.removeListener(onRemoved);
        }

        function onUpdated(updatedTabId: number, changeInfo: chrome.tabs.TabChangeInfo) {
            if (updatedTabId !== tabId) {
                return;
            }
            if (changeInfo.status === "complete") {
                cleanup();
                resolve();
            }
        }

        function onRemoved(removedTabId: number) {
            if (removedTabId !== tabId) {
                return;
            }
            cleanup();
            reject(new Error("Tab was closed before page finished loading"));
        }

        chrome.tabs.onUpdated.addListener(onUpdated);
        chrome.tabs.onRemoved.addListener(onRemoved);
        chrome.tabs.get(tabId, (tab) => {
            if (chrome.runtime.lastError) {
                cleanup();
                reject(new Error(chrome.runtime.lastError.message));
                return;
            }
            if (tab?.status === "complete") {
                cleanup();
                resolve();
            }
        });
    });
}

function closeTab(tabId: number): Promise<void> {
    return new Promise((resolve) => {
        chrome.tabs.remove(tabId, () => {
            resolve();
        });
    });
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}
