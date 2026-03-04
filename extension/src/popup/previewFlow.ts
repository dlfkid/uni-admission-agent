import type { ProgramRecord, ShowStatusFn, UniversityOption } from "./types";

interface PreviewFlowDeps {
    apiBase: string;
    showStatus: ShowStatusFn;
    getUniversities: () => UniversityOption[];
    sourceSlugInput: HTMLInputElement;
    sourceYearInput: HTMLInputElement;
    previewBtn: HTMLButtonElement;
    previewModal: HTMLDivElement;
    closePreviewBtn: HTMLButtonElement;
    previewSlugInput: HTMLInputElement;
    previewSlugDropdown: HTMLUListElement;
    previewYearInput: HTMLInputElement;
    previewSearchBtn: HTMLButtonElement;
    previewSummary: HTMLDivElement;
    previewCountBadge: HTMLSpanElement;
    previewList: HTMLDivElement;
}

export function initPreviewFlow(deps: PreviewFlowDeps): void {
    const {
        apiBase,
        showStatus,
        getUniversities,
        sourceSlugInput,
        sourceYearInput,
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
    } = deps;

    let activePreviewDropdownIndex = -1;

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

    function renderPreviewDropdown(query: string): void {
        previewSlugDropdown.innerHTML = "";
        activePreviewDropdownIndex = -1;

        const universities = getUniversities();
        const filtered = query
            ? universities.filter(
                (u) =>
                    u.slug.toLowerCase().includes(query.toLowerCase()) ||
                    u.name.toLowerCase().includes(query.toLowerCase())
            )
            : universities;
        if (filtered.length === 0) {
            hidePreviewDropdown();
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

            if (p.requirements?.length) {
                const requirementTag = document.createElement("span");
                requirementTag.className = "program-tag mode";
                requirementTag.textContent = `${p.requirements.length} req${p.requirements.length > 1 ? "s" : ""}`;
                meta.appendChild(requirementTag);
            }

            if (p.requirement_version?.version_no != null) {
                const versionTag = document.createElement("span");
                versionTag.className = "program-tag mode";
                versionTag.textContent = `req-v${p.requirement_version.version_no}`;
                meta.appendChild(versionTag);
            }

            if (meta.children.length > 0) {
                card.appendChild(meta);
            }

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

            if (p.requirements?.length) {
                const details = document.createElement("details");
                details.className = "program-card-deadlines";
                const summary = document.createElement("summary");
                summary.textContent = `${p.requirements.length} requirement${p.requirements.length > 1 ? "s" : ""}`;
                details.appendChild(summary);

                const ul = document.createElement("ul");
                ul.className = "deadline-list";
                for (const req of p.requirements) {
                    const li = document.createElement("li");
                    li.className = "deadline-item";

                    const catEl = document.createElement("span");
                    catEl.className = "dl-round";
                    catEl.textContent = (req.category || "other").replace(/_/g, " ");
                    li.appendChild(catEl);

                    const textEl = document.createElement("span");
                    textEl.className = "dl-date";
                    const subjectLike = req.subject_name || req.exam_name;
                    if (subjectLike && req.minimum_value) {
                        textEl.textContent = `${subjectLike}: ${req.minimum_value}${req.unit ? ` ${req.unit}` : ""}`;
                    } else if (subjectLike) {
                        textEl.textContent = subjectLike;
                    } else {
                        textEl.textContent = "Requirement";
                    }
                    li.appendChild(textEl);

                    const desc = req.requirement_text || req.framework || req.applicant_scope;
                    if (desc) {
                        const descEl = document.createElement("span");
                        descEl.textContent = desc;
                        li.appendChild(descEl);
                    }

                    ul.appendChild(li);
                }
                details.appendChild(ul);
                card.appendChild(details);
            }

            if (p.source_url) {
                const a = document.createElement("a");
                a.className = "program-card-url";
                a.href = p.source_url;
                a.target = "_blank";
                a.rel = "noopener";
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
            const res = await fetch(`${apiBase}/programs?univ_slug=${encodeURIComponent(slug)}${yearParam}`);
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

    previewBtn.addEventListener("click", () => {
        previewSlugInput.value = sourceSlugInput.value.trim();
        previewYearInput.value = sourceYearInput.value.trim();
        previewModal.classList.remove("hidden");
        previewSlugInput.focus();
    });

    closePreviewBtn.addEventListener("click", () => {
        previewModal.classList.add("hidden");
    });

    previewSearchBtn.addEventListener("click", () => loadPreview());

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

    initPreviewSlugAutocomplete();
}
