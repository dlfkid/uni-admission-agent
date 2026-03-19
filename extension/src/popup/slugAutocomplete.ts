import { slugDropdown, slugInput } from "./dom";
import { UNIV_SLUG_KEY } from "./preferences";
import type { UniversityOption } from "./types";

let cachedUniversities: UniversityOption[] = [];
let activeDropdownIndex = -1;

export function getCachedUniversities(): UniversityOption[] {
    return cachedUniversities;
}

export async function loadUniversities(apiBase: string): Promise<void> {
    try {
        const res = await fetch(`${apiBase}/universities`);
        if (res.ok) {
            cachedUniversities = await res.json();
        }
    } catch (err) {
        console.warn("Failed to load universities for autocomplete:", err);
    }
}

export function initSlugAutocomplete(): void {
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
                u.name.toLowerCase().includes(query.toLowerCase()),
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
