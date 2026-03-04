import type { ShowStatusFn } from "./types";

interface MonitorFlowDeps {
    apiBase: string;
    showStatus: ShowStatusFn;
    switchView: (view: "input" | "link-selection" | "monitor") => void;
    setFormEnabled: (enabled: boolean) => void;
    taskIdDisplay: HTMLSpanElement;
    progressText: HTMLParagraphElement;
    progressFill: HTMLDivElement;
    tokenDisplay: HTMLSpanElement;
    logsConsole: HTMLPreElement;
    stopBtn: HTMLButtonElement;
    continueBtn: HTMLButtonElement;
}

export function initMonitorFlow(deps: MonitorFlowDeps): {
    startMonitoring: (taskId: string) => void;
    stopPolling: () => void;
} {
    const {
        apiBase,
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
    } = deps;

    let activePollInterval: number | null = null;

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

    function startMonitoring(taskId: string) {
        switchView("monitor");
        taskIdDisplay.textContent = taskId;
        taskIdDisplay.textContent = taskId;

        stopBtn.classList.remove("hidden");
        continueBtn.classList.add("hidden");
        tokenDisplay.textContent = "Tokens: 0";
        tokenDisplay.classList.remove("hidden");
        progressFill.style.backgroundColor = "var(--accent)";
        progressFill.style.width = "2%";

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
            const { state, progress, logs, error, result } = data;

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

            if (logs && Array.isArray(logs)) {
                const text = logs.join("\n");
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
    };
}
