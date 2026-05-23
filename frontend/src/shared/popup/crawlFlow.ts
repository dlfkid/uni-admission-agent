import {
    autoPaginateCheckbox,
    autoPaginateField,
    automationConcurrencyInput,
    browserAutomationCheckbox,
    cancelLinksBtn,
    confirmLinksBtn,
    continueBtn,
    exportMdCheckbox,
    exportPathInput,
    linkCountBadge,
    linkListEl,
    pageTypeSelect,
    progressFill,
    progressText,
    selectAllLinksCheckbox,
    sendBtn,
    slugInput,
    stopBtn,
    tokenDisplay,
    urlDisplay,
    yearInput,
} from "./dom";
import { isExtensionContext } from "../platform";
import { DETAIL_BATCH_SIZE } from "./preferences";
import { initLinkSelectionFlow } from "./linkSelectionFlow";
import { captureDetailPagesBatch, chunkUrls, clampAutomationConcurrency } from "./automationQueue";
import {
    renderBatchSummary,
    submitCrawl,
    submitAgentRun,
    waitForTaskTerminal,
    type CrawlApiCallbacks,
} from "./crawlApi";
import type { AnalyzeResult, BrowserProvider, CrawlPayload, ShowStatusFn } from "./types";
import type { initMonitorFlow } from "./monitorFlow";

type MonitorFlow = ReturnType<typeof initMonitorFlow>;

// ---------------------------------------------------------------------------
//  Types
// ---------------------------------------------------------------------------

interface IndexBatchExecutionOptions {
    url: string;
    slug: string;
    year: number;
    exportMd: boolean;
    exportPath: string;
    selectedUrls: string[];
    selectedLinkTexts: Record<string, string>;
    browserAutomationEnabled: boolean;
    automationConcurrency: number;
}

export interface CrawlFlowDeps {
    apiBase: string;
    showStatus: ShowStatusFn;
    switchView: (view: "input" | "link-selection" | "monitor") => void;
    setFormEnabled: (enabled: boolean) => void;
    appendPreflightLog: (msg: string) => void;
    clearPreflightLogs: () => void;
    getTaxonomyOptions: () => {
        enabled: boolean;
        lowThreshold: number;
        highThreshold: number;
        hintTopK: number;
        overrideEnabled: boolean;
    };
    getBrowserSource?: () => { provider: BrowserProvider; clientId?: string };
    getMonitorFlow: () => MonitorFlow | null;
    serverAgentEnabled: () => boolean;
    reinit: () => Promise<void>;
}

// ---------------------------------------------------------------------------
//  getCurrentPageHTML — reads rendered HTML from the active browser tab
// ---------------------------------------------------------------------------

export async function getCurrentPageHTML(): Promise<string | null> {
    // Web mode has no concept of a "current tab" — return null so callers
    // fall back to server-side fetching of the URL the user typed.
    if (!isExtensionContext) {
        return null;
    }
    return new Promise((resolve) => {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
            const tab = tabs[0];
            if (!tab || !tab.id) {
                resolve(null);
                return;
            }

            // Execute script in the page to get full HTML
            chrome.scripting.executeScript(
                {
                    target: { tabId: tab.id },
                    func: () => {
                        return document.documentElement.outerHTML;
                    },
                },
                (results) => {
                    if (chrome.runtime.lastError || !results || results.length === 0) {
                        console.error("Failed to get page HTML:", chrome.runtime.lastError);
                        resolve(null);
                        return;
                    }
                    resolve(results[0].result as string);
                },
            );
        });
    });
}

// ---------------------------------------------------------------------------
//  initCrawlFlow — wires runIndexBatches, linkSelectionFlow, and sendBtn
// ---------------------------------------------------------------------------

export function initCrawlFlow(deps: CrawlFlowDeps): void {
    const {
        apiBase,
        showStatus,
        switchView,
        setFormEnabled,
        appendPreflightLog,
        clearPreflightLogs,
        getTaxonomyOptions,
        getBrowserSource,
        getMonitorFlow,
        serverAgentEnabled,
        reinit,
    } = deps;

    const apiCallbacks: CrawlApiCallbacks = {
        showStatus,
        setFormEnabled,
        getTaxonomyOptions,
        getBrowserSource,
        reinit,
    };

    const updateAutoPaginateVisibility = () => {
        const pageType = pageTypeSelect.value;
        autoPaginateField.style.display = pageType === "detail" ? "none" : "block";
    };
    pageTypeSelect.addEventListener("change", updateAutoPaginateVisibility);
    updateAutoPaginateVisibility();

    function getAutomationConcurrency(): number {
        const clamped = clampAutomationConcurrency(
            parseInt(automationConcurrencyInput.value.trim(), 10),
        );
        automationConcurrencyInput.value = String(clamped);
        return clamped;
    }

    async function runIndexBatches(opts: IndexBatchExecutionOptions): Promise<void> {
        const batches = chunkUrls(opts.selectedUrls, DETAIL_BATCH_SIZE);
        if (batches.length === 0) {
            throw new Error("No links selected");
        }

        switchView("monitor");
        setFormEnabled(false);
        progressText.textContent = "Preparing index batches…";
        progressFill.style.width = "2%";
        progressFill.style.backgroundColor = "var(--accent)";
        tokenDisplay.classList.add("hidden");
        stopBtn.classList.add("hidden");
        continueBtn.classList.add("hidden");

        const totalUrls = opts.selectedUrls.length;
        const batchTotal = batches.length;
        const monitorFlow = getMonitorFlow();
        monitorFlow?.clearBatchLogs();
        monitorFlow?.appendBatchLog(`Queue started: ${totalUrls} URLs in ${batchTotal} batches.`);
        let processed = 0;
        let success = 0;
        let failed = 0;
        let startedAnyTask = false;

        const updateSummary = (batchIndex: number, currentUrl = ""): void => {
            monitorFlow?.setBatchSummary(
                renderBatchSummary({
                    processed,
                    total: totalUrls,
                    batchIndex,
                    batchTotal,
                    success,
                    failed,
                    currentUrl,
                }),
            );
        };

        updateSummary(1);

        for (let offset = 0; offset < batches.length; offset += 1) {
            const batchIndex = offset + 1;
            const batchUrls = batches[offset];
            let currentUrl = "";
            let batchSubmitCount = batchUrls.length;
            let detailPagesBatch: CrawlPayload["detail_pages_batch"] = undefined;

            const batchLinkTexts: Record<string, string> = {};
            for (const detailUrl of batchUrls) {
                const anchorText = opts.selectedLinkTexts[detailUrl];
                if (anchorText) {
                    batchLinkTexts[detailUrl] = anchorText;
                }
            }

            updateSummary(batchIndex);

            if (opts.browserAutomationEnabled) {
                const captureCandidates = batchUrls.map((detailUrl) => ({
                    url: detailUrl,
                    selectedAnchorText: opts.selectedLinkTexts[detailUrl],
                }));

                const captureResult = await captureDetailPagesBatch(captureCandidates, {
                    concurrency: opts.automationConcurrency,
                    onProgress: (event) => {
                        currentUrl = event.url;
                        updateSummary(batchIndex, currentUrl);
                    },
                });

                detailPagesBatch = captureResult.successes;
                batchSubmitCount = captureResult.successes.length;
                failed += captureResult.failures.length;
                for (const failure of captureResult.failures) {
                    appendPreflightLog(
                        `Automation capture failed: ${failure.url} (${failure.error})`,
                    );
                    monitorFlow?.appendBatchLog(
                        `Capture failed: ${failure.url} (${failure.error})`,
                    );
                }

                if (batchSubmitCount === 0) {
                    processed += batchUrls.length;
                    showStatus(
                        `Batch ${batchIndex}/${batchTotal}: no pages captured, skipped.`,
                        "info",
                    );
                    monitorFlow?.appendBatchLog(
                        `Batch ${batchIndex}/${batchTotal}: no pages captured, skipped.`,
                    );
                    updateSummary(Math.min(batchTotal, batchIndex + 1));
                    continue;
                }
            }

            try {
                monitorFlow?.appendBatchLog(
                    `Submitting batch ${batchIndex}/${batchTotal} (${batchSubmitCount} pages).`,
                );
                const taskId = await submitCrawl(
                    {
                        url: opts.url,
                        slug: opts.slug,
                        year: opts.year,
                        pageType: "index",
                        exportMd: opts.exportMd,
                        exportPath: opts.exportPath,
                        selectedUrls: opts.browserAutomationEnabled ? undefined : batchUrls,
                        selectedLinkTexts: opts.browserAutomationEnabled
                            ? undefined
                            : batchLinkTexts,
                        browserAutomationEnabled: opts.browserAutomationEnabled,
                        detailPagesBatch: opts.browserAutomationEnabled
                            ? detailPagesBatch
                            : undefined,
                        batchIndex,
                        batchTotal,
                    },
                    apiBase,
                    apiCallbacks,
                );
                startedAnyTask = true;
                monitorFlow?.startMonitoring(taskId);
                const finalTask = await waitForTaskTerminal(taskId, apiBase);
                if (finalTask.state === "DONE") {
                    success += batchSubmitCount;
                    monitorFlow?.appendBatchLog(
                        `Batch ${batchIndex}/${batchTotal} finished successfully.`,
                    );
                } else {
                    failed += batchSubmitCount;
                    appendPreflightLog(
                        `Batch ${batchIndex}/${batchTotal} failed: ${finalTask.error || "unknown error"}`,
                    );
                    monitorFlow?.appendBatchLog(
                        `Batch ${batchIndex}/${batchTotal} failed: ${finalTask.error || "unknown error"}`,
                    );
                }
            } catch (err) {
                failed += batchSubmitCount;
                appendPreflightLog(
                    `Batch ${batchIndex}/${batchTotal} submission failed: ${String(err)}`,
                );
                monitorFlow?.appendBatchLog(
                    `Batch ${batchIndex}/${batchTotal} submit failed: ${String(err)}`,
                );
            } finally {
                processed += batchUrls.length;
                updateSummary(Math.min(batchTotal, batchIndex + 1));
            }
        }

        monitorFlow?.setBatchSummary(
            renderBatchSummary({
                processed,
                total: totalUrls,
                batchIndex: batchTotal,
                batchTotal,
                success,
                failed,
            }),
        );

        if (!startedAnyTask) {
            progressText.textContent = "No batches submitted";
            progressFill.style.width = "100%";
            stopBtn.classList.add("hidden");
            continueBtn.classList.remove("hidden");
            setFormEnabled(true);
        }

        if (failed > 0) {
            monitorFlow?.appendBatchLog(
                `Queue completed with failures: success=${success}, failed=${failed}.`,
            );
            showStatus(`Batch crawl finished: success=${success}, failed=${failed}`, "info");
        } else {
            monitorFlow?.appendBatchLog(`Queue completed: ${success}/${totalUrls}.`);
            showStatus(`Batch crawl completed: ${success}/${totalUrls}`, "success");
        }
    }

    // Initialize link selection flow (must be done before sendBtn listener so
    // the returned showLinkSelection is accessible in the closure below)
    const linkSelectionFlow = initLinkSelectionFlow({
        showStatus,
        switchView,
        setFormEnabled,
        getSlug: () => slugInput.value.trim(),
        getYear: () => parseInt(yearInput.value.trim(), 10),
        getExportMd: () => exportMdCheckbox.checked,
        getExportPath: () => exportPathInput.value.trim(),
        getBrowserAutomationEnabled: () => browserAutomationCheckbox.checked,
        getAutomationConcurrency,
        runIndexBatches,
        linkListEl,
        selectAllLinksCheckbox,
        linkCountBadge,
        browserAutomationCheckbox,
        automationConcurrencyInput,
        confirmLinksBtn,
        cancelLinksBtn,
    });

    // Wire up the main Start Crawl button
    sendBtn.addEventListener("click", async () => {
        clearPreflightLogs();
        appendPreflightLog("Started pre-crawl analysis.");

        const slug = slugInput.value.trim();
        const year = parseInt(yearInput.value.trim(), 10);
        const url = urlDisplay.textContent ?? "";
        const pageType = pageTypeSelect.value;
        const exportMd = exportMdCheckbox.checked;
        const exportPath = exportPathInput.value.trim();
        const useAgentMode = serverAgentEnabled();

        if (!slug || !year || !url || url.startsWith("(")) {
            appendPreflightLog("Input validation failed: invalid slug/year/url.");
            showStatus("Invalid input or URL", "error");
            return;
        }

        if (exportMd && !exportPath) {
            appendPreflightLog("Input validation failed: export path missing.");
            showStatus("Export path is required when export is enabled", "error");
            return;
        }

        try {
            getTaxonomyOptions();
        } catch (err) {
            appendPreflightLog(`Input validation failed: ${String(err)}`);
            showStatus(String(err), "error");
            return;
        }

        sendBtn.disabled = true;

        // Agent mode: use /agent/run with autonomous=true
        if (useAgentMode) {
            appendPreflightLog("Submitting to /agent/run (default mode).");
            sendBtn.textContent = "Agent running…";

            try {
                const taskId = await submitAgentRun(
                    { url, slug, year, pageType, autoPaginate: autoPaginateCheckbox.checked },
                    apiBase,
                    apiCallbacks,
                );
                appendPreflightLog(`Agent task submitted: ${taskId}`);
                getMonitorFlow()?.clearBatchSummary();
                getMonitorFlow()?.startMonitoring(taskId);
            } catch (err) {
                appendPreflightLog(`Agent run failed: ${String(err)}`);
                showStatus(String(err), "error");
            } finally {
                sendBtn.disabled = false;
                sendBtn.textContent = "Start Crawl";
            }
            return;
        }

        // Normal mode: analyze page first
        sendBtn.textContent = "Reading page…";
        appendPreflightLog("Reading current tab HTML content…");

        const pageHTML = await getCurrentPageHTML();
        if (!pageHTML) {
            appendPreflightLog("Failed to read page HTML from browser tab.");
            showStatus("Failed to read page content. Please refresh and try again.", "error");
            sendBtn.disabled = false;
            sendBtn.textContent = "Start Crawl";
            return;
        }
        appendPreflightLog(`Captured page HTML (${pageHTML.length.toLocaleString()} chars).`);

        sendBtn.textContent = "Analyzing…";
        appendPreflightLog("Analyzing page type and candidate links…");

        try {
            // Step 1: Analyze the page
            const analyzeRes = await fetch(`${apiBase}/analyze`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    url,
                    html_content: pageHTML,
                    page_type_hint: pageType,
                }),
            });

            if (!analyzeRes.ok) {
                throw new Error(`Analysis failed: ${analyzeRes.status}`);
            }

            const analyzeData: AnalyzeResult = await analyzeRes.json();
            appendPreflightLog(
                `Analyze complete: page_type=${analyzeData.page_type}, candidates=${analyzeData.links.length}, total_found=${analyzeData.total_found}.`,
            );

            if (analyzeData.page_type === "detail") {
                // Detail page: start crawl directly with browser HTML
                appendPreflightLog("Detected detail page; submitting crawl job.");
                const taskId = await submitCrawl(
                    {
                        url,
                        slug,
                        year,
                        pageType,
                        exportMd,
                        exportPath,
                        htmlContent: pageHTML,
                    },
                    apiBase,
                    apiCallbacks,
                );
                getMonitorFlow()?.clearBatchSummary();
                getMonitorFlow()?.startMonitoring(taskId);
            } else {
                // Index page: show link selection UI
                if (analyzeData.links.length === 0) {
                    appendPreflightLog("No candidate detail links found on index page.");
                    showStatus("No program links found on this page.", "error");
                    return;
                }
                appendPreflightLog("Rendering candidate detail links for manual selection.");
                linkSelectionFlow.showLinkSelection(
                    analyzeData.links,
                    analyzeData.total_found,
                    url,
                );
                switchView("link-selection");
            }
        } catch (err) {
            appendPreflightLog(`Analyze failed: ${String(err)}`);
            showStatus(String(err), "error");
        } finally {
            sendBtn.disabled = false;
            sendBtn.textContent = "Start Crawl";
        }
    });
}
