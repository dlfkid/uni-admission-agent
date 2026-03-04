import type { LinkCandidate, ShowStatusFn } from "./types";

interface SubmitCrawlOptions {
    url: string;
    slug: string;
    year: number;
    pageType: string;
    exportMd: boolean;
    exportPath: string;
    htmlContent?: string;
    selectedUrls?: string[];
}

interface LinkSelectionFlowDeps {
    showStatus: ShowStatusFn;
    switchView: (view: "input" | "link-selection" | "monitor") => void;
    setFormEnabled: (enabled: boolean) => void;
    getCurrentUrl: () => string;
    getSlug: () => string;
    getYear: () => number;
    getExportMd: () => boolean;
    getExportPath: () => string;
    submitCrawl: (opts: SubmitCrawlOptions) => Promise<void>;
    linkListEl: HTMLUListElement;
    selectAllLinksCheckbox: HTMLInputElement;
    linkCountBadge: HTMLSpanElement;
    confirmLinksBtn: HTMLButtonElement;
    cancelLinksBtn: HTMLButtonElement;
}

export function initLinkSelectionFlow(deps: LinkSelectionFlowDeps): {
    showLinkSelection: (links: LinkCandidate[], totalFound: number) => void;
} {
    const {
        showStatus,
        switchView,
        setFormEnabled,
        getCurrentUrl,
        getSlug,
        getYear,
        getExportMd,
        getExportPath,
        submitCrawl,
        linkListEl,
        selectAllLinksCheckbox,
        linkCountBadge,
        confirmLinksBtn,
        cancelLinksBtn,
    } = deps;

    let candidateLinks: LinkCandidate[] = [];

    function renderLinkSelection(links: LinkCandidate[], totalFound: number) {
        void totalFound;
        candidateLinks = links;
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

        selectAllLinksCheckbox.checked = checked === checkboxes.length;
        selectAllLinksCheckbox.indeterminate = checked > 0 && checked < checkboxes.length;

        confirmLinksBtn.disabled = checked === 0;
    }

    selectAllLinksCheckbox.addEventListener("change", () => {
        const isChecked = selectAllLinksCheckbox.checked;
        linkListEl.querySelectorAll<HTMLInputElement>("input[type=checkbox]").forEach(cb => {
            cb.checked = isChecked;
            const li = cb.closest(".link-item");
            if (li) {
                li.classList.toggle("selected", isChecked);
            }
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
            await submitCrawl({
                url: getCurrentUrl(),
                slug: getSlug(),
                year: getYear(),
                pageType: "detail",
                exportMd: getExportMd(),
                exportPath: getExportPath(),
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

    return {
        showLinkSelection: renderLinkSelection,
    };
}
