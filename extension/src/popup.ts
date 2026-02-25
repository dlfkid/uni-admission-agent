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

// ---------------------------------------------------------------------------
//  DOM Elements
// ---------------------------------------------------------------------------

// Sections
const inputSection = document.getElementById("input-section") as HTMLDivElement;
const monitorSection = document.getElementById("monitor-section") as HTMLDivElement;
const configModal = document.getElementById("config-modal") as HTMLDivElement;

// Input Form
const slugInput = document.getElementById("slug") as HTMLInputElement;
const slugDropdown = document.getElementById("slug-dropdown") as HTMLUListElement;
const yearInput = document.getElementById("year") as HTMLInputElement;
const urlDisplay = document.getElementById("current-url") as HTMLParagraphElement;
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

// Status
const statusDiv = document.getElementById("status") as HTMLDivElement;

// ---------------------------------------------------------------------------
//  State & Utils
// ---------------------------------------------------------------------------

let activePollInterval: number | null = null;
let draggedItem: HTMLElement | null = null;
const LOGS_EXPANDED_KEY = "logs_expanded";

// Slug autocomplete state
let cachedUniversities: UniversityOption[] = [];
let activeDropdownIndex = -1;

// Helper to disable/enable form
function setFormEnabled(enabled: boolean) {
    slugInput.disabled = !enabled;
    yearInput.disabled = !enabled;
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

function switchView(view: "input" | "monitor") {
    if (view === "input") {
        inputSection.classList.remove("hidden");
        monitorSection.classList.add("hidden");
        stopPolling();
    } else {
        inputSection.classList.add("hidden");
        monitorSection.classList.remove("hidden");
    }
}

async function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

// ---------------------------------------------------------------------------
//  Initialization
// ---------------------------------------------------------------------------

async function init() {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
        const tab = tabs[0];
        if (tab?.url) {
            urlDisplay.textContent = tab.url;

            // Auto-fill slug from URL if possible (simple heuristic)
            // e.g. https://admissions.hku.hk/... -> hku
            try {
                const urlObj = new URL(tab.url);
                const hostname = urlObj.hostname;
                const parts = hostname.split('.');
                // find part that looks like university name?
                // For now, just leave it manual or use simple regex if requested
            } catch (e) { }
        } else {
            urlDisplay.textContent = "(unable to read URL)";
        }
    });

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
//  Input & Monitor Flow (Same as before)
// ---------------------------------------------------------------------------

sendBtn.addEventListener("click", async () => {
    const slug = slugInput.value.trim();
    const year = parseInt(yearInput.value.trim(), 10);
    const url = urlDisplay.textContent ?? "";

    if (!slug || !year || !url || url.startsWith("(")) {
        showStatus("Invalid input or URL", "error");
        return;
    }

    sendBtn.disabled = true;
    sendBtn.textContent = "Starting…";

    try {
        const res = await fetch(`${API_BASE}/crawl`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url, univ_slug: slug, year, continue_depth: 0 }),
        });

        // Lock UI immediately
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

    } catch (err) {
        showStatus(String(err), "error");
    } finally {
        sendBtn.disabled = false;
        sendBtn.textContent = "Start Crawl";
    }
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

    // Merge priority list with any providers that might be missing from it
    const orderedKeys = [...config.llm_priority];
    const allProviders = Object.keys(config.providers);

    // Add missing providers to the end
    for (const p of allProviders) {
        if (!orderedKeys.includes(p)) {
            orderedKeys.push(p);
        }
    }

    // Render items
    orderedKeys.forEach(providerName => {
        const settings = config.providers[providerName];
        if (!settings) return; // Should not happen based on schema logic

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
        // Simplify label: VOLC_API_KEY -> API KEY
        label.textContent = key.replace(`${name.toUpperCase()}_`, "").replace(/_/g, " ");

        const input = document.createElement("input");
        input.type = "text";
        input.value = settings[key];
        input.dataset.key = key; // Store original key

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
            newPriority.push(name);

            const settings: Record<string, string> = {};
            const inputs = li.querySelectorAll("input");
            inputs.forEach(inp => {
                settings[inp.dataset.key!] = inp.value;
            });
            newProviders[name] = settings;
        });

        const payload: StructuredConfig = {
            database_url: dbUrlInput.value.trim(),
            llm_priority: newPriority,
            providers: newProviders
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
