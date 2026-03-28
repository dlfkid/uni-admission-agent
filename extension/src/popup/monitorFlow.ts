import type { ShowStatusFn } from "./types";

interface MonitorFlowDeps {
    apiBase: string;
    showStatus: ShowStatusFn;
    switchView: (view: "input" | "link-selection" | "monitor") => void;
    setFormEnabled: (enabled: boolean) => void;
    taskIdDisplay: HTMLSpanElement;
    progressText: HTMLParagraphElement;
    batchSummaryText: HTMLParagraphElement;
    progressFill: HTMLDivElement;
    tokenDisplay: HTMLSpanElement;
    logsConsole: HTMLPreElement;
    stopBtn: HTMLButtonElement;
    continueBtn: HTMLButtonElement;
}

export function initMonitorFlow(deps: MonitorFlowDeps): {
    startMonitoring: (taskId: string) => void;
    stopPolling: () => void;
    setBatchSummary: (summary: string) => void;
    clearBatchSummary: () => void;
    appendBatchLog: (line: string) => void;
    clearBatchLogs: () => void;
} {
    const {
        apiBase,
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
    } = deps;

    let activePollInterval: number | null = null;
    let activeEventSource: EventSource | null = null;
    let externalBatchLogs: string[] = [];
    // SSE-streamed event lines (separate from server-side task logs)
    let streamingLines: string[] = [];
    // Accumulates summary_delta tokens until summary_finished
    let summaryBuffer = "";
    let summaryLineIndex = -1; // index in streamingLines where summary text lives

    function resolveProgressPercent(payload: Record<string, unknown>, state: string): number {
        const raw = payload.progress_percent;
        if (typeof raw === "number" && Number.isFinite(raw)) {
            return Math.max(0, Math.min(100, raw));
        }
        if (state === "DONE" || state === "FAILED") {
            return 100;
        }
        if (state === "RUNNING") {
            return 55;
        }
        if (state === "PENDING") {
            return 5;
        }
        return 0;
    }

    // Map an SSE event object to a human-readable log line (or null to skip).
    function formatEvent(evt: Record<string, unknown>): string | null {
        const type = String(evt.type || "");
        const ts = new Date().toLocaleTimeString([], { hour12: false });
        switch (type) {
            case "agent_started":
                return `[${ts}] [Agent] Started`;
            case "llm_call_started":
                return `[${ts}] [LLM] Thinking… (iter ${evt.iteration ?? "?"})`;
            case "llm_call_finished":
                return `[${ts}] [LLM] Response received`;
            case "tool_call_started":
                return `[${ts}] [Tool] → ${evt.tool_name ?? evt.name ?? "unknown"}`;
            case "tool_call_finished":
                return `[${ts}] [Tool] ✓ ${evt.tool_name ?? evt.name ?? "unknown"}`;
            case "persist_started":
                return `[${ts}] [Persist] Saving programs…`;
            case "persist_finished":
                return `[${ts}] [Persist] Done`;
            case "summary_started":
                return `[${ts}] [Summary] `;
            case "summary_finished":
                return null; // handled inline via summary_delta
            case "agent_done":
                return `[${ts}] [Agent] Completed`;
            case "agent_failed":
                return `[${ts}] [Agent] Failed: ${evt.error ?? ""}`;
            case "runtime_fallback":
                return `[${ts}] [Runtime] Falling back to legacy`;
            default:
                return null;
        }
    }

    function renderLogsConsole() {
        const allLines = [...externalBatchLogs, ...streamingLines];
        const text = allLines.join("\n");
        const isScrolledToBottom =
            logsConsole.scrollHeight - logsConsole.scrollTop <= logsConsole.clientHeight + 50;
        if (logsConsole.textContent !== text) {
            logsConsole.textContent = text;
            if (isScrolledToBottom) {
                logsConsole.scrollTop = logsConsole.scrollHeight;
            }
        }
    }

    function stopEventStream() {
        if (activeEventSource) {
            activeEventSource.close();
            activeEventSource = null;
        }
    }

    function startEventStream(taskId: string) {
        stopEventStream();
        summaryBuffer = "";
        summaryLineIndex = -1;

        const url = `${apiBase}/tasks/${taskId}/events`;
        const es = new EventSource(url);
        activeEventSource = es;

        es.onmessage = (msgEvt) => {
            let parsed: Record<string, unknown>;
            try {
                parsed = JSON.parse(String(msgEvt.data)) as Record<string, unknown>;
            } catch {
                return;
            }

            const type = String(parsed.type || "");

            if (type === "summary_delta") {
                const delta = String(parsed.delta ?? "");
                summaryBuffer += delta;
                if (summaryLineIndex < 0) {
                    // First delta — create the summary line
                    const ts = new Date().toLocaleTimeString([], { hour12: false });
                    streamingLines.push(`[${ts}] [Summary] ${summaryBuffer}`);
                    summaryLineIndex = streamingLines.length - 1;
                } else {
                    // Update existing summary line in place
                    streamingLines[summaryLineIndex] =
                        streamingLines[summaryLineIndex].replace(
                            / \[Summary\] .*$/,
                            ` [Summary] ${summaryBuffer}`,
                        );
                }
                renderLogsConsole();
                return;
            }

            const line = formatEvent(parsed);
            if (line !== null) {
                streamingLines.push(line);
                // Keep buffer bounded
                if (streamingLines.length > 200) {
                    streamingLines = streamingLines.slice(-200);
                    if (summaryLineIndex >= 0) {
                        summaryLineIndex = Math.max(0, summaryLineIndex - (streamingLines.length - 200));
                    }
                }
                renderLogsConsole();
            }

            // Close on terminal events
            if (type === "agent_done" || type === "agent_failed") {
                es.close();
                activeEventSource = null;
            }
        };

        es.onerror = () => {
            // EventSource auto-retries on network errors; close only if task is terminal
            void (async () => {
                try {
                    const res = await fetch(`${apiBase}/tasks/${taskId}`);
                    if (res.ok) {
                        const data = await res.json() as { state?: string };
                        if (data.state === "DONE" || data.state === "FAILED") {
                            es.close();
                            activeEventSource = null;
                        }
                    }
                } catch {
                    // ignore
                }
            })();
        };
    }

    function startMonitoring(taskId: string) {
        switchView("monitor");
        taskIdDisplay.textContent = taskId;
        if (batchSummaryText.textContent?.trim()) {
            batchSummaryText.classList.remove("hidden");
        } else {
            batchSummaryText.classList.add("hidden");
        }

        stopBtn.classList.remove("hidden");
        continueBtn.classList.add("hidden");
        tokenDisplay.textContent = "Tokens: 0";
        tokenDisplay.classList.remove("hidden");
        progressFill.style.backgroundColor = "var(--accent)";
        progressFill.style.width = "2%";

        // Reset streaming state
        streamingLines = [];
        summaryBuffer = "";
        summaryLineIndex = -1;

        // Start SSE stream for real-time events
        startEventStream(taskId);

        // Keep slow poll for progress bar / token count / terminal state
        void pollTask(taskId);
        if (activePollInterval) {
            clearInterval(activePollInterval);
        }
        activePollInterval = window.setInterval(() => {
            void pollTask(taskId);
        }, 2000);
    }

    function stopPolling() {
        if (activePollInterval) {
            clearInterval(activePollInterval);
            activePollInterval = null;
        }
        stopEventStream();
    }

    function setBatchSummary(summary: string) {
        batchSummaryText.textContent = summary;
        batchSummaryText.classList.remove("hidden");
    }

    function clearBatchSummary() {
        batchSummaryText.textContent = "";
        batchSummaryText.classList.add("hidden");
    }

    function appendBatchLog(line: string) {
        const timestamp = new Date().toLocaleTimeString([], { hour12: false });
        externalBatchLogs.push(`[${timestamp}] [Queue] ${line}`);
        externalBatchLogs = externalBatchLogs.slice(-120);
        if (activePollInterval === null) {
            logsConsole.textContent = externalBatchLogs.join("\n");
            logsConsole.scrollTop = logsConsole.scrollHeight;
        }
    }

    function clearBatchLogs() {
        externalBatchLogs = [];
        logsConsole.textContent = "";
    }

    async function pollTask(taskId: string) {
        try {
            const res = await fetch(`${apiBase}/tasks/${taskId}`);
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
            const { state, progress, error, result } = data;

            progressText.textContent = `${state}: ${progress || "..."}`;
            const progressPercent = resolveProgressPercent(data as Record<string, unknown>, state);
            progressFill.style.width = `${progressPercent}%`;
            progressFill.style.backgroundColor = state === "FAILED" ? "var(--error)" : "var(--accent)";

            if (data.tokens_used !== undefined) {
                tokenDisplay.textContent = `Tokens: ${data.tokens_used.toLocaleString()}`;
                tokenDisplay.classList.remove("hidden");
            }

            if (state === "RUNNING" || state === "PENDING") {
                stopBtn.classList.remove("hidden");
                continueBtn.classList.add("hidden");
                setFormEnabled(false);
            } else {
                stopBtn.classList.add("hidden");
                continueBtn.classList.remove("hidden");
            }

            // Server-side task logs (fallback for non-agent tasks or when SSE has no events)
            if (data.logs && Array.isArray(data.logs) && streamingLines.length === 0) {
                const mergedLogs = [...externalBatchLogs, ...data.logs.map((item: unknown) => String(item))];
                const text = mergedLogs.join("\n");
                const isScrolledToBottom =
                    logsConsole.scrollHeight - logsConsole.scrollTop <= logsConsole.clientHeight + 50;
                if (logsConsole.textContent !== text) {
                    logsConsole.textContent = text;
                    if (isScrolledToBottom) {
                        logsConsole.scrollTop = logsConsole.scrollHeight;
                    }
                }
            }

            if (state === "DONE") {
                stopPolling();
                showStatus(`Completed! Imported: ${result?.imported_count ?? 0}`, "success");
            } else if (state === "FAILED") {
                stopPolling();
                showStatus(`Failed: ${error}`, "error");
            }
        } catch (err) {
            console.error("Poll error", err);
        }
    }

    stopBtn.addEventListener("click", async () => {
        const taskId = taskIdDisplay.textContent;
        if (!taskId) {
            return;
        }
        if (confirm("Stop current task?")) {
            try {
                await fetch(`${apiBase}/tasks/${taskId}/cancel`, { method: "POST" });
                showStatus("Cancellation requested...", "info");
            } catch (err) {
                showStatus("Failed to cancel", "error");
            }
        }
    });

    return {
        startMonitoring,
        stopPolling,
        setBatchSummary,
        clearBatchSummary,
        appendBatchLog,
        clearBatchLogs,
    };
}
