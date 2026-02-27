/**
 * UniAdmission Agent — Chrome Extension Popup Logic
 *
 * Features:
 *  - Start Crawl (Singleton)
 *  - Monitor Progress (Logs stream)
 *  - Stop Task
 *  - Structured Config Editor (Draggable LLM List)
 */

const API_BASE = "http://localhost:8910";

// ---------------------------------------------------------------------------
//  Types
// ---------------------------------------------------------------------------

interface TaskParams {
    url: string;
    univ_slug: string;
    year: number;
    continue_depth: number;
}

interface TaskInfo {
    task_id: string;
    state: string;
    progress?: string;
    result?: any;
    error?: string;
    logs?: string[];
    params?: TaskParams;
    tokens_used?: number;
}

interface StructuredConfig {
    database_url: string;
    llm_priority: string[];
    providers: Record<string, Record<string, string>>;
}

interface UniversityOption {
    slug: string;
    name: string;
    updated_at: string;
}

interface LinkCandidate {
    url: string;
    text: string;
}

interface AnalyzeResult {
    page_type: string;
    links: LinkCandidate[];
    total_found: number;
}

// ---------------------------------------------------------------------------
//  DOM Elements
// ---------------------------------------------------------------------------

// Sections
const inputSection = document.getElementById("input-section") as HTMLDivElement;
const linkSelectionSection = document.getElementById("link-selection-section") as HTMLDivElement;
const monitorSection = document.getElementById("monitor-section") as HTMLDivElement;
const configModal = document.getElementById("config-modal") as HTMLDivElement;

// Input Form
const slugInput = document.getElementById("slug") as HTMLInputElement;
const slugDropdown = document.getElementById("slug-dropdown") as HTMLUListElement;
const yearInput = document.getElementById("year") as HTMLInputElement;
const urlDisplay = document.getElementById("current-url") as HTMLParagraphElement;
const pageTypeSelect = document.getElementById("page-type") as HTMLSelectElement;
const exportMdCheckbox = document.getElementById("export-md") as HTMLInputElement;
const exportPathInput = document.getElementById("export-path") as HTMLInputElement;
const exportPathField = document.getElementById("export-path-field") as HTMLDivElement;
const sendBtn = document.getElementById("send-btn") as HTMLButtonElement;

// Monitor
const taskIdDisplay = document.getElementById("task-id-display") as HTMLSpanElement;
const progressText = document.getElementById("progress-text") as HTMLParagraphElement;
const progressFill = document.getElementById("progress-fill") as HTMLDivElement;
const tokenDisplay = document.getElementById("token-display") as HTMLSpanElement;
const logsConsole = document.getElementById("logs-console") as HTMLPreElement;
const toggleLogsBtn = document.getElementById("toggle-logs-btn") as HTMLButtonElement;
const stopBtn = document.getElementById("stop-btn") as HTMLButtonElement;
const continueBtn = document.getElementById("continue-btn") as HTMLButtonElement;

// Config
const configBtn = document.getElementById("config-btn") as HTMLButtonElement;
const closeConfigBtn = document.getElementById("close-config-btn") as HTMLButtonElement;
const saveConfigBtn = document.getElementById("save-config-btn") as HTMLButtonElement;
const dbUrlInput = document.getElementById("db-url-input") as HTMLInputElement;
const llmList = document.getElementById("llm-list") as HTMLUListElement;

// Export
const exportBtn = document.getElementById("export-btn") as HTMLButtonElement;
const exportModal = document.getElementById("export-modal") as HTMLDivElement;
const closeExportBtn = document.getElementById("close-export-btn") as HTMLButtonElement;
const exportSlugInput = document.getElementById("export-slug") as HTMLInputElement;
const exportSlugDropdown = document.getElementById("export-slug-dropdown") as HTMLUListElement;
const exportYearInput = document.getElementById("export-year") as HTMLInputElement;
const doExportBtn = document.getElementById("do-export-btn") as HTMLButtonElement;

// Preview
const previewBtn = document.getElementById("preview-btn") as HTMLButtonElement;
const previewModal = document.getElementById("preview-modal") as HTMLDivElement;
const closePreviewBtn = document.getElementById("close-preview-btn") as HTMLButtonElement;
const previewSlugInput = document.getElementById("preview-slug") as HTMLInputElement;
const previewSlugDropdown = document.getElementById("preview-slug-dropdown") as HTMLUListElement;
const previewYearInput = document.getElementById("preview-year") as HTMLInputElement;
const previewSearchBtn = document.getElementById("preview-search-btn") as HTMLButtonElement;
const previewSummary = document.getElementById("preview-summary") as HTMLDivElement;
const previewCountBadge = document.getElementById("preview-count-badge") as HTMLSpanElement;
const previewList = document.getElementById("preview-list") as HTMLDivElement;

// Status
const statusDiv = document.getElementById("status") as HTMLDivElement;

// Link Selection
const selectAllLinksCheckbox = document.getElementById("select-all-links") as HTMLInputElement;
const linkCountBadge = document.getElementById("link-count") as HTMLSpanElement;
const linkListEl = document.getElementById("link-list") as HTMLUListElement;
const confirmLinksBtn = document.getElementById("confirm-links-btn") as HTMLButtonElement;
const cancelLinksBtn = document.getElementById("cancel-links-btn") as HTMLButtonElement;

// ---------------------------------------------------------------------------
//  State & Utils
// ---------------------------------------------------------------------------

let activePollInterval: number | null = null;
let currentWindowId: number | null = null;
let candidateLinks: LinkCandidate[] = [];
let lastPageHTML: string | null = null;
let draggedItem: HTMLElement | null = null;
const LOGS_EXPANDED_KEY = "logs_expanded";

// Cache keys for user preferences
const PAGE_TYPE_KEY = "crawl_page_type";
const EXPORT_MD_KEY = "crawl_export_md";
const EXPORT_PATH_KEY = "crawl_export_path";
const UNIV_SLUG_KEY = "crawl_univ_slug";

// Slug autocomplete state
let cachedUniversities: UniversityOption[] = [];
let activeDropdownIndex = -1;
let activeExportDropdownIndex = -1;
let activePreviewDropdownIndex = -1;

// Helper to disable/enable form
function setFormEnabled(enabled: boolean) {
    slugInput.disabled = !enabled;
    yearInput.disabled = !enabled;
    pageTypeSelect.disabled = !enabled;
    exportMdCheckbox.disabled = !enabled;
    exportPathInput.disabled = !enabled;
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

function switchView(view: "input" | "link-selection" | "monitor") {
    inputSection.classList.add("hidden");
    linkSelectionSection.classList.add("hidden");
    monitorSection.classList.add("hidden");
    statusDiv.classList.add("hidden");

    if (view === "input") {
        inputSection.classList.remove("hidden");
        stopPolling();
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
                startMonitoring(data.task_id);

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

init();

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

sendBtn.addEventListener("click", async () => {
    const slug = slugInput.value.trim();
    const year = parseInt(yearInput.value.trim(), 10);
    const url = urlDisplay.textContent ?? "";
    const pageType = pageTypeSelect.value;
    const exportMd = exportMdCheckbox.checked;
    const exportPath = exportPathInput.value.trim();

    if (!slug || !year || !url || url.startsWith("(")) {
        showStatus("Invalid input or URL", "error");
        return;
    }

    if (exportMd && !exportPath) {
        showStatus("Export path is required when export is enabled", "error");
        return;
    }

    sendBtn.disabled = true;
    sendBtn.textContent = "Reading page…";

    // Get current page HTML
    const pageHTML = await getCurrentPageHTML();
    if (!pageHTML) {
        showStatus("Failed to read page content. Please refresh and try again.", "error");
        sendBtn.disabled = false;
        sendBtn.textContent = "Start Crawl";
        return;
    }
    lastPageHTML = pageHTML;

    sendBtn.textContent = "Analyzing…";

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

        if (analyzeData.page_type === "detail") {
            // Detail page: start crawl directly with browser HTML
            await submitCrawl({ url, slug, year, pageType, exportMd, exportPath, htmlContent: pageHTML });
        } else {
            // Index page: show link selection UI
            if (analyzeData.links.length === 0) {
                showStatus("No program links found on this page.", "error");
                return;
            }
            candidateLinks = analyzeData.links;
            renderLinkSelection(analyzeData.links, analyzeData.total_found);
            switchView("link-selection");
        }
    } catch (err) {
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
}) {
    const payload: any = {
        url: opts.url,
        univ_slug: opts.slug,
        year: opts.year,
        continue_depth: 0,
        page_type_hint: opts.pageType,
    };

    if (opts.htmlContent) {
        payload.html_content = opts.htmlContent;
    }
    if (opts.selectedUrls) {
        payload.selected_urls = opts.selectedUrls;
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
    startMonitoring(data.task_id);
}

// ---------------------------------------------------------------------------
//  Link Selection
// ---------------------------------------------------------------------------

function renderLinkSelection(links: LinkCandidate[], totalFound: number) {
    linkListEl.innerHTML = "";
    selectAllLinksCheckbox.checked = true;

    links.forEach((link, idx) => {
        const li = document.createElement("li");
        li.className = "link-item selected";

        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.idx = String(idx);

        const content = document.createElement("div");
        content.className = "link-item-content";

        const textSpan = document.createElement("div");
        textSpan.className = "link-item-text";
        textSpan.textContent = link.text || "(no text)";

        const urlSpan = document.createElement("div");
        urlSpan.className = "link-item-url";
        urlSpan.textContent = link.url;

        content.appendChild(textSpan);
        content.appendChild(urlSpan);

        li.appendChild(cb);
        li.appendChild(content);

        // Click anywhere on the item to toggle
        li.addEventListener("click", (e) => {
            if ((e.target as HTMLElement).tagName !== "INPUT") {
                cb.checked = !cb.checked;
            }
            li.classList.toggle("selected", cb.checked);
            updateLinkCount();
        });

        cb.addEventListener("change", () => {
            li.classList.toggle("selected", cb.checked);
            updateLinkCount();
        });

        linkListEl.appendChild(li);
    });

    updateLinkCount();
}

function updateLinkCount() {
    const checkboxes = linkListEl.querySelectorAll<HTMLInputElement>("input[type=checkbox]");
    const checked = Array.from(checkboxes).filter(cb => cb.checked).length;
    linkCountBadge.textContent = `${checked}/${checkboxes.length} selected`;

    // Sync "Select All" state
    selectAllLinksCheckbox.checked = checked === checkboxes.length;
    selectAllLinksCheckbox.indeterminate = checked > 0 && checked < checkboxes.length;

    confirmLinksBtn.disabled = checked === 0;
}

selectAllLinksCheckbox.addEventListener("change", () => {
    const isChecked = selectAllLinksCheckbox.checked;
    linkListEl.querySelectorAll<HTMLInputElement>("input[type=checkbox]").forEach(cb => {
        cb.checked = isChecked;
        const li = cb.closest(".link-item");
        if (li) li.classList.toggle("selected", isChecked);
    });
    updateLinkCount();
});

confirmLinksBtn.addEventListener("click", async () => {
    const checkboxes = linkListEl.querySelectorAll<HTMLInputElement>("input[type=checkbox]");
    const selectedUrls: string[] = [];
    checkboxes.forEach(cb => {
        if (cb.checked) {
            const idx = parseInt(cb.dataset.idx!, 10);
            if (candidateLinks[idx]) {
                selectedUrls.push(candidateLinks[idx].url);
            }
        }
    });

    if (selectedUrls.length === 0) {
        showStatus("No links selected", "error");
        return;
    }

    confirmLinksBtn.disabled = true;
    confirmLinksBtn.textContent = "Starting…";

    try {
        const url = urlDisplay.textContent ?? "";
        const slug = slugInput.value.trim();
        const year = parseInt(yearInput.value.trim(), 10);
        const exportMd = exportMdCheckbox.checked;
        const exportPath = exportPathInput.value.trim();

        await submitCrawl({
            url,
            slug,
            year,
            pageType: "detail",
            exportMd,
            exportPath,
            selectedUrls,
        });
    } catch (err) {
        showStatus(String(err), "error");
    } finally {
        confirmLinksBtn.disabled = false;
        confirmLinksBtn.textContent = "Crawl Selected";
    }
});

cancelLinksBtn.addEventListener("click", () => {
    candidateLinks = [];
    switchView("input");
    setFormEnabled(true);
});

function startMonitoring(taskId: string) {
    switchView("monitor");
    taskIdDisplay.textContent = taskId;
    taskIdDisplay.textContent = taskId;
    // Do NOT clear logs if we are reconnecting to a running task!
    // logsConsole.textContent = ""; 

    // Check if we are reconnecting (logs might be empty initially)
    // Actually, pollTask will overwrite textContent. 
    // To avoid flicker, we can clear only if we are starting FRESH.
    // But distinguishing fresh vs reconnect is hard here.
    // Let's just let pollTask handle it. It replaces textContent.

    stopBtn.classList.remove("hidden"); // Default visible when monitoring starts
    continueBtn.classList.add("hidden"); // Hide continue button
    tokenDisplay.textContent = "Tokens: 0";
    tokenDisplay.classList.remove("hidden");

    pollTask(taskId);
    if (activePollInterval) clearInterval(activePollInterval);
    activePollInterval = window.setInterval(() => pollTask(taskId), 2000);
}

function stopPolling() {
    if (activePollInterval) {
        clearInterval(activePollInterval);
        activePollInterval = null;
    }
}

async function pollTask(taskId: string) {
    try {
        const res = await fetch(`${API_BASE}/tasks/${taskId}`);
        if (!res.ok) {
            if (res.status === 404) {
                stopPolling();
                switchView("input");
                showStatus("Task lost or cleaned up", "info");
                return;
            }
            return;
        }

        const data = await res.json();
        const { state, progress, logs, error, result } = data;

        progressText.textContent = `${state}: ${progress || "..."}`;

        if (data.tokens_used !== undefined) {
            tokenDisplay.textContent = `Tokens: ${data.tokens_used.toLocaleString()}`;
            tokenDisplay.classList.remove("hidden");
        }

        // Stop button visibility (Requirement 2)
        if (state === "RUNNING" || state === "PENDING") {
            stopBtn.classList.remove("hidden");
            continueBtn.classList.add("hidden");
            setFormEnabled(false); // Ensure locked
        } else {
            stopBtn.classList.add("hidden");
            continueBtn.classList.remove("hidden");
            // setFormEnabled(true); // Don't unlock yet, wait for continue
            // sendBtn.textContent = "Start Crawl"; // Wait for continue
        }

        if (logs && Array.isArray(logs)) {
            const text = logs.join("\n");
            const isScrolledToBottom = logsConsole.scrollHeight - logsConsole.scrollTop <= logsConsole.clientHeight + 50;

            if (logsConsole.textContent !== text) {
                logsConsole.textContent = text;
                if (isScrolledToBottom) {
                    logsConsole.scrollTop = logsConsole.scrollHeight;
                }
            }
        }

        if (state === "RUNNING") {
            progressFill.style.width = "60%";
        } else if (state === "DONE") {
            progressFill.style.width = "100%";
            stopPolling();
            showStatus(`Completed! Imported: ${result?.imported_count ?? 0}`, "success");
            // setTimeout(() => switchView("input"), 3000);
        } else if (state === "FAILED") {
            progressFill.style.width = "100%";
            progressFill.style.backgroundColor = "var(--error)";
            stopPolling();
            showStatus(`Failed: ${error}`, "error");
            // setTimeout(() => switchView("input"), 5000);
        }

    } catch (err) {
        console.error("Poll error", err);
    }
}

stopBtn.addEventListener("click", async () => {
    const taskId = taskIdDisplay.textContent;
    if (!taskId) return;
    if (confirm("Stop current task?")) {
        try {
            await fetch(`${API_BASE}/tasks/${taskId}/cancel`, { method: "POST" });
            showStatus("Cancellation requested...", "info");
        } catch (err) {
            showStatus("Failed to cancel", "error");
        }
    }
});

// ---------------------------------------------------------------------------
//  Structured Config Flow
// ---------------------------------------------------------------------------

configBtn.addEventListener("click", async () => {
    try {
        const res = await fetch(`${API_BASE}/config/structured`);
        if (!res.ok) throw new Error("Failed to load config");
        const config: StructuredConfig = await res.json();

        renderConfigForm(config);
        configModal.classList.remove("hidden");
    } catch (err) {
        showStatus("Could not load config", "error");
        console.error(err);
    }
});

closeConfigBtn.addEventListener("click", () => {
    configModal.classList.add("hidden");
});

function renderConfigForm(config: StructuredConfig) {
    dbUrlInput.value = config.database_url;
    llmList.innerHTML = "";

    // Ensure custom provider is always available
    const allProviders = { ...config.providers };
    if (!allProviders.custom) {
        // Add default empty custom provider if not configured
        allProviders.custom = {
            "CUSTOM_LLM_BASE_URL": "",
            "CUSTOM_LLM_API_KEY": "",
            "CUSTOM_LLM_MODEL_NAME": "gpt-4o-mini",
        };
    }

    // Merge priority list with any providers that might be missing from it
    const orderedKeys = [...config.llm_priority];
    const providerNames = Object.keys(allProviders);

    // Add missing providers to the end (including custom)
    for (const p of providerNames) {
        if (!orderedKeys.includes(p)) {
            orderedKeys.push(p);
        }
    }

    // Render items
    orderedKeys.forEach(providerName => {
        const settings = allProviders[providerName];
        if (!settings) return;

        const li = createProviderItem(providerName, settings);
        llmList.appendChild(li);
    });
}

function createProviderItem(name: string, settings: Record<string, string>): HTMLElement {
    const li = document.createElement("li");
    li.className = "llm-item";
    li.draggable = true;
    li.dataset.provider = name;

    // Header
    const header = document.createElement("div");
    header.className = "llm-header";

    // Handle
    const handle = document.createElement("span");
    handle.className = "handle";
    handle.textContent = "☰";
    header.appendChild(handle);

    // Name
    const nameSpan = document.createElement("span");
    nameSpan.className = "name";
    nameSpan.textContent = name;
    header.appendChild(nameSpan);

    // Toggle
    const toggle = document.createElement("button");
    toggle.className = "toggle-btn";
    toggle.textContent = "▼";
    header.appendChild(toggle);

    li.appendChild(header);

    // Settings Body
    const body = document.createElement("div");
    body.className = "llm-settings hidden";

    // Render inputs for each setting
    // We want specific order usually, but object key order is shaky.
    // Let's sort keys: API_KEY first, then others.
    const keys = Object.keys(settings).sort((a, b) => {
        if (a.includes("API_KEY")) return -1;
        if (b.includes("API_KEY")) return 1;
        return a.localeCompare(b);
    });

    keys.forEach(key => {
        const row = document.createElement("div");
        row.className = "setting-row";

        const label = document.createElement("label");
        // Special handling for custom provider
        if (name === "custom") {
            // CUSTOM_LLM_BASE_URL -> Base URL
            const cleanKey = key.replace("CUSTOM_LLM_", "").replace(/_/g, " ");
            label.textContent = cleanKey;
        } else {
            // VOLC_API_KEY -> API KEY
            label.textContent = key.replace(`${name.toUpperCase()}_`, "").replace(/_/g, " ");
        }

        const input = document.createElement("input");
        // Use password type for API keys
        if (key.includes("API_KEY")) {
            input.type = "password";
            input.autocomplete = "off";
        } else {
            input.type = "text";
        }
        input.value = settings[key] || "";
        input.dataset.key = key; // Store original key
        
        // Add placeholder hints for custom provider
        if (name === "custom") {
            if (key === "CUSTOM_LLM_BASE_URL") {
                input.placeholder = "https://api.openai.com/v1";
            } else if (key === "CUSTOM_LLM_API_KEY") {
                input.placeholder = "sk-...";
            } else if (key === "CUSTOM_LLM_MODEL_NAME") {
                input.placeholder = "gpt-4o-mini";
            }
        }

        row.appendChild(label);
        row.appendChild(input);
        body.appendChild(row);
    });

    li.appendChild(body);

    // Event Listeners

    // Toggle
    toggle.addEventListener("click", (e) => {
        e.stopPropagation();
        body.classList.toggle("hidden");
        toggle.textContent = body.classList.contains("hidden") ? "▼" : "▲";
    });

    // Drag Events
    li.addEventListener("dragstart", (e) => {
        draggedItem = li;
        e.dataTransfer!.effectAllowed = "move";
        e.dataTransfer!.setData("text/plain", name);
        li.classList.add("dragging");
    });

    li.addEventListener("dragend", () => {
        li.classList.remove("dragging");
        draggedItem = null;
    });

    li.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer!.dropEffect = "move";

        const targetLi = (e.target as HTMLElement).closest(".llm-item") as HTMLElement;
        if (targetLi && targetLi !== draggedItem) {
            const rect = targetLi.getBoundingClientRect();
            const next = (e.clientY - rect.top) / (rect.bottom - rect.top) > 0.5;
            llmList.insertBefore(draggedItem!, next ? targetLi.nextSibling : targetLi);
        }
    });

    return li;
}

saveConfigBtn.addEventListener("click", async () => {
    saveConfigBtn.disabled = true;
    saveConfigBtn.textContent = "Saving...";

    try {
        // Collect data
        const newPriority: string[] = [];
        const newProviders: Record<string, Record<string, string>> = {};

        const items = llmList.querySelectorAll(".llm-item");
        items.forEach((item) => {
            const li = item as HTMLElement;
            const name = li.dataset.provider!;

            const settings: Record<string, string> = {};
            const inputs = li.querySelectorAll("input");
            inputs.forEach(inp => {
                settings[inp.dataset.key!] = inp.value.trim();
            });
            
            // For custom provider, only include if BASE_URL is configured
            if (name === "custom") {
                const baseUrl = settings["CUSTOM_LLM_BASE_URL"];
                if (baseUrl) {
                    newPriority.push(name);
                    newProviders[name] = settings;
                }
                // Skip custom if not configured
            } else {
                newPriority.push(name);
                newProviders[name] = settings;
            }
        });

        const payload: StructuredConfig = {
            database_url: dbUrlInput.value.trim(),
            llm_priority: newPriority,
            providers: newProviders,
        };

        const res = await fetch(`${API_BASE}/config/structured`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        if (!res.ok) throw new Error("Failed to save");

        showStatus("Config saved!", "success");
        configModal.classList.add("hidden");

    } catch (err) {
        showStatus("Failed to save config", "error");
        console.error(err);
    } finally {
        saveConfigBtn.disabled = false;
        saveConfigBtn.textContent = "Save Changes";
    }
});

// ---------------------------------------------------------------------------
//  Export Flow
// ---------------------------------------------------------------------------

exportBtn.addEventListener("click", () => {
    // Pre-fill from main form if available
    exportSlugInput.value = slugInput.value.trim();
    exportYearInput.value = yearInput.value.trim();
    exportModal.classList.remove("hidden");
    exportSlugInput.focus();
});

closeExportBtn.addEventListener("click", () => {
    exportModal.classList.add("hidden");
});

// Autocomplete for the export slug input (reuses cachedUniversities)
function initExportSlugAutocomplete(): void {
    exportSlugInput.addEventListener("input", () => {
        renderExportDropdown(exportSlugInput.value.trim());
    });

    exportSlugInput.addEventListener("focus", () => {
        renderExportDropdown(exportSlugInput.value.trim());
    });

    exportSlugInput.addEventListener("keydown", (e: KeyboardEvent) => {
        const items = exportSlugDropdown.querySelectorAll("li");
        if (!items.length || exportSlugDropdown.classList.contains("hidden")) {
            return;
        }

        if (e.key === "ArrowDown") {
            e.preventDefault();
            activeExportDropdownIndex = Math.min(activeExportDropdownIndex + 1, items.length - 1);
            highlightExportItem(items);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            activeExportDropdownIndex = Math.max(activeExportDropdownIndex - 1, 0);
            highlightExportItem(items);
        } else if (e.key === "Enter") {
            if (activeExportDropdownIndex >= 0 && activeExportDropdownIndex < items.length) {
                e.preventDefault();
                const slug = (items[activeExportDropdownIndex] as HTMLElement).dataset.slug;
                if (slug) {
                    exportSlugInput.value = slug;
                }
                hideExportDropdown();
            }
        } else if (e.key === "Escape") {
            hideExportDropdown();
        }
    });

    document.addEventListener("click", (e: MouseEvent) => {
        const target = e.target as HTMLElement;
        if (!target.closest("#export-modal .autocomplete-wrapper")) {
            hideExportDropdown();
        }
    });
}

function renderExportDropdown(query: string): void {
    exportSlugDropdown.innerHTML = "";
    activeExportDropdownIndex = -1;

    const filtered = query
        ? cachedUniversities.filter(
            (u) =>
                u.slug.toLowerCase().includes(query.toLowerCase()) ||
                u.name.toLowerCase().includes(query.toLowerCase())
        )
        : cachedUniversities;

    if (filtered.length === 0) {
        hideExportDropdown();
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
            activeExportDropdownIndex = idx;
            highlightExportItem(exportSlugDropdown.querySelectorAll("li"));
        });

        li.addEventListener("click", () => {
            exportSlugInput.value = u.slug;
            hideExportDropdown();
            exportSlugInput.focus();
        });

        exportSlugDropdown.appendChild(li);
    });

    exportSlugDropdown.classList.remove("hidden");

    if (filtered.length === 1 && filtered[0].slug.toLowerCase() === query.toLowerCase()) {
        activeExportDropdownIndex = 0;
        highlightExportItem(exportSlugDropdown.querySelectorAll("li"));
    }
}

function highlightExportItem(items: NodeListOf<Element>): void {
    items.forEach((item, idx) => {
        item.classList.toggle("active", idx === activeExportDropdownIndex);
    });
    if (activeExportDropdownIndex >= 0 && items[activeExportDropdownIndex]) {
        (items[activeExportDropdownIndex] as HTMLElement).scrollIntoView({ block: "nearest" });
    }
}

function hideExportDropdown(): void {
    exportSlugDropdown.classList.add("hidden");
    activeExportDropdownIndex = -1;
}

// Perform export & download
doExportBtn.addEventListener("click", async () => {
    const slug = exportSlugInput.value.trim();
    if (!slug) {
        showStatus("University slug is required", "error");
        return;
    }

    const yearStr = exportYearInput.value.trim();
    const year = yearStr ? parseInt(yearStr, 10) : null;

    doExportBtn.disabled = true;
    doExportBtn.textContent = "Exporting…";

    try {
        const res = await fetch(`${API_BASE}/export`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ univ_slug: slug, year }),
        });

        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
            throw new Error(err.detail || `Export failed: ${res.status}`);
        }

        // Trigger browser download
        const blob = await res.blob();
        const disposition = res.headers.get("Content-Disposition") || "";
        const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
        const filename = filenameMatch ? filenameMatch[1] : `${slug}_export.xlsx`;

        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        showStatus(`Exported ${filename}`, "success");
        exportModal.classList.add("hidden");

    } catch (err) {
        showStatus(String(err), "error");
    } finally {
        doExportBtn.disabled = false;
        doExportBtn.textContent = "📥 Export";
    }
});

// Initialize export autocomplete alongside main autocomplete
initExportSlugAutocomplete();

// ---------------------------------------------------------------------------
//  Preview Flow
// ---------------------------------------------------------------------------

interface ProgramRecord {
    id: number | null;
    name_en: string;
    name_zh: string | null;
    academic_year: number;
    faculty: string | null;
    program_group_code: string | null;
    tuition_amount: number | null;
    currency: string | null;
    study_options: { mode: string; duration_months: number }[];
    deadlines: { round?: number; description?: string; cutoff_date?: string }[];
    source_url: string | null;
}

previewBtn.addEventListener("click", () => {
    previewSlugInput.value = slugInput.value.trim();
    previewYearInput.value = yearInput.value.trim();
    previewModal.classList.remove("hidden");
    previewSlugInput.focus();
});

closePreviewBtn.addEventListener("click", () => {
    previewModal.classList.add("hidden");
});

previewSearchBtn.addEventListener("click", () => loadPreview());

// Allow Enter in the inputs to trigger search
previewSlugInput.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Enter" && previewSlugDropdown.classList.contains("hidden")) {
        e.preventDefault();
        loadPreview();
    }
});
previewYearInput.addEventListener("keydown", (e: KeyboardEvent) => {
    if (e.key === "Enter") {
        e.preventDefault();
        loadPreview();
    }
});

async function loadPreview() {
    const slug = previewSlugInput.value.trim();
    if (!slug) {
        showStatus("University slug is required", "error");
        return;
    }
    const yearStr = previewYearInput.value.trim();
    const yearParam = yearStr ? `&year=${parseInt(yearStr, 10)}` : "";

    previewSearchBtn.disabled = true;
    previewSearchBtn.textContent = "Loading…";
    previewList.innerHTML = '<div class="preview-empty">Loading…</div>';

    try {
        const res = await fetch(`${API_BASE}/programs?univ_slug=${encodeURIComponent(slug)}${yearParam}`);
        if (!res.ok) {
            const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
            throw new Error(err.detail || `Query failed: ${res.status}`);
        }
        const programs: ProgramRecord[] = await res.json();
        renderPreviewResults(programs);
    } catch (err) {
        previewList.innerHTML = `<div class="preview-empty" style="color:var(--error)">${String(err)}</div>`;
        previewSummary.classList.add("hidden");
    } finally {
        previewSearchBtn.disabled = false;
        previewSearchBtn.textContent = "Search";
    }
}

function renderPreviewResults(programs: ProgramRecord[]) {
    previewCountBadge.textContent = `${programs.length} program${programs.length !== 1 ? "s" : ""}`;
    previewSummary.classList.remove("hidden");

    if (programs.length === 0) {
        previewList.innerHTML = '<div class="preview-empty">No programs found</div>';
        return;
    }

    previewList.innerHTML = "";
    for (const p of programs) {
        const card = document.createElement("div");
        card.className = "program-card";

        // Header: name + ID
        const header = document.createElement("div");
        header.className = "program-card-header";

        const nameEl = document.createElement("div");
        nameEl.className = "program-card-name";
        nameEl.textContent = p.name_en || "(unnamed)";
        header.appendChild(nameEl);

        if (p.program_group_code) {
            const idEl = document.createElement("span");
            idEl.className = "program-card-id";
            idEl.textContent = p.program_group_code;
            header.appendChild(idEl);
        }
        card.appendChild(header);

        // Tags row
        const meta = document.createElement("div");
        meta.className = "program-card-meta";

        if (p.faculty) {
            const t = document.createElement("span");
            t.className = "program-tag faculty";
            t.textContent = p.faculty;
            meta.appendChild(t);
        }

        if (p.tuition_amount != null) {
            const t = document.createElement("span");
            t.className = "program-tag tuition";
            const cur = p.currency ?? "";
            t.textContent = `${cur} ${p.tuition_amount.toLocaleString()}`;
            meta.appendChild(t);
        }

        if (p.study_options?.length) {
            for (const opt of p.study_options) {
                const t = document.createElement("span");
                t.className = "program-tag mode";
                const months = opt.duration_months;
                const dur = months >= 12 ? `${(months / 12).toFixed(months % 12 ? 1 : 0)}yr` : `${months}mo`;
                t.textContent = `${opt.mode} · ${dur}`;
                meta.appendChild(t);
            }
        }

        if (meta.children.length > 0) {
            card.appendChild(meta);
        }

        // Deadlines
        if (p.deadlines?.length) {
            const details = document.createElement("details");
            details.className = "program-card-deadlines";
            const summary = document.createElement("summary");
            summary.textContent = `${p.deadlines.length} deadline${p.deadlines.length > 1 ? "s" : ""}`;
            details.appendChild(summary);

            const ul = document.createElement("ul");
            ul.className = "deadline-list";
            for (const d of p.deadlines) {
                const li = document.createElement("li");
                li.className = "deadline-item";

                const roundEl = document.createElement("span");
                roundEl.className = "dl-round";
                roundEl.textContent = d.round ? `R${d.round}` : "—";
                li.appendChild(roundEl);

                const dateEl = document.createElement("span");
                dateEl.className = "dl-date";
                dateEl.textContent = d.cutoff_date ? new Date(d.cutoff_date).toLocaleDateString() : "TBD";
                li.appendChild(dateEl);

                if (d.description) {
                    const descEl = document.createElement("span");
                    descEl.textContent = d.description;
                    li.appendChild(descEl);
                }
                ul.appendChild(li);
            }
            details.appendChild(ul);
            card.appendChild(details);
        }

        // Source URL
        if (p.source_url) {
            const a = document.createElement("a");
            a.className = "program-card-url";
            a.href = p.source_url;
            a.target = "_blank";
            a.rel = "noopener";
            // Show only the pathname for brevity
            try {
                const u = new URL(p.source_url);
                a.textContent = u.host + u.pathname;
            } catch {
                a.textContent = p.source_url;
            }
            card.appendChild(a);
        }

        previewList.appendChild(card);
    }
}

// Preview slug autocomplete (reuses cachedUniversities)
function initPreviewSlugAutocomplete(): void {
    previewSlugInput.addEventListener("input", () => {
        renderPreviewDropdown(previewSlugInput.value.trim());
    });
    previewSlugInput.addEventListener("focus", () => {
        renderPreviewDropdown(previewSlugInput.value.trim());
    });
    previewSlugInput.addEventListener("keydown", (e: KeyboardEvent) => {
        const items = previewSlugDropdown.querySelectorAll("li");
        if (!items.length || previewSlugDropdown.classList.contains("hidden")) return;
        if (e.key === "ArrowDown") {
            e.preventDefault();
            activePreviewDropdownIndex = Math.min(activePreviewDropdownIndex + 1, items.length - 1);
            highlightPreviewItem(items);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            activePreviewDropdownIndex = Math.max(activePreviewDropdownIndex - 1, 0);
            highlightPreviewItem(items);
        } else if (e.key === "Enter") {
            if (activePreviewDropdownIndex >= 0 && activePreviewDropdownIndex < items.length) {
                e.preventDefault();
                const slug = (items[activePreviewDropdownIndex] as HTMLElement).dataset.slug;
                if (slug) previewSlugInput.value = slug;
                hidePreviewDropdown();
            }
        } else if (e.key === "Escape") {
            hidePreviewDropdown();
        }
    });
    document.addEventListener("click", (e: MouseEvent) => {
        const target = e.target as HTMLElement;
        if (!target.closest("#preview-modal .autocomplete-wrapper")) {
            hidePreviewDropdown();
        }
    });
}

function renderPreviewDropdown(query: string): void {
    previewSlugDropdown.innerHTML = "";
    activePreviewDropdownIndex = -1;
    const filtered = query
        ? cachedUniversities.filter(
            (u) =>
                u.slug.toLowerCase().includes(query.toLowerCase()) ||
                u.name.toLowerCase().includes(query.toLowerCase())
        )
        : cachedUniversities;
    if (filtered.length === 0) { hidePreviewDropdown(); return; }
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
            activePreviewDropdownIndex = idx;
            highlightPreviewItem(previewSlugDropdown.querySelectorAll("li"));
        });
        li.addEventListener("click", () => {
            previewSlugInput.value = u.slug;
            hidePreviewDropdown();
            previewSlugInput.focus();
        });
        previewSlugDropdown.appendChild(li);
    });
    previewSlugDropdown.classList.remove("hidden");
    if (filtered.length === 1 && filtered[0].slug.toLowerCase() === query.toLowerCase()) {
        activePreviewDropdownIndex = 0;
        highlightPreviewItem(previewSlugDropdown.querySelectorAll("li"));
    }
}

function highlightPreviewItem(items: NodeListOf<Element>): void {
    items.forEach((item, idx) => {
        item.classList.toggle("active", idx === activePreviewDropdownIndex);
    });
    if (activePreviewDropdownIndex >= 0 && items[activePreviewDropdownIndex]) {
        (items[activePreviewDropdownIndex] as HTMLElement).scrollIntoView({ block: "nearest" });
    }
}

function hidePreviewDropdown(): void {
    previewSlugDropdown.classList.add("hidden");
    activePreviewDropdownIndex = -1;
}

initPreviewSlugAutocomplete();
