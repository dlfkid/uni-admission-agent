/**
 * UniAdmission Agent — Chrome Extension Popup Logic
 *
 * Features:
 *  - Start Crawl (Singleton)
 *  - Monitor Progress (Logs stream)
 *  - Stop Task
 *  - Structured Config Editor (Draggable LLM List)
 */

import {
    cancelLinksBtn,
    closeConfigBtn,
    closeExportBtn,
    closePreviewBtn,
    configBtn,
    configModal,
    confirmLinksBtn,
    continueBtn,
    dbUrlInput,
    doExportBtn,
    exportBtn,
    exportMdCheckbox,
    exportModal,
    exportPathField,
    exportPathInput,
    exportSlugDropdown,
    exportSlugInput,
    exportYearInput,
    inputSection,
    linkCountBadge,
    linkListEl,
    linkSelectionSection,
    llmList,
    logsConsole,
    monitorSection,
    pageTypeSelect,
    preflightLogConsole,
    preflightLogSection,
    previewBtn,
    previewCountBadge,
    previewList,
    previewModal,
    previewSearchBtn,
    previewSlugDropdown,
    previewSlugInput,
    previewSummary,
    previewYearInput,
    progressFill,
    progressText,
    saveConfigBtn,
    selectAllLinksCheckbox,
    sendBtn,
    slugDropdown,
    slugInput,
    statusDiv,
    stopBtn,
    taskIdDisplay,
    taxonomyEnabledCheckbox,
    taxonomyHighThresholdInput,
    taxonomyHintTopKInput,
    taxonomyLowThresholdInput,
    taxonomyOverrideEnabledCheckbox,
    taxonomySettings,
    toggleLogsBtn,
    tokenDisplay,
    urlDisplay,
    yearInput,
} from "./popup/dom";
import { initConfigFlow } from "./popup/configFlow";
import { initExportFlow } from "./popup/exportFlow";
import { initLinkSelectionFlow } from "./popup/linkSelectionFlow";
import { initMonitorFlow } from "./popup/monitorFlow";
import { initPreviewFlow } from "./popup/previewFlow";
import type { AnalyzeResult, CrawlPayload, TaskInfo, UniversityOption } from "./popup/types";

const API_BASE = "http://localhost:8910";

// ---------------------------------------------------------------------------
//  State & Utils
// ---------------------------------------------------------------------------

let currentWindowId: number | null = null;
let lastPageHTML: string | null = null;
const LOGS_EXPANDED_KEY = "logs_expanded";

// Cache keys for user preferences
const PAGE_TYPE_KEY = "crawl_page_type";
const EXPORT_MD_KEY = "crawl_export_md";
const EXPORT_PATH_KEY = "crawl_export_path";
const UNIV_SLUG_KEY = "crawl_univ_slug";
const TAXONOMY_ENABLED_KEY = "crawl_taxonomy_enabled";
const TAXONOMY_LOW_THRESHOLD_KEY = "crawl_taxonomy_low_threshold";
const TAXONOMY_HIGH_THRESHOLD_KEY = "crawl_taxonomy_high_threshold";
const TAXONOMY_HINT_TOP_K_KEY = "crawl_taxonomy_hint_top_k";
const TAXONOMY_OVERRIDE_ENABLED_KEY = "crawl_taxonomy_override_enabled";

// Slug autocomplete state
let cachedUniversities: UniversityOption[] = [];
let activeDropdownIndex = -1;
let monitorFlow: ReturnType<typeof initMonitorFlow> | null = null;

// Helper to disable/enable form
function setFormEnabled(enabled: boolean) {
    slugInput.disabled = !enabled;
    yearInput.disabled = !enabled;
    pageTypeSelect.disabled = !enabled;
    exportMdCheckbox.disabled = !enabled;
    exportPathInput.disabled = !enabled;
    taxonomyEnabledCheckbox.disabled = !enabled;
    taxonomyLowThresholdInput.disabled = !enabled;
    taxonomyHighThresholdInput.disabled = !enabled;
    taxonomyHintTopKInput.disabled = !enabled;
    taxonomyOverrideEnabledCheckbox.disabled = !enabled;
    sendBtn.disabled = !enabled;
    // We don't disable config btn as user might want to check settings?
    // But changing settings while running is risky. Let's leave config enabled for now.
}

function initLogsToggle() {
    const isExpanded = localStorage.getItem(LOGS_EXPANDED_KEY) !== "false"; // Default to true/open if missing? 
    // User requested default collapsed? "Default popup... collapsed"
    // Okay, requirement: "default popup ... until user clicks expand. memory function."
    // So default should be false if not set.
    const savedState = localStorage.getItem(LOGS_EXPANDED_KEY);
    const shouldBeExpanded = savedState === "true"; // Default false

    updateLogsState(shouldBeExpanded);



    continueBtn.addEventListener("click", () => {
        switchView("input");
        clearPreflightLogs();
        // Reset monitor state for next run
        progressText.textContent = "Ready";
        progressFill.style.width = "0%";
        tokenDisplay.classList.add("hidden");
        // logsConsole.textContent = ""; 

        // Ensure inputs are unlocked
        setFormEnabled(true);
        sendBtn.textContent = "Start Crawl";
    });

    toggleLogsBtn.addEventListener("click", () => {
        const currentlyExpanded = !logsConsole.classList.contains("collapsed");
        updateLogsState(!currentlyExpanded);
    });
}

function updateLogsState(expanded: boolean) {
    if (expanded) {
        logsConsole.classList.remove("collapsed");
        toggleLogsBtn.textContent = "Hide";
    } else {
        logsConsole.classList.add("collapsed");
        toggleLogsBtn.textContent = "Show";
    }
    localStorage.setItem(LOGS_EXPANDED_KEY, String(expanded));
}

function showStatus(msg: string, type: "success" | "error" | "info"): void {
    statusDiv.textContent = msg;
    statusDiv.className = `status ${type}`;
    statusDiv.classList.remove("hidden");

    if (type !== "error") {
        setTimeout(() => {
            if (statusDiv.textContent === msg) {
                statusDiv.classList.add("hidden");
            }
        }, 5000);
    }
}

function clearPreflightLogs(): void {
    preflightLogConsole.textContent = "";
    preflightLogSection.classList.add("hidden");
}

function appendPreflightLog(message: string): void {
    const timestamp = new Date().toLocaleTimeString([], { hour12: false });
    const line = `[${timestamp}] ${message}`;
    const existing = preflightLogConsole.textContent || "";
    preflightLogConsole.textContent = existing ? `${existing}\n${line}` : line;
    preflightLogSection.classList.remove("hidden");
    preflightLogConsole.scrollTop = preflightLogConsole.scrollHeight;
}

function updateTaxonomySettingsVisibility(): void {
    taxonomySettings.style.display = taxonomyEnabledCheckbox.checked ? "block" : "none";
}

function getTaxonomyOptions(): {
    enabled: boolean;
    lowThreshold: number;
    highThreshold: number;
    hintTopK: number;
    overrideEnabled: boolean;
} {
    const enabled = taxonomyEnabledCheckbox.checked;
    let lowThreshold = parseFloat(taxonomyLowThresholdInput.value);
    let highThreshold = parseFloat(taxonomyHighThresholdInput.value);
    let hintTopK = parseInt(taxonomyHintTopKInput.value, 10);

    if (Number.isNaN(lowThreshold)) lowThreshold = 0.8;
    if (Number.isNaN(highThreshold)) highThreshold = 0.92;
    if (Number.isNaN(hintTopK)) hintTopK = 3;

    lowThreshold = Math.min(1, Math.max(0, lowThreshold));
    highThreshold = Math.min(1, Math.max(0, highThreshold));
    hintTopK = Math.min(5, Math.max(1, hintTopK));

    if (lowThreshold > highThreshold) {
        throw new Error("Taxonomy low threshold must be less than or equal to high threshold.");
    }

    taxonomyLowThresholdInput.value = lowThreshold.toFixed(2);
    taxonomyHighThresholdInput.value = highThreshold.toFixed(2);
    taxonomyHintTopKInput.value = String(hintTopK);

    return {
        enabled,
        lowThreshold,
        highThreshold,
        hintTopK,
        overrideEnabled: taxonomyOverrideEnabledCheckbox.checked,
    };
}

function switchView(view: "input" | "link-selection" | "monitor") {
    inputSection.classList.add("hidden");
    linkSelectionSection.classList.add("hidden");
    monitorSection.classList.add("hidden");
    statusDiv.classList.add("hidden");

    if (view === "input") {
        inputSection.classList.remove("hidden");
        monitorFlow?.stopPolling();
    } else if (view === "link-selection") {
        linkSelectionSection.classList.remove("hidden");
    } else {
        monitorSection.classList.remove("hidden");
    }
}

async function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function restoreCachedPreferences() {
    // Restore page type selection
    const cachedPageType = localStorage.getItem(PAGE_TYPE_KEY);
    if (cachedPageType && ["auto", "index", "detail"].includes(cachedPageType)) {
        pageTypeSelect.value = cachedPageType;
    }

    // Restore export MD checkbox state
    const cachedExportMd = localStorage.getItem(EXPORT_MD_KEY);
    if (cachedExportMd === "true") {
        exportMdCheckbox.checked = true;
        exportPathField.style.display = "block";
    } else {
        exportMdCheckbox.checked = false;
        exportPathField.style.display = "none";
    }

    // Restore export path
    const cachedExportPath = localStorage.getItem(EXPORT_PATH_KEY);
    if (cachedExportPath) {
        exportPathInput.value = cachedExportPath;
    }

    // Restore university slug
    const cachedUnivSlug = localStorage.getItem(UNIV_SLUG_KEY);
    if (cachedUnivSlug) {
        slugInput.value = cachedUnivSlug;
    }

    const cachedTaxonomyEnabled = localStorage.getItem(TAXONOMY_ENABLED_KEY);
    taxonomyEnabledCheckbox.checked = cachedTaxonomyEnabled !== "false";

    const cachedTaxonomyLow = localStorage.getItem(TAXONOMY_LOW_THRESHOLD_KEY);
    taxonomyLowThresholdInput.value = cachedTaxonomyLow || "0.80";

    const cachedTaxonomyHigh = localStorage.getItem(TAXONOMY_HIGH_THRESHOLD_KEY);
    taxonomyHighThresholdInput.value = cachedTaxonomyHigh || "0.92";

    const cachedTaxonomyTopK = localStorage.getItem(TAXONOMY_HINT_TOP_K_KEY);
    taxonomyHintTopKInput.value = cachedTaxonomyTopK || "3";

    const cachedTaxonomyOverride = localStorage.getItem(TAXONOMY_OVERRIDE_ENABLED_KEY);
    taxonomyOverrideEnabledCheckbox.checked = cachedTaxonomyOverride !== "false";

    updateTaxonomySettingsVisibility();
}

// ---------------------------------------------------------------------------
//  Initialization
// ---------------------------------------------------------------------------

// Handle Export MD checkbox toggle
exportMdCheckbox.addEventListener("change", () => {
    const isChecked = exportMdCheckbox.checked;
    if (isChecked) {
        exportPathField.style.display = "block";
    } else {
        exportPathField.style.display = "none";
    }
    // Save to cache
    localStorage.setItem(EXPORT_MD_KEY, String(isChecked));
});

// Handle page type selection change
pageTypeSelect.addEventListener("change", () => {
    // Save to cache
    localStorage.setItem(PAGE_TYPE_KEY, pageTypeSelect.value);
});

// Handle export path input change
exportPathInput.addEventListener("blur", () => {
    // Save to cache when user leaves the input
    localStorage.setItem(EXPORT_PATH_KEY, exportPathInput.value.trim());
});

taxonomyEnabledCheckbox.addEventListener("change", () => {
    localStorage.setItem(TAXONOMY_ENABLED_KEY, String(taxonomyEnabledCheckbox.checked));
    updateTaxonomySettingsVisibility();
});

taxonomyLowThresholdInput.addEventListener("blur", () => {
    localStorage.setItem(TAXONOMY_LOW_THRESHOLD_KEY, taxonomyLowThresholdInput.value.trim());
});

taxonomyHighThresholdInput.addEventListener("blur", () => {
    localStorage.setItem(TAXONOMY_HIGH_THRESHOLD_KEY, taxonomyHighThresholdInput.value.trim());
});

taxonomyHintTopKInput.addEventListener("blur", () => {
    localStorage.setItem(TAXONOMY_HINT_TOP_K_KEY, taxonomyHintTopKInput.value.trim());
});

taxonomyOverrideEnabledCheckbox.addEventListener("change", () => {
    localStorage.setItem(
        TAXONOMY_OVERRIDE_ENABLED_KEY,
        String(taxonomyOverrideEnabledCheckbox.checked),
    );
});

/**
 * Update the displayed URL from the current active tab.
 * Called on init and whenever tab changes.
 */
function updateCurrentUrl() {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const tab = tabs[0];
        if (tab?.url) {
            urlDisplay.textContent = tab.url;
        } else {
            urlDisplay.textContent = "(unable to read URL)";
        }
        // Store window ID for filtering events
        if (tab?.windowId) {
            currentWindowId = tab.windowId;
        }
    });
}

/**
 * Setup listeners to auto-update URL when user switches tabs or navigates.
 */
function setupTabListeners() {
    // When user switches to a different tab
    chrome.tabs.onActivated.addListener((activeInfo) => {
        // Only update if it's in our window
        if (currentWindowId === null || activeInfo.windowId === currentWindowId) {
            updateCurrentUrl();
        }
    });

    // When a tab's URL changes (navigation)
    chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
        // Only care about URL changes in the active tab of our window
        if (changeInfo.url && tab.active &&
            (currentWindowId === null || tab.windowId === currentWindowId)) {
            urlDisplay.textContent = changeInfo.url;
        }
    });
}

async function init() {
    // Restore cached preferences
    restoreCachedPreferences();
    clearPreflightLogs();

    // Get current URL and setup auto-tracking for side panel
    updateCurrentUrl();
    setupTabListeners();

    // Initialize logs toggle state
    initLogsToggle();

    // Load university slugs for autocomplete
    await loadUniversities();
    initSlugAutocomplete();

    try {
        const res = await fetch(`${API_BASE}/tasks/active`);
        if (res.ok) {
            const data: TaskInfo = await res.json();
            if (data && data.task_id) {
                // Task Running! Sync state.
                monitorFlow?.startMonitoring(data.task_id);

                // POPULATE & LOCK PARAMS (Requirement 3)
                if (data.params) {
                    slugInput.value = data.params.univ_slug || "";
                    yearInput.value = String(data.params.year || "");
                    // URL? tab.url might differ if user navigated away.
                    // But we display params in the inputs.
                    if (data.params.url) {
                        urlDisplay.textContent = data.params.url;
                        // Visual cue that it's the *task's* URL, not necessarily current tab
                    }
                }

                // Disable inputs if running
                if (data.state === "RUNNING" || data.state === "PENDING") {
                    setFormEnabled(false);
                    sendBtn.textContent = "Running...";
                }
            }
        }
    } catch (err) {
        console.warn("Failed to check active tasks:", err);
    }
}

// ---------------------------------------------------------------------------
//  Slug Autocomplete
// ---------------------------------------------------------------------------

async function loadUniversities(): Promise<void> {
    try {
        const res = await fetch(`${API_BASE}/universities`);
        if (res.ok) {
            cachedUniversities = await res.json();
        }
    } catch (err) {
        console.warn("Failed to load universities for autocomplete:", err);
    }
}

function initSlugAutocomplete(): void {
    slugInput.addEventListener("input", () => {
        renderDropdown(slugInput.value.trim());
    });

    slugInput.addEventListener("focus", () => {
        renderDropdown(slugInput.value.trim());
    });

    // Save slug to cache when user finishes typing (on blur)
    slugInput.addEventListener("blur", () => {
        const slug = slugInput.value.trim();
        if (slug) {
            localStorage.setItem(UNIV_SLUG_KEY, slug);
        }
    });

    slugInput.addEventListener("keydown", (e: KeyboardEvent) => {
        const items = slugDropdown.querySelectorAll("li");
        if (!items.length || slugDropdown.classList.contains("hidden")) {
            return;
        }

        if (e.key === "ArrowDown") {
            e.preventDefault();
            activeDropdownIndex = Math.min(activeDropdownIndex + 1, items.length - 1);
            highlightItem(items);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            activeDropdownIndex = Math.max(activeDropdownIndex - 1, 0);
            highlightItem(items);
        } else if (e.key === "Enter") {
            if (activeDropdownIndex >= 0 && activeDropdownIndex < items.length) {
                e.preventDefault();
                const slug = (items[activeDropdownIndex] as HTMLElement).dataset.slug;
                if (slug) {
                    slugInput.value = slug;
                    // Save to cache when user selects via Enter key
                    localStorage.setItem(UNIV_SLUG_KEY, slug);
                }
                hideDropdown();
            }
        } else if (e.key === "Escape") {
            hideDropdown();
        }
    });

    // Close dropdown when clicking outside
    document.addEventListener("click", (e: MouseEvent) => {
        const target = e.target as HTMLElement;
        if (!target.closest(".autocomplete-wrapper")) {
            hideDropdown();
        }
    });
}

function renderDropdown(query: string): void {
    slugDropdown.innerHTML = "";
    activeDropdownIndex = -1;

    // Always show the full list when empty, filtered when typing
    const filtered = query
        ? cachedUniversities.filter(
            (u) =>
                u.slug.toLowerCase().includes(query.toLowerCase()) ||
                u.name.toLowerCase().includes(query.toLowerCase())
        )
        : cachedUniversities;

    if (filtered.length === 0) {
        hideDropdown();
        return;
    }

    filtered.forEach((u, idx) => {
        const li = document.createElement("li");
        li.dataset.slug = u.slug;

        const nameSpan = document.createElement("span");
        nameSpan.className = "slug-name";
        nameSpan.textContent = u.slug;

        const metaSpan = document.createElement("span");
        metaSpan.className = "slug-meta";
        metaSpan.textContent = u.name !== u.slug ? u.name : "";

        li.appendChild(nameSpan);
        li.appendChild(metaSpan);

        li.addEventListener("mouseenter", () => {
            activeDropdownIndex = idx;
            highlightItem(slugDropdown.querySelectorAll("li"));
        });

        li.addEventListener("click", () => {
            slugInput.value = u.slug;
            // Save to cache when user selects from dropdown
            localStorage.setItem(UNIV_SLUG_KEY, u.slug);
            hideDropdown();
            slugInput.focus();
        });

        slugDropdown.appendChild(li);
    });

    slugDropdown.classList.remove("hidden");

    // If query matches exactly one item, pre-select it
    if (filtered.length === 1 && filtered[0].slug.toLowerCase() === query.toLowerCase()) {
        activeDropdownIndex = 0;
        highlightItem(slugDropdown.querySelectorAll("li"));
    }
}

function highlightItem(items: NodeListOf<Element>): void {
    items.forEach((item, idx) => {
        item.classList.toggle("active", idx === activeDropdownIndex);
    });
    // Scroll active item into view
    if (activeDropdownIndex >= 0 && items[activeDropdownIndex]) {
        (items[activeDropdownIndex] as HTMLElement).scrollIntoView({ block: "nearest" });
    }
}

function hideDropdown(): void {
    slugDropdown.classList.add("hidden");
    activeDropdownIndex = -1;
}

// ---------------------------------------------------------------------------
//  Input & Monitor Flow
// ---------------------------------------------------------------------------

/**
 * Get the full HTML content of the current active tab.
 * This ensures we extract from the rendered page the user sees.
 */
async function getCurrentPageHTML(): Promise<string | null> {
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
                }
            );
        });
    });
}

const linkSelectionFlow = initLinkSelectionFlow({
    showStatus,
    switchView,
    setFormEnabled,
    getCurrentUrl: () => urlDisplay.textContent ?? "",
    getSlug: () => slugInput.value.trim(),
    getYear: () => parseInt(yearInput.value.trim(), 10),
    getExportMd: () => exportMdCheckbox.checked,
    getExportPath: () => exportPathInput.value.trim(),
    submitCrawl,
    linkListEl,
    selectAllLinksCheckbox,
    linkCountBadge,
    confirmLinksBtn,
    cancelLinksBtn,
});

sendBtn.addEventListener("click", async () => {
    clearPreflightLogs();
    appendPreflightLog("Started pre-crawl analysis.");

    const slug = slugInput.value.trim();
    const year = parseInt(yearInput.value.trim(), 10);
    const url = urlDisplay.textContent ?? "";
    const pageType = pageTypeSelect.value;
    const exportMd = exportMdCheckbox.checked;
    const exportPath = exportPathInput.value.trim();

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
    sendBtn.textContent = "Reading page…";
    appendPreflightLog("Reading current tab HTML content…");

    // Get current page HTML
    const pageHTML = await getCurrentPageHTML();
    if (!pageHTML) {
        appendPreflightLog("Failed to read page HTML from browser tab.");
        showStatus("Failed to read page content. Please refresh and try again.", "error");
        sendBtn.disabled = false;
        sendBtn.textContent = "Start Crawl";
        return;
    }
    lastPageHTML = pageHTML;
    appendPreflightLog(`Captured page HTML (${pageHTML.length.toLocaleString()} chars).`);

    sendBtn.textContent = "Analyzing…";
    appendPreflightLog("Analyzing page type and candidate links…");

    try {
        // Step 1: Analyze the page
        const analyzeRes = await fetch(`${API_BASE}/analyze`, {
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
            `Analyze complete: page_type=${analyzeData.page_type}, candidates=${analyzeData.links.length}, total_found=${analyzeData.total_found}.`
        );

        if (analyzeData.page_type === "detail") {
            // Detail page: start crawl directly with browser HTML
            appendPreflightLog("Detected detail page; submitting crawl job.");
            await submitCrawl({ url, slug, year, pageType, exportMd, exportPath, htmlContent: pageHTML });
        } else {
            // Index page: show link selection UI
            if (analyzeData.links.length === 0) {
                appendPreflightLog("No candidate detail links found on index page.");
                showStatus("No program links found on this page.", "error");
                return;
            }
            appendPreflightLog("Rendering candidate detail links for manual selection.");
            linkSelectionFlow.showLinkSelection(analyzeData.links, analyzeData.total_found);
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

/**
 * Submit a crawl job to the server and switch to the monitor view.
 */
async function submitCrawl(opts: {
    url: string;
    slug: string;
    year: number;
    pageType: string;
    exportMd: boolean;
    exportPath: string;
    htmlContent?: string;
    selectedUrls?: string[];
    selectedLinkTexts?: Record<string, string>;
}) {
    const taxonomyOptions = getTaxonomyOptions();
    const payload: CrawlPayload = {
        url: opts.url,
        univ_slug: opts.slug,
        year: opts.year,
        continue_depth: 0,
        page_type_hint: opts.pageType,
        taxonomy_enabled: taxonomyOptions.enabled,
        taxonomy_low_threshold: taxonomyOptions.lowThreshold,
        taxonomy_high_threshold: taxonomyOptions.highThreshold,
        taxonomy_hint_top_k: taxonomyOptions.hintTopK,
        taxonomy_override_enabled: taxonomyOptions.overrideEnabled,
    };

    if (opts.htmlContent) {
        payload.html_content = opts.htmlContent;
    }
    if (opts.selectedUrls) {
        payload.selected_urls = opts.selectedUrls;
    }
    if (opts.selectedLinkTexts) {
        payload.selected_link_texts = opts.selectedLinkTexts;
    }
    if (opts.exportMd && opts.exportPath) {
        payload.export_md = true;
        payload.export_path = opts.exportPath;
    }

    const res = await fetch(`${API_BASE}/crawl`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });

    setFormEnabled(false);

    if (res.status === 409) {
        showStatus("A task is already running!", "error");
        init();
        return;
    }

    if (!res.ok) {
        throw new Error(`Server error: ${res.status}`);
    }

    const data = await res.json();
    monitorFlow?.startMonitoring(data.task_id);
}

monitorFlow = initMonitorFlow({
    apiBase: API_BASE,
    showStatus,
    switchView,
    setFormEnabled,
    taskIdDisplay,
    progressText,
    progressFill,
    tokenDisplay,
    logsConsole,
    stopBtn,
    continueBtn,
});

init();

// ---------------------------------------------------------------------------
//  Feature Modules
// ---------------------------------------------------------------------------

initConfigFlow({
    apiBase: API_BASE,
    configBtn,
    closeConfigBtn,
    saveConfigBtn,
    configModal,
    dbUrlInput,
    llmList,
    showStatus,
});

initExportFlow({
    apiBase: API_BASE,
    showStatus,
    getUniversities: () => cachedUniversities,
    sourceSlugInput: slugInput,
    sourceYearInput: yearInput,
    exportBtn,
    exportModal,
    closeExportBtn,
    exportSlugInput,
    exportSlugDropdown,
    exportYearInput,
    doExportBtn,
});

initPreviewFlow({
    apiBase: API_BASE,
    showStatus,
    getUniversities: () => cachedUniversities,
    sourceSlugInput: slugInput,
    sourceYearInput: yearInput,
    previewBtn,
    previewModal,
    closePreviewBtn,
    previewSlugInput,
    previewSlugDropdown,
    previewYearInput,
    previewSearchBtn,
    previewSummary,
    previewCountBadge,
    previewList,
});
