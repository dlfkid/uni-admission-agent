import {
    automationConcurrencyInput,
    browserAutomationCheckbox,
    exportMdCheckbox,
    exportPathField,
    exportPathInput,
    pageTypeSelect,
    slugInput,
    taxonomyEnabledCheckbox,
    taxonomyHighThresholdInput,
    taxonomyHintTopKInput,
    taxonomyLowThresholdInput,
    taxonomyOverrideEnabledCheckbox,
} from "./dom";
import { clampAutomationConcurrency } from "./automationQueue";

// ---------------------------------------------------------------------------
//  localStorage keys
// ---------------------------------------------------------------------------

export const LOGS_EXPANDED_KEY = "logs_expanded";
export const PAGE_TYPE_KEY = "crawl_page_type";
export const EXPORT_MD_KEY = "crawl_export_md";
export const EXPORT_PATH_KEY = "crawl_export_path";
export const UNIV_SLUG_KEY = "crawl_univ_slug";
export const TAXONOMY_ENABLED_KEY = "crawl_taxonomy_enabled";
export const TAXONOMY_LOW_THRESHOLD_KEY = "crawl_taxonomy_low_threshold";
export const TAXONOMY_HIGH_THRESHOLD_KEY = "crawl_taxonomy_high_threshold";
export const TAXONOMY_HINT_TOP_K_KEY = "crawl_taxonomy_hint_top_k";
export const TAXONOMY_OVERRIDE_ENABLED_KEY = "crawl_taxonomy_override_enabled";
export const BROWSER_AUTOMATION_ENABLED_KEY = "crawl_browser_automation_enabled";
export const BROWSER_AUTOMATION_CONCURRENCY_KEY = "crawl_browser_automation_concurrency";
export const DETAIL_BATCH_SIZE = 10;

// ---------------------------------------------------------------------------
//  Restore persisted preferences into DOM on startup
// ---------------------------------------------------------------------------

export function restoreCachedPreferences(callbacks: {
    updateTaxonomySettingsVisibility: () => void;
}): void {
    const cachedPageType = localStorage.getItem(PAGE_TYPE_KEY);
    if (cachedPageType && ["auto", "index", "detail"].includes(cachedPageType)) {
        pageTypeSelect.value = cachedPageType;
    }

    const cachedExportMd = localStorage.getItem(EXPORT_MD_KEY);
    if (cachedExportMd === "true") {
        exportMdCheckbox.checked = true;
        exportPathField.style.display = "block";
    } else {
        exportMdCheckbox.checked = false;
        exportPathField.style.display = "none";
    }

    const cachedExportPath = localStorage.getItem(EXPORT_PATH_KEY);
    if (cachedExportPath) {
        exportPathInput.value = cachedExportPath;
    }

    const cachedUnivSlug = localStorage.getItem(UNIV_SLUG_KEY);
    if (cachedUnivSlug) {
        slugInput.value = cachedUnivSlug;
    }

    taxonomyEnabledCheckbox.checked = localStorage.getItem(TAXONOMY_ENABLED_KEY) !== "false";
    taxonomyLowThresholdInput.value = localStorage.getItem(TAXONOMY_LOW_THRESHOLD_KEY) || "0.80";
    taxonomyHighThresholdInput.value = localStorage.getItem(TAXONOMY_HIGH_THRESHOLD_KEY) || "0.92";
    taxonomyHintTopKInput.value = localStorage.getItem(TAXONOMY_HINT_TOP_K_KEY) || "3";
    taxonomyOverrideEnabledCheckbox.checked =
        localStorage.getItem(TAXONOMY_OVERRIDE_ENABLED_KEY) !== "false";

    browserAutomationCheckbox.checked =
        localStorage.getItem(BROWSER_AUTOMATION_ENABLED_KEY) === "true";

    const cachedConcurrencyRaw = localStorage.getItem(BROWSER_AUTOMATION_CONCURRENCY_KEY);
    const cachedConcurrencyValue = cachedConcurrencyRaw ? parseInt(cachedConcurrencyRaw, 10) : 2;
    automationConcurrencyInput.value = String(clampAutomationConcurrency(cachedConcurrencyValue));

    callbacks.updateTaxonomySettingsVisibility();
}

// ---------------------------------------------------------------------------
//  Wire up form-control event listeners that persist preferences
// ---------------------------------------------------------------------------

export function initPreferenceListeners(callbacks: {
    updateTaxonomySettingsVisibility: () => void;
}): void {
    exportMdCheckbox.addEventListener("change", () => {
        const isChecked = exportMdCheckbox.checked;
        exportPathField.style.display = isChecked ? "block" : "none";
        localStorage.setItem(EXPORT_MD_KEY, String(isChecked));
    });

    pageTypeSelect.addEventListener("change", () => {
        localStorage.setItem(PAGE_TYPE_KEY, pageTypeSelect.value);
    });

    exportPathInput.addEventListener("blur", () => {
        localStorage.setItem(EXPORT_PATH_KEY, exportPathInput.value.trim());
    });

    taxonomyEnabledCheckbox.addEventListener("change", () => {
        localStorage.setItem(TAXONOMY_ENABLED_KEY, String(taxonomyEnabledCheckbox.checked));
        callbacks.updateTaxonomySettingsVisibility();
    });

    taxonomyLowThresholdInput.addEventListener("blur", () => {
        localStorage.setItem(TAXONOMY_LOW_THRESHOLD_KEY, taxonomyLowThresholdInput.value.trim());
    });

    taxonomyHighThresholdInput.addEventListener("blur", () => {
        localStorage.setItem(TAXONOMY_HIGH_THRESHOLD_KEY, taxonomyHighThresholdInput.value.trim());
    });

    taxonomyHintTopKInput.addEventListener("blur", () => {
        localStorage.setItem(TAXONOMY_HINT_TOP_K_KEY, taxonomyHintTopKInput.value.trim());
    });

    taxonomyOverrideEnabledCheckbox.addEventListener("change", () => {
        localStorage.setItem(
            TAXONOMY_OVERRIDE_ENABLED_KEY,
            String(taxonomyOverrideEnabledCheckbox.checked),
        );
    });

    browserAutomationCheckbox.addEventListener("change", () => {
        localStorage.setItem(
            BROWSER_AUTOMATION_ENABLED_KEY,
            String(browserAutomationCheckbox.checked),
        );
    });

    automationConcurrencyInput.addEventListener("blur", () => {
        const clamped = clampAutomationConcurrency(
            parseInt(automationConcurrencyInput.value.trim(), 10),
        );
        automationConcurrencyInput.value = String(clamped);
        localStorage.setItem(BROWSER_AUTOMATION_CONCURRENCY_KEY, String(clamped));
    });
}
