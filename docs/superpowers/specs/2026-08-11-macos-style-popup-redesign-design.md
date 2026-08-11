# Chrome Extension Popup — macOS-Style Visual Redesign

**Date:** 2026-08-11
**Status:** Approved (pending spec review)

## 1. Problem

The extension's Side Panel UI (`frontend/src/shared/popup.html` + `popup.css`)
works but reads as a generic dark "dev tool" panel: a single hardcoded dark
theme (`--bg: #1a1a2e`, purple `#6c63ff` accent), flat label-above-input
fields with no grouping, and mostly linear CSS transitions. There is no
light mode — the panel always renders dark regardless of the user's system
appearance.

The user wants a visual overhaul modeled on macOS System Settings /
iOS-style interface conventions, with light/dark mode that follows the
browser's `prefers-color-scheme` automatically.

## 2. Scope

Full visual redesign covering all 7 UI regions in the popup:

1. Input form (main crawl-config screen)
2. Link selection screen
3. Monitor / progress screen
4. Config modal
5. Export modal
6. Preview modal
7. Preview-edit modal

**In scope:** `frontend/src/shared/popup.html`, `frontend/src/shared/popup.css`.

**Explicitly out of scope** (confirmed with user):
- No component framework, no animation library dependency (Motion/Framer
  Motion) — CSS-only spring-like transitions.
- No backend/API changes.
- No changes to `background.ts`.
- No manual light/dark toggle — purely `prefers-color-scheme`, zero JS.
- No changes to `popup.ts`, `dom.ts`, or any `popup/*Flow.ts` module — see
  §4 for why this is safe.

## 3. Visual System

### 3.1 Color tokens

| Token | Light | Dark |
|---|---|---|
| `--bg` (page background) | `#eeeef1` | `#1e1e1e` |
| `--group-bg` (grouped-list card) | `#ffffff` | `#2c2c2e` |
| `--text` | `#1d1d1f` | `#f5f5f7` |
| `--text-muted` | `#6e6e73` | `#98989d` |
| `--divider` | `rgba(0,0,0,.08)` | `rgba(255,255,255,.08)` |
| `--accent` (systemBlue) | `#007aff` | `#0a84ff` |
| `--accent-soft` (accent tint, badges/focus rings) | `rgba(0,122,255,.12)` | `rgba(10,132,255,.18)` |
| `--control-bg` (input/select/switch-track base) | `#e4e4e8` | `#3a3a3c` |
| `--success` | `#34c759` | `#32d74b` |
| `--error` | `#ff3b30` | `#ff453a` |

Implemented as CSS custom properties on `.side-panel`-equivalent root scope,
with the dark set applied inside `@media (prefers-color-scheme: dark)`. No
`data-theme` attribute, no JS branching — the browser/OS setting is the only
input.

### 3.2 Typography

Font stack: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif`.
Drops the `Inter` reference (Inter was never actually bundled as a webfont —
only declared in the stack as a no-op — so this is a net simplification, not
a regression).

Sizes stay close to current (13px body, 11px micro-labels, 16-18px headers)
but section headers move from bold uppercase labels-per-field to macOS-style
group titles (11px, uppercase, `--text-muted`, sits above each group).

### 3.3 Shape & elevation

- Grouped-list containers: 10px radius, `box-shadow: 0 0 0 1px var(--divider)`
  (hairline, not a heavy border).
- Buttons: 10px radius. Icon buttons: fully round.
- Modals keep centered overlay + backdrop blur (already present), just
  restyled chrome (header/body/actions use the new tokens).

### 3.4 Motion

CSS-only spring approximation via `cubic-bezier` easing (no JS/animation
library):

- Button press: `transform: scale(0.97)` / `scale(0.88)` for icon buttons,
  `cubic-bezier(.34,1.56,.64,1)` (overshoot) on release.
- Toggle switch thumb: same overshoot curve, ~260ms.
- Hover/focus: shorter, no-overshoot easing (~140-160ms).
- `@media (prefers-reduced-motion: reduce)` collapses all of the above to
  near-instant (`transition-duration: 0.01ms`), consistent with the
  accessibility principle from the Apple design reference material used as
  inspiration for this pass.

## 4. Layout Restructuring

All 7 regions adopt the same **grouped-list pattern**:

```
.group-title            (small caps label above each group, e.g. "OPTIONS")
.settings-group          (white/dark card, 10px radius)
  .settings-row          (one field; hairline divider between rows, none on last)
    .row-label           (field name, normal weight — not uppercase)
    .row-control          (right-aligned compact control: switch / select / short input)
  .settings-row.stacked  (label above, full-width control below — for long values:
                           URL display, JSON textareas, password fields)
```

Native checkboxes (`auto-paginate`, `export-md`, `taxonomy-enabled`,
`taxonomy-override-enabled`, `select-all-links`, `browser-automation-enabled`)
get wrapped in a `.switch` label (`<label class="switch"><input type="checkbox" id="...">
<span class="track"></span></label>`) to render as iOS-style toggles. The
`id` and the underlying `<input type="checkbox">` are untouched, so
`element.checked` reads/writes in existing `.ts` code keep working unchanged.

### 4.1 Why this is low-risk

Confirmed by reading `dom.ts` and the `popup/*Flow.ts` modules before
finalizing this design:

- `dom.ts` resolves every element by **id** — none of the ids change.
- `linkSelectionFlow.ts`, `previewFlow.ts`, `configFlow.ts` generate HTML
  fragments at runtime using fixed class names: `program-card`,
  `program-card-header`, `program-card-name`, `program-card-id`,
  `program-card-meta`, `program-card-actions`, `program-card-action-btn`,
  `program-card-deadlines`, `link-item`, `link-item-content`,
  `link-item-text`, `link-item-url`, `llm-item`, `llm-header`,
  `deadline-item`, `deadline-list`, `preview-empty`. **These class names are
  kept as-is** — only their CSS rules are rewritten to match the new visual
  system. No `.ts` file needs to change.

This means the redesign is purely additive/replacing in `popup.html` markup
structure and a full rewrite of `popup.css`, with zero behavioral surface
area touched.

## 5. Testing & Verification

- `npm run build --prefix frontend` (`tsc --noEmit && vite build`) must pass
  — confirms no markup/id mismatch broke TS compilation.
- Existing Python suite (`tests/test_web_ui_mount.py`,
  `tests/test_build_dist_client_flags.py`) is id/class-agnostic (checks
  bundle serving + content-type only) — expected to stay green unchanged.
- Manual visual verification: load the unpacked extension in Chrome and
  separately open `/ui/`; check all 7 regions once under system Light mode
  and once under system Dark mode (macOS: System Settings → Appearance, or
  Chrome DevTools → Rendering → "Emulate CSS media feature
  prefers-color-scheme").
- `prefers-reduced-motion: reduce` spot-check via DevTools emulation (no
  functional behavior depends on animation completing, so this is a visual
  check only).

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Grouped-list markup change accidentally drops/renames an id `dom.ts` depends on | Cross-check every id in `dom.ts` against the new `popup.html` after editing; `tsc --noEmit` will not catch this (DOM lookups aren't statically typed against the HTML), so this needs a manual diff pass, not just a build check. |
| Dynamically-generated fragments (`program-card`, `link-item`, etc.) look inconsistent with the new statically-restyled sections if any class is missed | Grep `popup/*Flow.ts` for all class-name string literals before finishing `popup.css`, confirm each has a corresponding rule under the new token system. |
| `prefers-color-scheme` dark tokens look fine in the demo mockup but clash with existing hardcoded colors inside flow-generated fragments (e.g. inline styles, if any) | Grep for inline `style=` and hardcoded hex colors in `popup/*Flow.ts` during implementation; none were found in the initial scan, but re-verify. |

## 7. Non-Goals (explicit)

- No manual theme switcher UI.
- No new npm dependencies.
- No change to extension behavior, API contracts, or `background.ts`.
- No rebrand of the extension icon/name — only in-panel chrome.
