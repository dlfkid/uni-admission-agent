import type { ShowStatusFn, UniversityOption } from "./types";

interface ExportFlowDeps {
    apiBase: string;
    showStatus: ShowStatusFn;
    getUniversities: () => UniversityOption[];
    sourceSlugInput: HTMLInputElement;
    sourceYearInput: HTMLInputElement;
    exportBtn: HTMLButtonElement;
    exportModal: HTMLDivElement;
    closeExportBtn: HTMLButtonElement;
    exportSlugInput: HTMLInputElement;
    exportSlugDropdown: HTMLUListElement;
    exportYearInput: HTMLInputElement;
    doExportBtn: HTMLButtonElement;
}

export function initExportFlow(deps: ExportFlowDeps): void {
    const {
        apiBase,
        showStatus,
        getUniversities,
        sourceSlugInput,
        sourceYearInput,
        exportBtn,
        exportModal,
        closeExportBtn,
        exportSlugInput,
        exportSlugDropdown,
        exportYearInput,
        doExportBtn,
    } = deps;

    let activeExportDropdownIndex = -1;

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

    function renderExportDropdown(query: string): void {
        exportSlugDropdown.innerHTML = "";
        activeExportDropdownIndex = -1;

        const universities = getUniversities();
        const filtered = query
            ? universities.filter(
                (u) =>
                    u.slug.toLowerCase().includes(query.toLowerCase()) ||
                    u.name.toLowerCase().includes(query.toLowerCase())
            )
            : universities;

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

    exportBtn.addEventListener("click", () => {
        exportSlugInput.value = sourceSlugInput.value.trim();
        exportYearInput.value = sourceYearInput.value.trim();
        exportModal.classList.remove("hidden");
        exportSlugInput.focus();
    });

    closeExportBtn.addEventListener("click", () => {
        exportModal.classList.add("hidden");
    });

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
            const res = await fetch(`${apiBase}/export`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ univ_slug: slug, year }),
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
                throw new Error(err.detail || `Export failed: ${res.status}`);
            }

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

    initExportSlugAutocomplete();
}
