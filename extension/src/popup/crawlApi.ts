import type { CrawlPayload, ShowStatusFn, TaskInfo } from "./types";

// ---------------------------------------------------------------------------
//  Pure utilities
// ---------------------------------------------------------------------------

export function shortenUrl(url: string, maxLength = 54): string {
    const trimmed = String(url || "").trim();
    if (trimmed.length <= maxLength) {
        return trimmed;
    }
    return `${trimmed.slice(0, maxLength - 3)}...`;
}

export function renderBatchSummary(args: {
    processed: number;
    total: number;
    batchIndex: number;
    batchTotal: number;
    success: number;
    failed: number;
    currentUrl?: string;
}): string {
    const safeBatchIndex = Math.min(args.batchTotal, Math.max(1, args.batchIndex));
    const base = `Processed ${args.processed}/${args.total} · Batch ${safeBatchIndex}/${args.batchTotal} · Success ${args.success} · Failed ${args.failed}`;
    if (!args.currentUrl) {
        return base;
    }
    return `${base} · URL ${shortenUrl(args.currentUrl)}`;
}

// ---------------------------------------------------------------------------
//  Task polling
// ---------------------------------------------------------------------------

export async function waitForTaskTerminal(taskId: string, apiBase: string): Promise<TaskInfo> {
    while (true) {
        const res = await fetch(`${apiBase}/tasks/${taskId}`);
        if (!res.ok) {
            throw new Error(`Task polling failed (${res.status})`);
        }
        const data: TaskInfo = await res.json();
        if (data.state === "DONE" || data.state === "FAILED") {
            return data;
        }
        await new Promise<void>((resolve) => setTimeout(resolve, 1500));
    }
}

// ---------------------------------------------------------------------------
//  Shared callbacks type
// ---------------------------------------------------------------------------

export interface CrawlApiCallbacks {
    showStatus: ShowStatusFn;
    setFormEnabled: (enabled: boolean) => void;
    getTaxonomyOptions: () => {
        enabled: boolean;
        lowThreshold: number;
        highThreshold: number;
        hintTopK: number;
        overrideEnabled: boolean;
    };
    reinit: () => Promise<void>;
}

// ---------------------------------------------------------------------------
//  submitCrawl
// ---------------------------------------------------------------------------

export interface SubmitCrawlOpts {
    url: string;
    slug: string;
    year: number;
    pageType: string;
    exportMd: boolean;
    exportPath: string;
    htmlContent?: string;
    selectedUrls?: string[];
    selectedLinkTexts?: Record<string, string>;
    browserAutomationEnabled?: boolean;
    detailPagesBatch?: CrawlPayload["detail_pages_batch"];
    batchIndex?: number;
    batchTotal?: number;
}

export async function submitCrawl(
    opts: SubmitCrawlOpts,
    apiBase: string,
    callbacks: CrawlApiCallbacks,
): Promise<string> {
    const taxonomyOptions = callbacks.getTaxonomyOptions();
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
    if (typeof opts.browserAutomationEnabled === "boolean") {
        payload.browser_automation_enabled = opts.browserAutomationEnabled;
    }
    if (opts.detailPagesBatch && opts.detailPagesBatch.length > 0) {
        payload.detail_pages_batch = opts.detailPagesBatch;
    }
    if (typeof opts.batchIndex === "number") {
        payload.batch_index = opts.batchIndex;
    }
    if (typeof opts.batchTotal === "number") {
        payload.batch_total = opts.batchTotal;
    }
    if (opts.exportMd && opts.exportPath) {
        payload.export_md = true;
        payload.export_path = opts.exportPath;
    }

    const res = await fetch(`${apiBase}/crawl`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });

    callbacks.setFormEnabled(false);

    if (res.status === 409) {
        callbacks.showStatus("A task is already running!", "error");
        await callbacks.reinit();
        throw new Error("Task conflict: another crawl is still running");
    }

    if (!res.ok) {
        throw new Error(`Server error: ${res.status}`);
    }

    const data = await res.json();
    if (!data.task_id) {
        throw new Error("Missing task_id from /crawl response");
    }
    return data.task_id as string;
}

// ---------------------------------------------------------------------------
//  submitAgentRun
// ---------------------------------------------------------------------------

export interface SubmitAgentRunOpts {
    url: string;
    slug: string;
    year: number;
    pageType: string;
}

export async function submitAgentRun(
    opts: SubmitAgentRunOpts,
    apiBase: string,
    callbacks: CrawlApiCallbacks,
): Promise<string> {
    const taxonomyOptions = callbacks.getTaxonomyOptions();
    const payload = {
        url: opts.url,
        univ_slug: opts.slug,
        year: opts.year,
        page_type_hint: opts.pageType,
        runtime: "pydanticai",
        autonomous: true,  // Extension uses autonomous mode (server-side LLM drives all decisions)
        policy_profile: {
            taxonomy_keep_threshold: taxonomyOptions.lowThreshold,
            taxonomy_auto_threshold: taxonomyOptions.highThreshold,
            auto_run_max_candidates: taxonomyOptions.hintTopK * 10,
        },
    };

    const res = await fetch(`${apiBase}/agent/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });

    callbacks.setFormEnabled(false);

    if (res.status === 409) {
        const detail = await res.json().catch(() => ({}));
        const message = detail.detail || "Agent runtime is disabled or a task is already running.";
        callbacks.showStatus(message, "error");
        await callbacks.reinit();
        throw new Error(message);
    }

    if (!res.ok) {
        throw new Error(`Server error: ${res.status}`);
    }

    const data = await res.json();
    if (!data.task_id) {
        throw new Error("Missing task_id from /agent/run response");
    }
    return data.task_id as string;
}
