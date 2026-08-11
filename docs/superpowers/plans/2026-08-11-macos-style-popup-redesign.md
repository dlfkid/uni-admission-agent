# macOS-Style Popup Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `frontend/src/shared/popup.css` and `frontend/src/shared/popup.html` to give the Chrome extension Side Panel a macOS-Settings-style visual system (grouped lists, systemBlue accent, system font stack, CSS-spring motion) with light/dark mode driven purely by `prefers-color-scheme`.

**Architecture:** Two-file, presentation-only change. `popup.ts`, `dom.ts`, and every `popup/*Flow.ts` module keep their existing element ids and the class names they generate at runtime (`program-card*`, `link-item*`, `llm-item`, `deadline-item`, `slug-name`/`slug-meta`, state classes `hidden`/`dragging`/`active`/`selected`/`connected`/`unavailable`/`collapsed`) — those are treated as a fixed contract the CSS must style, not something this plan renames.

**Tech Stack:** Vanilla TypeScript + Vite + hand-written CSS (no framework, no new npm dependency).

## Global Constraints

- Only `frontend/src/shared/popup.html` and `frontend/src/shared/popup.css` may change. Do not touch `popup.ts`, `dom.ts`, or any file under `frontend/src/shared/popup/`.
- Every `id` currently read via `document.getElementById` in `dom.ts` must still exist, unchanged, in the new `popup.html`.
- Every class name referenced via `classList`/`className` in the `.ts` files must still exist, unchanged, as a CSS-styleable class.
- No new npm dependency. No manual light/dark toggle — `@media (prefers-color-scheme: dark)` only.
- `@media (prefers-reduced-motion: reduce)` must collapse all transitions/animations to near-instant.
- Colors: light `--accent:#007aff` / dark `--accent:#0a84ff` (systemBlue); font stack `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif`.
- `npm run build --prefix frontend` (`tsc --noEmit && vite build`) must pass after every task.

---

## Pre-flight: exact JS/DOM coupling this plan must respect

Found by grepping every `.ts` file under `frontend/src` for `\.style\.`, `classList`, and `className` before writing this plan (see design doc §4.1 for the class-name inventory). Three elements have their `display` set directly via **inline style** by TypeScript, not via `classList`:

| Element (`id`) | Set by | Values used |
|---|---|---|
| `#taxonomy-settings` | `popup.ts:167` | `"block"` / `"none"` |
| `#auto-paginate-field` | `crawlFlow.ts:144` | `"block"` / `"none"` |
| `#export-path-field` | `preferences.ts:50,53,92` | `"block"` / `"none"` |

**Why this matters:** inline styles beat any non-`!important` stylesheet rule. If `.settings-row` (the new grouped-list row class) were styled `display: flex` directly, then JS setting `style.display = "block"` on `#auto-paginate-field` / `#export-path-field` would flatten their layout (label and control would stack instead of sitting side by side) the next time crawlFlow/preferences re-shows them. The fix used throughout this plan: `.settings-row` itself stays plain `display: block` (the default for a `<div>`, which is exactly what the inline `"block"`/`"none"` toggling expects), and a **nested** `.row-inner` element carries `display: flex` for the actual label/control alignment. `#taxonomy-settings` doesn't need this trick — it becomes a `.settings-group` directly, which was never `flex` to begin with.

Also found: `#export-path-field` ships with `style="display: none;"` **in the static HTML** today (not just toggled later) — the new markup must keep that exact inline attribute, not swap it for a `hidden` class (this codebase's `.hidden` utility is `!important`, which would permanently defeat the later inline `"block"` and make the field impossible to reveal).

Also found: `previewFlow.ts:591` sets `style="color:var(--error)"` inline in a template string — confirms the new stylesheet must keep the custom property named exactly `--error` (already the design's naming, no conflict). `monitorFlow.ts`/`crawlFlow.ts` set `progressFill.style.width` and `.style.backgroundColor = "var(--accent)"/"var(--error)"` inline — the new CSS must not declare `background` on `#progress-fill` with `!important`, and must define `--accent`/`--error` (it does).

Also found: `.field`, `.checkbox-field`, `.preset-bar`, `.preset-label`, `.preset-buttons`, `.preset-btn`, `.password-wrapper`, `.eye-btn`, `input[type="range"]` thumb styles, `.slider-value`, `.field-row`, `.field-half`, `.toggle-row`, `.toggle-item`, `.toggle-label`, the old `.switch`/`.switch-slider` pair, `.test-connection-row`, `.test-conn-btn`, `.conn-status` are **dead CSS** — grepped every `.ts` file for `className`, `classList`, and literal `class="` in template strings; none of these names are ever produced by any code path, and none appear in the current `popup.html` either. Safe to delete; this also frees up the name `.switch` for the new real toggle-switch component (built fresh, using `.track` as the inner element instead of the dead code's `.switch-slider`, so there's no accidental partial match).

Also found: `.status`, `.status.success`, `.status.error`, `.status.info` have **zero CSS rules today** even though `popup.ts:140` actively sets `statusDiv.className = \`status ${type}\`` — a pre-existing unstyled-banner gap. This plan fixes it as part of the badge/status styling (small, in-scope opportunistic fix — the element and the JS that drives it already exist and are exercised on every status message).

---

### Task 1: Rewrite `popup.css` with the macOS design system

**Files:**
- Modify: `frontend/src/shared/popup.css` (full-file rewrite)

**Interfaces:**
- Consumes: nothing (pure CSS).
- Produces: every selector listed in the Pre-flight section above, plus the ones enumerated in the file below. Task 2 (`popup.html`) is written to match these class names exactly.

- [ ] **Step 1: Replace the entire contents of `popup.css`**

```css
/* UniAdmission Agent — Chrome Extension Popup
 * macOS Settings-style visual system. Light/dark via prefers-color-scheme
 * only — no manual toggle, no JS branching on theme.
 */

:root {
  color-scheme: light dark;

  --bg: #eeeef1;
  --group-bg: #ffffff;
  --text: #1d1d1f;
  --text-muted: #6e6e73;
  --divider: rgba(0, 0, 0, 0.08);
  --accent: #007aff;
  --accent-soft: rgba(0, 122, 255, 0.12);
  --control-bg: #e4e4e8;
  --success: #34c759;
  --error: #ff3b30;

  --radius: 10px;
  --radius-sm: 7px;
  --spring: cubic-bezier(0.34, 1.56, 0.64, 1);
  --ease: cubic-bezier(0.2, 0.9, 0.3, 1);
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1e1e1e;
    --group-bg: #2c2c2e;
    --text: #f5f5f7;
    --text-muted: #98989d;
    --divider: rgba(255, 255, 255, 0.08);
    --accent: #0a84ff;
    --accent-soft: rgba(10, 132, 255, 0.18);
    --control-bg: #3a3a3c;
    --success: #32d74b;
    --error: #ff453a;
  }
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}

/* ---------------------------------------------------------------------
 * Reset & base
 * --------------------------------------------------------------------- */

* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

html, body {
  height: 100%;
  overflow: hidden;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
  background: var(--bg);
  color: var(--text);
  min-width: 360px;
}

.hidden { display: none !important; }

.container {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  height: 100%;
  overflow-y: auto;
}

/* ---------------------------------------------------------------------
 * Header
 * --------------------------------------------------------------------- */

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--divider);
}

.app-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.app-title .glyph {
  width: 26px;
  height: 26px;
  border-radius: 7px;
  background: var(--accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}

.header-actions { display: flex; gap: 4px; align-items: center; }

.icon-btn {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: background 160ms var(--spring), transform 120ms var(--spring);
}
.icon-btn:hover { background: var(--control-bg); }
.icon-btn:active { transform: scale(0.88); }
.icon-btn.small {
  width: auto;
  height: auto;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--control-bg);
}
.icon-btn.small:hover { filter: brightness(0.95); }

/* ---------------------------------------------------------------------
 * Grouped-list components (macOS Settings pattern)
 * --------------------------------------------------------------------- */

.group-block { display: flex; flex-direction: column; gap: 6px; }

.group-title {
  margin: 0 2px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text-muted);
}

.settings-group {
  background: var(--group-bg);
  border-radius: var(--radius);
  box-shadow: 0 0 0 1px var(--divider);
  overflow: hidden;
}

/* IMPORTANT: stays plain block (the <div> default) — never `display: flex`
 * here. #auto-paginate-field and #export-path-field have this class AND
 * have their `display` toggled directly via inline style by crawlFlow.ts /
 * preferences.ts (`"block"` / `"none"`). If this rule set `display: flex`,
 * the inline `"block"` would flatten the row back to block layout every
 * time JS re-shows it. The actual flex alignment lives one level down, on
 * .row-inner, which JS never touches. */
.settings-row {
  border-bottom: 1px solid var(--divider);
  padding: 0 12px;
}
.settings-row:last-child { border-bottom: none; }

.row-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  min-height: 40px;
  padding: 9px 0;
}
.settings-row.stacked .row-inner {
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  padding: 10px 0;
}

.row-label {
  font-size: 13px;
  font-weight: 400;
  color: var(--text);
  flex-shrink: 0;
}
.row-label .hint {
  display: block;
  font-size: 11px;
  font-weight: 400;
  color: var(--text-muted);
  margin-top: 1px;
}

.row-control {
  flex: 1;
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.settings-row.stacked .row-control { justify-content: stretch; }

/* Simple "label above control" field, used outside the grouped-list
 * pattern: the Preview modal's search toolbar and the Preview-edit
 * modal's grid/JSON fields. */
.preview-field, .stacked-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preview-field label, .stacked-field label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--text-muted);
}

/* ---------------------------------------------------------------------
 * Form controls
 * --------------------------------------------------------------------- */

input[type="text"],
input[type="number"],
input[type="password"],
textarea,
select {
  font-family: inherit;
  font-size: 13px;
  color: var(--text);
  background: var(--control-bg);
  border: none;
  border-radius: var(--radius-sm);
  padding: 7px 10px;
  outline: none;
  width: 100%;
  transition: box-shadow 160ms ease;
}
input:focus, textarea:focus, select:focus {
  box-shadow: 0 0 0 3px var(--accent-soft);
}
input:disabled, select:disabled, textarea:disabled, button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
input[type="checkbox"] { accent-color: var(--accent); }

textarea {
  resize: vertical;
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
}

.inline-input { width: 140px; flex-shrink: 0; }

.url-display {
  font-size: 12px;
  color: var(--text-muted);
  background: var(--control-bg);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  line-height: 1.4;
  word-break: break-all;
  max-height: 60px;
  overflow-y: auto;
}

/* Toggle switch — wraps a real <input type="checkbox"> so the underlying
 * `.checked` property (read/written by popup.ts et al.) is untouched. */
.switch { position: relative; display: inline-block; width: 40px; height: 24px; flex-shrink: 0; }
.switch input { position: absolute; opacity: 0; width: 0; height: 0; }
.switch .track {
  position: absolute;
  inset: 0;
  background: var(--control-bg);
  border-radius: 12px;
  cursor: pointer;
  transition: background 200ms var(--spring);
}
.switch .track::before {
  content: "";
  position: absolute;
  width: 20px;
  height: 20px;
  left: 2px;
  top: 2px;
  background: #fff;
  border-radius: 50%;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);
  transition: transform 260ms var(--spring);
}
.switch input:checked + .track { background: var(--success); }
.switch input:checked + .track::before { transform: translateX(16px); }
.switch input:focus-visible + .track { box-shadow: 0 0 0 3px var(--accent-soft); }

/* ---------------------------------------------------------------------
 * Buttons
 * --------------------------------------------------------------------- */

button {
  border: none;
  border-radius: var(--radius);
  cursor: pointer;
  font-family: inherit;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: transform 140ms var(--spring), filter 160ms ease, background 160ms ease, opacity 160ms ease;
}
button:active { transform: scale(0.97); }

.primary-btn {
  background: var(--accent);
  color: #fff;
  padding: 12px;
  font-size: 14px;
  font-weight: 600;
  width: 100%;
}
.primary-btn:hover { filter: brightness(1.08); }

.secondary-btn {
  background: var(--control-bg);
  color: var(--text);
  padding: 12px 16px;
  font-size: 13px;
  font-weight: 600;
}
.secondary-btn:hover { filter: brightness(0.96); }

.danger-btn {
  background: rgba(255, 59, 48, 0.12);
  color: var(--error);
  padding: 10px;
  font-size: 13px;
  font-weight: 600;
  border: 1px solid rgba(255, 59, 48, 0.25);
  width: 100%;
}
.danger-btn:hover { background: rgba(255, 59, 48, 0.2); }

.close-btn {
  background: transparent;
  color: var(--text-muted);
  font-size: 22px;
  padding: 0 4px;
  line-height: 1;
}
.close-btn:hover { color: var(--text); }

/* ---------------------------------------------------------------------
 * Status banner, badges
 * --------------------------------------------------------------------- */

.status {
  padding: 10px 12px;
  border-radius: var(--radius-sm);
  font-size: 12px;
  font-weight: 500;
  line-height: 1.4;
}
.status.success { background: rgba(52, 199, 89, 0.12); color: var(--success); }
.status.error { background: rgba(255, 59, 48, 0.12); color: var(--error); }
.status.info { background: var(--accent-soft); color: var(--accent); }

.task-badge {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-muted);
  background: var(--control-bg);
  padding: 2px 6px;
  border-radius: 6px;
}

.token-badge {
  font-size: 10px;
  font-family: "SF Mono", Menlo, monospace;
  background: rgba(45, 212, 191, 0.12);
  color: #2dd4bf;
  padding: 2px 6px;
  border-radius: 6px;
  border: 1px solid rgba(45, 212, 191, 0.25);
}

.link-count-badge, .preview-count-badge {
  font-size: 11px;
  font-weight: 600;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 2px 8px;
  border-radius: 10px;
}

.source-status { font-size: 11px; color: var(--text-muted); white-space: nowrap; }
.source-status.connected { color: var(--success); }
.source-status.unavailable { color: #ff9500; }

.browser-source-row { display: flex; align-items: center; gap: 8px; }
.browser-source-row select { flex: 1; min-width: 0; }

/* ---------------------------------------------------------------------
 * Progress bar / pulse
 * --------------------------------------------------------------------- */

.status-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  gap: 8px;
}
#progress-text {
  font-size: 12px;
  color: var(--accent);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
}
.batch-summary-text {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.3;
  margin-bottom: 8px;
}
.progress-bar {
  height: 6px;
  background: var(--control-bg);
  border-radius: 3px;
  overflow: hidden;
}
/* width and background-color are set inline by monitorFlow.ts/crawlFlow.ts
 * on every poll tick — this only supplies the shape and a sane fallback
 * color before the first inline write lands. Do not add !important here. */
#progress-fill {
  height: 100%;
  width: 0%;
  border-radius: 3px;
  background: var(--accent);
  transition: width 300ms ease;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 0.85; }
  50% { opacity: 1; }
}

.monitor-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.monitor-header h3 { font-size: 14px; font-weight: 600; }

/* ---------------------------------------------------------------------
 * Logs / console
 * --------------------------------------------------------------------- */

.logs-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--divider);
}
.logs-header h4 {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
}
.logs-container { display: flex; flex-direction: column; }
.preflight-log-section { display: flex; flex-direction: column; }

#logs-console, #preflight-log-console {
  background: var(--group-bg);
  border-radius: var(--radius-sm);
  padding: 10px;
  box-shadow: 0 0 0 1px var(--divider);
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 11px;
  color: var(--text-muted);
  white-space: pre-wrap;
  word-wrap: break-word;
  line-height: 1.4;
  overflow-y: auto;
  user-select: text;
  cursor: text;
  transition: all 300ms ease;
}
#logs-console { height: 180px; }
#preflight-log-console { max-height: 110px; }
#logs-console.collapsed {
  max-height: 0 !important;
  padding: 0 !important;
  box-shadow: none !important;
  opacity: 0;
}

/* ---------------------------------------------------------------------
 * Modal chrome
 * --------------------------------------------------------------------- */

.modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  backdrop-filter: blur(6px);
  opacity: 1;
  transition: opacity 200ms ease;
}
.modal.hidden { opacity: 0; pointer-events: none; }

.modal-content {
  background: var(--bg);
  width: 95%;
  height: 95%;
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.35);
  overflow: hidden;
}
.modal-content.modal-compact { height: auto; max-height: 80%; }

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--divider);
}
.modal-header h2 { font-size: 16px; font-weight: 600; }

.config-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.modal-actions {
  padding: 14px 20px;
  border-top: 1px solid var(--divider);
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

/* ---------------------------------------------------------------------
 * LLM priority list (Config modal)
 * --------------------------------------------------------------------- */

#llm-list { list-style: none; display: flex; flex-direction: column; gap: 8px; }

.llm-item {
  background: var(--group-bg);
  border-radius: var(--radius);
  box-shadow: 0 0 0 1px var(--divider);
  transition: box-shadow 160ms ease;
}
.llm-item.dragging { opacity: 0.5; box-shadow: 0 0 0 2px var(--accent); }

.llm-header {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 10px 12px;
  cursor: grab;
}
.llm-header:active { cursor: grabbing; }

.handle { color: var(--text-muted); font-size: 16px; line-height: 1; }
.name { flex: 1; font-weight: 600; font-size: 13px; text-transform: capitalize; }

.toggle-btn {
  background: transparent;
  color: var(--text-muted);
  padding: 4px;
  font-size: 10px;
  border-radius: 6px;
  min-width: 24px;
  height: 24px;
}
.toggle-btn:hover { background: var(--control-bg); }

.llm-settings {
  padding: 10px 12px;
  border-top: 1px solid var(--divider);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.llm-settings.hidden { display: none; }

.setting-row { display: flex; flex-direction: column; gap: 4px; }
.setting-row label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
}

/* ---------------------------------------------------------------------
 * Autocomplete / slug dropdown
 * --------------------------------------------------------------------- */

.autocomplete-wrapper { position: relative; }

.slug-dropdown {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  z-index: 50;
  list-style: none;
  padding: 4px;
  background: var(--group-bg);
  border-radius: var(--radius);
  box-shadow: 0 0 0 1px var(--divider), 0 10px 24px rgba(0, 0, 0, 0.18);
  max-height: 180px;
  overflow-y: auto;
}
.slug-dropdown.hidden { display: none; }

.slug-dropdown li {
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  transition: background 120ms ease;
}
.slug-dropdown li:hover, .slug-dropdown li.active { background: var(--accent-soft); }
.slug-dropdown li .slug-name { font-weight: 600; color: var(--text); }
.slug-dropdown li .slug-meta { font-size: 10px; color: var(--text-muted); margin-left: 8px; white-space: nowrap; }

/* ---------------------------------------------------------------------
 * Link selection screen
 * --------------------------------------------------------------------- */

.link-selection-header h3 { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
.link-selection-subtext { font-size: 12px; color: var(--text-muted); margin-bottom: 10px; line-height: 1.4; }

.link-actions-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--divider);
}

.select-all-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  text-transform: none;
  letter-spacing: normal;
  cursor: pointer;
}
.select-all-label input[type="checkbox"] { width: auto; cursor: pointer; }

.link-automation-settings {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 10px;
  padding: 10px;
  background: var(--group-bg);
  border-radius: var(--radius);
  box-shadow: 0 0 0 1px var(--divider);
}

.compact-field { display: flex; flex-direction: column; gap: 4px; }
.compact-field label {
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-muted);
}
.compact-field input { max-width: 120px; }

.link-list {
  list-style: none;
  max-height: 400px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.link-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px;
  background: var(--group-bg);
  border-radius: var(--radius);
  box-shadow: 0 0 0 1px var(--divider);
  cursor: pointer;
  transition: box-shadow 140ms ease, background 140ms ease;
}
.link-item:hover { box-shadow: 0 0 0 1px var(--accent); }
.link-item.selected { box-shadow: 0 0 0 1.5px var(--accent); background: var(--accent-soft); }
.link-item input[type="checkbox"] { width: auto; margin-top: 2px; cursor: pointer; flex-shrink: 0; }

.link-item-content { flex: 1; min-width: 0; }
.link-item-text { font-size: 13px; font-weight: 500; color: var(--text); line-height: 1.3; word-break: break-word; }
.link-item-url { font-size: 11px; color: var(--text-muted); word-break: break-all; line-height: 1.3; margin-top: 2px; }

.link-actions-bottom { display: flex; gap: 8px; }
.link-actions-bottom .primary-btn { flex: 1; }

/* ---------------------------------------------------------------------
 * Preview modal
 * --------------------------------------------------------------------- */

.preview-filters { padding: 12px 16px; border-bottom: 1px solid var(--divider); flex-shrink: 0; }
.preview-filter-row { display: flex; gap: 10px; align-items: flex-end; }
.preview-field { flex: 1; min-width: 150px; }
.preview-search-btn { padding: 10px 16px !important; white-space: nowrap; flex-shrink: 0; width: auto !important; min-width: 80px; }

.preview-summary {
  display: flex;
  align-items: center;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--divider);
}

.preview-list { flex: 1; overflow-y: auto; padding: 12px 16px; display: flex; flex-direction: column; gap: 8px; }
.preview-empty { text-align: center; color: var(--text-muted); padding: 40px 20px; font-size: 13px; }

/* ---------------------------------------------------------------------
 * Preview-edit modal
 * --------------------------------------------------------------------- */

.program-edit-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
#preview-edit-modal .modal-content { max-width: 900px; width: 92%; }
#preview-edit-modal textarea { min-height: 88px; }

/* ---------------------------------------------------------------------
 * Program card (dynamically generated by previewFlow.ts)
 * --------------------------------------------------------------------- */

.program-card {
  background: var(--group-bg);
  border-radius: var(--radius);
  box-shadow: 0 0 0 1px var(--divider);
  padding: 12px 14px;
  transition: box-shadow 140ms ease;
}
.program-card:hover { box-shadow: 0 0 0 1px var(--accent); }

.program-card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; margin-bottom: 6px; }
.program-card-name { font-size: 13px; font-weight: 600; color: var(--text); line-height: 1.35; word-break: break-word; flex: 1; }
.program-card-id {
  font-size: 10px;
  color: var(--text-muted);
  background: var(--control-bg);
  padding: 2px 6px;
  border-radius: 6px;
  white-space: nowrap;
  flex-shrink: 0;
}

.program-card-meta { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.program-card-actions { display: flex; gap: 8px; margin-bottom: 6px; }

.program-card-action-btn {
  background: var(--control-bg);
  color: var(--text);
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
  padding: 4px 10px;
}
.program-card-action-btn:hover { filter: brightness(0.95); }
.program-card-action-btn.danger { color: var(--error); }
.program-card-action-btn.danger:hover { background: rgba(255, 59, 48, 0.12); }

.program-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--control-bg);
  color: var(--text-muted);
  white-space: nowrap;
}
.program-tag.faculty { background: var(--accent-soft); color: var(--accent); }
.program-tag.tuition { background: rgba(52, 199, 89, 0.12); color: var(--success); }
.program-tag.mode { background: rgba(255, 149, 0, 0.12); color: #ff9500; }

.program-card-deadlines { margin-top: 6px; padding-top: 6px; border-top: 1px solid var(--divider); }
.program-card-deadlines summary { font-size: 11px; color: var(--text-muted); cursor: pointer; }
.program-card-deadlines summary:hover { color: var(--text); }

.deadline-list { list-style: none; padding-top: 4px; display: flex; flex-direction: column; gap: 3px; }
.deadline-item { font-size: 11px; color: var(--text-muted); display: flex; gap: 6px; }
.deadline-item .dl-round { color: var(--accent); font-weight: 600; flex-shrink: 0; }
.deadline-item .dl-date { color: var(--text); }

.program-card-url {
  display: block;
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-muted);
  text-decoration: none;
  word-break: break-all;
  opacity: 0.75;
  transition: opacity 140ms ease;
}
.program-card-url:hover { opacity: 1; color: var(--accent); }

/* ---------------------------------------------------------------------
 * Platform-specific visibility (unchanged from prior version).
 *
 * The same Vite bundle powers both the Chrome extension popup and the
 * standalone web UI served at /ui/. In web mode, elements that depend
 * on extension-only APIs (chrome.tabs, chrome.scripting, background-tab
 * automation) are hidden via this rule. Body class is set by
 * applyPlatformBodyClass() in platform.ts during init.
 * --------------------------------------------------------------------- */

body.platform-web .extension-only { display: none !important; }
```

- [ ] **Step 2: Verify the build still passes (CSS can't break TS compilation, but confirms nothing upstream broke)**

Run: `npm run build --prefix frontend`
Expected: exits 0, `frontend/dist/assets/popup.css` (or equivalent hashed filename) is produced.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/shared/popup.css
git commit -m "style: rewrite popup.css with macOS-style design system"
```

---

### Task 2: Rewrite `popup.html` into the grouped-list structure

**Files:**
- Modify: `frontend/src/shared/popup.html` (full-file rewrite)

**Interfaces:**
- Consumes: every class name defined in Task 1 (`.settings-group`, `.settings-row`, `.row-inner`, `.row-label`, `.row-control`, `.switch`/`.track`, `.group-block`, `.group-title`, `.preview-field`/`.stacked-field`, `.app-header`/`.app-title`/`.glyph`).
- Produces: the same set of element `id`s `dom.ts` already depends on (verified in Task 3) — no new ids, no renamed ids, no removed ids.

- [ ] **Step 1: Replace the entire contents of `popup.html`**

```html
<!DOCTYPE html>
<html lang="en">

<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>UniAdmission Agent</title>
    <link rel="stylesheet" href="./popup.css" />
</head>

<body>
    <div class="container">
        <header class="app-header">
            <div class="app-title"><span class="glyph">🎓</span> UniAdmission</div>
            <div class="header-actions">
                <button id="preview-btn" class="icon-btn" title="Preview Database">👁</button>
                <button id="export-btn" class="icon-btn" title="Export to Excel">📥</button>
                <button id="config-btn" class="icon-btn" title="Configure .env">⚙️</button>
            </div>
        </header>

        <!-- INPUT FORM MODE -->
        <div id="input-section">
            <div class="group-block">
                <div class="group-title">University</div>
                <div class="settings-group">
                    <div class="settings-row">
                        <div class="row-inner">
                            <label class="row-label" for="slug">Slug</label>
                            <div class="row-control">
                                <div class="autocomplete-wrapper">
                                    <input id="slug" class="inline-input" type="text" placeholder="e.g. hku" autocomplete="off" />
                                    <ul id="slug-dropdown" class="slug-dropdown hidden"></ul>
                                </div>
                            </div>
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="row-inner">
                            <label class="row-label" for="year">Academic Year</label>
                            <div class="row-control">
                                <input id="year" class="inline-input" type="number" placeholder="e.g. 2026" value="2026" />
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="group-block">
                <div class="group-title">Page</div>
                <div class="settings-group">
                    <div class="settings-row stacked">
                        <div class="row-inner">
                            <span class="row-label">Current URL</span>
                            <p id="current-url" class="url-display">Loading…</p>
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="row-inner">
                            <label class="row-label" for="page-type">Page Type</label>
                            <div class="row-control">
                                <select id="page-type">
                                    <option value="auto">Auto Detect</option>
                                    <option value="index">Index Page (Program List)</option>
                                    <option value="detail">Detail Page (Single Program)</option>
                                </select>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="group-block">
                <div class="group-title">Options</div>
                <div class="settings-group">
                    <div class="settings-row" id="auto-paginate-field">
                        <div class="row-inner">
                            <label class="row-label" for="auto-paginate">Auto-paginate<span class="hint">Collect all result pages</span></label>
                            <label class="switch"><input id="auto-paginate" type="checkbox" /><span class="track"></span></label>
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="row-inner">
                            <label class="row-label" for="export-md">Export Markdown Files</label>
                            <label class="switch"><input id="export-md" type="checkbox" /><span class="track"></span></label>
                        </div>
                    </div>
                    <div class="settings-row stacked" id="export-path-field" style="display: none;">
                        <div class="row-inner">
                            <label class="row-label" for="export-path">Export Path</label>
                            <input id="export-path" type="text" placeholder="e.g. /Users/username/crawl-output" />
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="row-inner">
                            <label class="row-label" for="browser-provider">Browser Source</label>
                            <div class="row-control browser-source-row">
                                <select id="browser-provider">
                                    <option value="server">🌐 Server (built-in fetcher)</option>
                                    <option value="client">🛡️ Local Client (best anti-detection)</option>
                                </select>
                                <span id="browser-source-status" class="source-status">Detecting…</span>
                            </div>
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="row-inner">
                            <label class="row-label" for="taxonomy-enabled">Enable Taxonomy Guidance</label>
                            <label class="switch"><input id="taxonomy-enabled" type="checkbox" checked /><span class="track"></span></label>
                        </div>
                    </div>
                </div>
            </div>

            <div id="taxonomy-settings" class="settings-group">
                <div class="settings-row">
                    <div class="row-inner">
                        <label class="row-label" for="taxonomy-low-threshold">Taxonomy Low Threshold</label>
                        <div class="row-control">
                            <input id="taxonomy-low-threshold" class="inline-input" type="number" min="0" max="1" step="0.01" value="0.80" />
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="row-inner">
                        <label class="row-label" for="taxonomy-high-threshold">Taxonomy High Threshold</label>
                        <div class="row-control">
                            <input id="taxonomy-high-threshold" class="inline-input" type="number" min="0" max="1" step="0.01" value="0.92" />
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="row-inner">
                        <label class="row-label" for="taxonomy-hint-top-k">Taxonomy Hint Top-K</label>
                        <div class="row-control">
                            <input id="taxonomy-hint-top-k" class="inline-input" type="number" min="1" max="5" step="1" value="3" />
                        </div>
                    </div>
                </div>
                <div class="settings-row">
                    <div class="row-inner">
                        <label class="row-label" for="taxonomy-override-enabled">Allow High-Confidence Name Override</label>
                        <label class="switch"><input id="taxonomy-override-enabled" type="checkbox" checked /><span class="track"></span></label>
                    </div>
                </div>
            </div>

            <button id="send-btn" type="button" class="primary-btn">Start Crawl</button>

            <div id="preflight-log-section" class="preflight-log-section hidden">
                <div class="logs-header">
                    <h4>Analyze Logs</h4>
                </div>
                <pre id="preflight-log-console"></pre>
            </div>
        </div>

        <!-- LINK SELECTION MODE -->
        <div id="link-selection-section" class="hidden">
            <div class="link-selection-header">
                <h3>Select Programs to Crawl</h3>
                <p class="link-selection-subtext">
                    LLM identified potential program pages. Select the ones you want to crawl.
                </p>
            </div>
            <div class="link-actions-top">
                <label class="select-all-label">
                    <input id="select-all-links" type="checkbox" checked />
                    <span>Select All</span>
                </label>
                <span id="link-count" class="link-count-badge">0 selected</span>
            </div>
            <div class="link-automation-settings extension-only">
                <div class="row-inner">
                    <label class="row-label" for="browser-automation-enabled">Browser Automation<span class="hint">Index only</span></label>
                    <label class="switch"><input id="browser-automation-enabled" type="checkbox" /><span class="track"></span></label>
                </div>
                <div class="compact-field">
                    <label for="automation-concurrency">Automation Concurrency (1-3)</label>
                    <input id="automation-concurrency" type="number" min="1" max="3" step="1" value="2" />
                </div>
            </div>
            <ul id="link-list" class="link-list"></ul>
            <div class="link-actions-bottom">
                <button id="confirm-links-btn" type="button" class="primary-btn">Crawl Selected</button>
                <button id="cancel-links-btn" type="button" class="secondary-btn">Cancel</button>
            </div>
        </div>

        <!-- MONITOR MODE -->
        <div id="monitor-section" class="hidden">
            <div class="monitor-header">
                <h3>Crawling in Progress...</h3>
                <span id="task-id-display" class="task-badge"></span>
            </div>

            <div class="progress-container">
                <div class="status-row">
                    <p id="progress-text">Initializing...</p>
                    <span id="token-display" class="token-badge hidden">0 Tokens</span>
                </div>
                <p id="batch-summary-text" class="batch-summary-text hidden"></p>
                <div class="progress-bar">
                    <div id="progress-fill"></div>
                </div>
            </div>

            <div class="logs-container">
                <div class="logs-header">
                    <h4>Console Output</h4>
                    <button id="toggle-logs-btn" class="icon-btn small" title="Toggle Logs">_</button>
                </div>
                <pre id="logs-console"></pre>
            </div>

            <button id="stop-btn" type="button" class="danger-btn hidden">Stop Task</button>
            <button id="continue-btn" type="button" class="primary-btn hidden">Continue</button>
        </div>

        <div id="status" class="status hidden"></div>
    </div>

    <!-- CONFIG MODAL -->
    <div id="config-modal" class="modal hidden">
        <div class="modal-content">
            <div class="modal-header">
                <h2>Configuration</h2>
                <button id="close-config-btn" class="close-btn">&times;</button>
            </div>

            <div class="config-body">
                <div class="group-block">
                    <div class="group-title">Database</div>
                    <div class="settings-group">
                        <div class="settings-row stacked">
                            <div class="row-inner">
                                <label class="row-label" for="db-url-input">Database URL</label>
                                <input id="db-url-input" type="text" placeholder="postgresql://..." />
                            </div>
                        </div>
                    </div>
                </div>

                <div class="group-block">
                    <div class="group-title">LLM Priority (Drag to reorder)</div>
                    <ul id="llm-list">
                        <!-- Items injected by TS -->
                    </ul>
                </div>
            </div>

            <div class="modal-actions">
                <button id="save-config-btn" class="primary-btn">Save Changes</button>
            </div>
        </div>
    </div>

    <!-- EXPORT MODAL -->
    <div id="export-modal" class="modal hidden">
        <div class="modal-content modal-compact">
            <div class="modal-header">
                <h2>Export to Excel</h2>
                <button id="close-export-btn" class="close-btn">&times;</button>
            </div>

            <div class="config-body">
                <div class="settings-group">
                    <div class="settings-row stacked">
                        <div class="row-inner">
                            <label class="row-label" for="export-slug">University Slug</label>
                            <div class="autocomplete-wrapper">
                                <input id="export-slug" type="text" placeholder="e.g. hku" autocomplete="off" />
                                <ul id="export-slug-dropdown" class="slug-dropdown hidden"></ul>
                            </div>
                        </div>
                    </div>
                    <div class="settings-row">
                        <div class="row-inner">
                            <label class="row-label" for="export-year">Academic Year</label>
                            <div class="row-control">
                                <input id="export-year" class="inline-input" type="number" placeholder="Leave empty for all years" />
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="modal-actions">
                <button id="do-export-btn" class="primary-btn">📥 Export</button>
            </div>
        </div>
    </div>

    <!-- PREVIEW MODAL -->
    <div id="preview-modal" class="modal hidden">
        <div class="modal-content">
            <div class="modal-header">
                <h2>Database Preview</h2>
                <button id="close-preview-btn" class="close-btn">&times;</button>
            </div>

            <div class="preview-filters">
                <div class="preview-filter-row">
                    <div class="preview-field">
                        <label for="preview-slug">University</label>
                        <div class="autocomplete-wrapper">
                            <input id="preview-slug" type="text" placeholder="e.g. hku" autocomplete="off" />
                            <ul id="preview-slug-dropdown" class="slug-dropdown hidden"></ul>
                        </div>
                    </div>
                    <div class="preview-field">
                        <label for="preview-year">Year</label>
                        <input id="preview-year" type="number" placeholder="All" />
                    </div>
                    <button id="preview-search-btn" class="primary-btn preview-search-btn">Search</button>
                </div>
                <div id="preview-summary" class="preview-summary hidden">
                    <span id="preview-count-badge" class="preview-count-badge">0 programs</span>
                </div>
            </div>

            <div id="preview-list" class="preview-list">
                <div class="preview-empty">Select a university and click Search</div>
            </div>
        </div>
    </div>

    <!-- PREVIEW EDIT MODAL -->
    <div id="preview-edit-modal" class="modal hidden">
        <div class="modal-content modal-compact">
            <div class="modal-header">
                <h2>Edit Program (Preview)</h2>
                <button id="close-preview-edit-btn" class="close-btn">&times;</button>
            </div>
            <div class="config-body">
                <div class="program-edit-grid">
                    <div class="stacked-field">
                        <label for="preview-edit-name-en">Program Name (EN)</label>
                        <input id="preview-edit-name-en" type="text" />
                    </div>
                    <div class="stacked-field">
                        <label for="preview-edit-name-zh">Program Name (ZH)</label>
                        <input id="preview-edit-name-zh" type="text" />
                    </div>
                    <div class="stacked-field">
                        <label for="preview-edit-faculty">Faculty</label>
                        <input id="preview-edit-faculty" type="text" />
                    </div>
                    <div class="stacked-field">
                        <label for="preview-edit-group-code">Group Code</label>
                        <input id="preview-edit-group-code" type="text" />
                    </div>
                    <div class="stacked-field">
                        <label for="preview-edit-tuition">Tuition Amount</label>
                        <input id="preview-edit-tuition" type="number" step="0.01" />
                    </div>
                    <div class="stacked-field">
                        <label for="preview-edit-currency">Currency</label>
                        <input id="preview-edit-currency" type="text" placeholder="HKD" />
                    </div>
                    <div class="stacked-field">
                        <label for="preview-edit-source-url">Source URL</label>
                        <input id="preview-edit-source-url" type="text" />
                    </div>
                </div>

                <div class="stacked-field">
                    <label for="preview-edit-study-options">Study Options (JSON Array)</label>
                    <textarea id="preview-edit-study-options" rows="4"></textarea>
                </div>
                <div class="stacked-field">
                    <label for="preview-edit-deadlines">Deadlines (JSON Array)</label>
                    <textarea id="preview-edit-deadlines" rows="4"></textarea>
                </div>
                <div class="stacked-field">
                    <label for="preview-edit-requirements">Requirements (JSON Array)</label>
                    <textarea id="preview-edit-requirements" rows="6"></textarea>
                </div>
            </div>
            <div class="modal-actions">
                <button id="preview-edit-cancel-btn" class="secondary-btn">Cancel</button>
                <button id="preview-edit-save-btn" class="primary-btn">Save</button>
            </div>
        </div>
    </div>

    <script type="module" src="./popup.ts"></script>
</body>

</html>
```

- [ ] **Step 2: Verify the build passes**

Run: `npm run build --prefix frontend`
Expected: exits 0 (`tsc --noEmit` catches nothing here since no `.ts` changed, but this also confirms Vite can still resolve `./popup.ts` from the rewritten HTML).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/shared/popup.html
git commit -m "feat: restructure popup.html into macOS-style grouped lists"
```

---

### Task 3: Cross-check every `dom.ts` id and every dynamically-generated class name

**Files:**
- Read only: `frontend/src/shared/popup/dom.ts`, `frontend/src/shared/popup.html`, `frontend/src/shared/popup.css`, every file under `frontend/src/shared/popup/`, `frontend/src/shared/popup.ts`

**Interfaces:**
- Consumes: the finished Task 1 + Task 2 files.
- Produces: a pass/fail confirmation gating Task 4. No file changes unless a mismatch is found (see Step 3).

- [ ] **Step 1: Diff every id `dom.ts` expects against the new `popup.html`**

Run:
```bash
cd frontend/src/shared
grep -oE 'getElementById\("[a-zA-Z0-9_-]+"\)' popup/dom.ts | sed -E 's/getElementById\("([^"]+)"\)/\1/' | sort -u > /tmp/dom-ids.txt
grep -oE 'id="[a-zA-Z0-9_-]+"' popup.html | sed -E 's/id="([^"]+)"/\1/' | sort -u > /tmp/html-ids.txt
comm -23 /tmp/dom-ids.txt /tmp/html-ids.txt
```
Expected: **empty output** (every id `dom.ts` looks up exists in the new HTML). If anything prints, that id was dropped or typo'd during Task 2 — fix `popup.html` and re-run until empty.

- [ ] **Step 2: Confirm every dynamically-generated class name still has CSS coverage**

Run:
```bash
cd frontend/src/shared/popup
grep -ohE '\.className = "[^"]+"' *.ts | sed -E 's/\.className = "([^"]+)"/\1/' | tr ' ' '\n' | sort -u > /tmp/js-classes.txt
cat /tmp/js-classes.txt
```
Expected output (compare by eye against `popup.css`, confirming each has a rule — all of these were written into Task 1's stylesheet): `llm-item`, `llm-header`, `handle`, `name`, `toggle-btn`, `llm-settings`, `hidden`, `setting-row`, `link-item`, `selected`, `link-item-content`, `link-item-text`, `link-item-url`, `slug-name`, `slug-meta`, `program-card`, `program-card-header`, `program-card-name`, `program-card-id`, `program-card-actions`, `program-card-action-btn`, `danger`, `program-card-meta`, `program-tag`, `faculty`, `tuition`, `mode`, `program-card-deadlines`, `deadline-list`, `deadline-item`, `dl-round`, `dl-date`, `program-card-url`.

If any name from this list has no matching selector in `popup.css`, add it (it means Task 1's rewrite missed a rule — go back and patch `popup.css`, re-run `npm run build --prefix frontend`, and amend the Task 1 commit).

- [ ] **Step 3: Confirm the three inline-`style.display` elements still behave correctly (static inspection, no build needed)**

Open `frontend/src/shared/popup.html` and confirm:
- `#taxonomy-settings` has class `settings-group` and **no** `display` inline style (defaults visible; `popup.ts` sets it explicitly on init anyway).
- `#auto-paginate-field` has class `settings-row` and **no** `display` inline style (defaults visible via normal block flow).
- `#export-path-field` has class `settings-row stacked` **and** `style="display: none;"` inline (matches the original file's default-hidden state exactly).

If any of the three differs from this, fix `popup.html` before proceeding — this is the exact failure mode described in the plan's Pre-flight section.

- [ ] **Step 4: No commit for this task** (verification only; if Steps 1-3 required a fix, that fix was already committed as an amendment to Task 1 or Task 2's commit in the step above).

---

### Task 4: Manual visual QA across light/dark and reduced motion, then final commit

**Files:** none (manual verification task).

**Interfaces:**
- Consumes: the finished, verified `popup.html` + `popup.css` from Tasks 1-3.
- Produces: nothing further downstream — this is the last task in the plan.

- [ ] **Step 1: Build the extension bundle**

Run: `npm run build --prefix frontend`
Expected: exits 0, produces `frontend/dist/popup.html`, `frontend/dist/assets/*.js`, `frontend/dist/assets/*.css`.

- [ ] **Step 2: Load the unpacked extension in Chrome and check all 7 regions in Light mode**

1. Open `chrome://extensions`, enable Developer Mode, "Load unpacked", select `frontend/dist`.
2. Click the extension icon to open the Side Panel.
3. With the OS/browser in Light appearance, visually check: input form (University/Page/Options groups + taxonomy sub-group), the Preview modal (👁), Export modal (📥), Config modal (⚙️, including dragging an LLM item and expanding its settings), and — if a crawl can be triggered against a real page — the link-selection screen and the monitor/progress screen.
4. Confirm: grouped-list cards render with hairline borders (not heavy borders), toggle switches animate with a slight overshoot on click, the status banner (trigger via any error, e.g. searching Preview with no slug) is now visibly styled (this was the pre-existing unstyled-`.status` gap called out in the plan's Pre-flight section).

Expected: no layout looks broken (rows collapsed to the wrong axis, switches invisible, text unreadable against its background).

- [ ] **Step 3: Repeat Step 2 in Dark mode**

Switch the OS appearance to Dark (macOS: System Settings → Appearance → Dark; or in Chrome DevTools, Rendering tab → "Emulate CSS media feature prefers-color-scheme" → `dark`), reload the panel, and re-check all 7 regions.

Expected: same as Step 2, with the dark token set (`--bg:#1e1e1e`, `--accent:#0a84ff`, etc.) applied — no region should still show light-mode colors (would indicate a hardcoded color slipped into the CSS instead of a `var(--token)`).

- [ ] **Step 4: Confirm the `#auto-paginate-field` / `#export-path-field` toggle behavior specifically**

In the input form, switch Page Type between "Detail Page" and "Index Page" / "Auto Detect" — confirm the Auto-paginate row appears/disappears **and stays a proper horizontal row** (label left, switch right) each time it reappears, not stacked vertically. Then check the "Export Markdown Files" switch — turning it on should reveal the "Export Path" row as a proper stacked label-above-input row, not broken layout. This is the specific regression the Pre-flight section's inline-style analysis was written to prevent — confirm it in the running extension, not just by reading the CSS.

- [ ] **Step 5: Spot-check reduced motion**

In Chrome DevTools → Rendering tab → "Emulate CSS media feature prefers-reduced-motion" → `reduce`, reload the panel, click a few buttons/switches. Expected: no visible overshoot/spring animation — transitions are effectively instant.

- [ ] **Step 6: Run the existing Python test suite to confirm the `/ui/` mount still serves correctly**

Run: `python -m pytest tests/test_web_ui_mount.py tests/test_build_dist_client_flags.py -v`
Expected: all tests pass (or skip with the "run `npm run build --prefix frontend`" message if the bundle path isn't detected — since Step 1 already built it, they should pass, not skip).

- [ ] **Step 7: Final commit** (only if Steps 2-6 required any touch-up fixes not already committed in Tasks 1-3)

```bash
git add frontend/src/shared/popup.css frontend/src/shared/popup.html
git commit -m "style: visual QA fixes for macOS-style popup redesign"
```

If no fixes were needed, skip this step — Tasks 1 and 2 already carry the full change.
