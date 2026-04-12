/**
 * UniAdmission Agent — Chrome Extension Popup Logic
 *
 * Features:
 *  - Start Crawl (Singleton)
 *  - Monitor Progress (Logs stream)
 *  - Stop Task
 *  - Structured Config Editor (Draggable LLM List)
 *
 * Module layout:
 *  popup.ts             — state, UI helpers, URL tracking, init, module wiring
 *  popup/preferences.ts — localStorage constants, restoreCachedPreferences, form listeners
 *  popup/slugAutocomplete.ts — university slug autocomplete
 *  popup/crawlApi.ts    — submitCrawl, submitAgentRun, waitForTaskTerminal, pure utils
 *  popup/crawlFlow.ts   — runIndexBatches, sendBtn handler, getCurrentPageHTML
 */

import {
    batchSummaryText,
    closeConfigBtn,
    closeExportBtn,
    closePreviewBtn,
    closePreviewEditBtn,
    configBtn,
    configModal,
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
    linkSelectionSection,
    llmList,
    logsConsole,
    monitorSection,
    pageTypeSelect,
    preflightLogConsole,
    preflightLogSection,
    previewBtn,
    previewCountBadge,
    previewEditCancelBtn,
    previewEditCurrencyInput,
    previewEditDeadlinesInput,
    previewEditFacultyInput,
    previewEditGroupCodeInput,
    previewEditModal,
    previewEditNameEnInput,
    previewEditNameZhInput,
    previewEditRequirementsInput,
    previewEditSaveBtn,
    previewEditSourceUrlInput,
    previewEditStudyOptionsInput,
    previewEditTuitionInput,
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
    sendBtn,
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
import { initMonitorFlow } from "./popup/monitorFlow";
import { initPreviewFlow } from "./popup/previewFlow";
import {
    LOGS_EXPANDED_KEY,
    restoreCachedPreferences,
    initPreferenceListeners,
} from "./popup/preferences";
import {
    loadUniversities,
    getCachedUniversities,
    initSlugAutocomplete,
} from "./popup/slugAutocomplete";
import { initCrawlFlow } from "./popup/crawlFlow";
import type { TaskInfo } from "./popup/types";

const API_BASE = "http://localhost:8910";

// ---------------------------------------------------------------------------
//  State
// ---------------------------------------------------------------------------

let currentWindowId: number | null = null;
let monitorFlow: ReturnType<typeof initMonitorFlow> | null = null;
let serverAgentEnabled = false;  // Whether server currently allows the default agent path

// ---------------------------------------------------------------------------
//  UI Helpers
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
//  Logs toggle
// ---------------------------------------------------------------------------

function initLogsToggle() {
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
        batchSummaryText.classList.add("hidden");
        batchSummaryText.textContent = "";
        monitorFlow?.clearBatchSummary();
        monitorFlow?.clearBatchLogs();

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

// ---------------------------------------------------------------------------
//  URL tracking — keeps urlDisplay in sync with the active tab
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
//  Initialization
// ---------------------------------------------------------------------------

async function init() {
    // Restore cached preferences
    restoreCachedPreferences({
        updateTaxonomySettingsVisibility,
    });
    clearPreflightLogs();

    // Get current URL and setup auto-tracking for side panel
    updateCurrentUrl();
    setupTabListeners();

    // Initialize logs toggle state
    initLogsToggle();

    // Check server status so the popup can fall back only if agent is explicitly disabled
    try {
        const statusRes = await fetch(`${API_BASE}/status`);
        if (statusRes.ok) {
            const statusData = await statusRes.json();
            serverAgentEnabled = Boolean(statusData.agent_enabled);
        }
    } catch (err) {
        console.warn("Failed to check server agent status:", err);
        serverAgentEnabled = false;
    }

    // Load university slugs for autocomplete
    await loadUniversities(API_BASE);
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
//  Module wiring
// ---------------------------------------------------------------------------

monitorFlow = initMonitorFlow({
    apiBase: API_BASE,
    showStatus,
    switchView,
    setFormEnabled,
    taskIdDisplay,
    progressText,
    batchSummaryText,
    progressFill,
    tokenDisplay,
    logsConsole,
    stopBtn,
    continueBtn,
});

initCrawlFlow({
    apiBase: API_BASE,
    showStatus,
    switchView,
    setFormEnabled,
    appendPreflightLog,
    clearPreflightLogs,
    getTaxonomyOptions,
    getMonitorFlow: () => monitorFlow,
    serverAgentEnabled: () => serverAgentEnabled,
    reinit: init,
});

initPreferenceListeners({ updateTaxonomySettingsVisibility });

init();

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
    getUniversities: getCachedUniversities,
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
    getUniversities: getCachedUniversities,
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
    previewEditModal,
    closePreviewEditBtn,
    previewEditCancelBtn,
    previewEditSaveBtn,
    previewEditNameEnInput,
    previewEditNameZhInput,
    previewEditFacultyInput,
    previewEditGroupCodeInput,
    previewEditTuitionInput,
    previewEditCurrencyInput,
    previewEditSourceUrlInput,
    previewEditStudyOptionsInput,
    previewEditDeadlinesInput,
    previewEditRequirementsInput,
});
