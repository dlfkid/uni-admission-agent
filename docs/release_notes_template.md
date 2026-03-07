# Uni Admission Agent Release Notes Template (Three Separate Artifacts)

> Copy this template for each release and replace placeholders like `<VERSION>`, `<DATE>`, `<LINK>`.

## 🚀 Release `<VERSION>` (`<DATE>`)

### TL;DR
- This release publishes **three separate downloadable artifacts**: `extension` / `backend` / `client`.
- Pick only what you need using the matrix below.

## 📦 Download Artifacts

### 1) Chrome Extension
- **Who needs this:** Users using popup UI in Chrome.
- **Asset name pattern:** `uni-admission-extension-<VERSION>.zip`
- **Download:** `<LINK>`

### 2) Backend (`adm-agent`)
- **Who needs this:** Anyone running API/MCP server (`serve`).
- **Asset name pattern:**
  - Windows: `adm-agent-<VERSION>-windows-<ARCH>.zip`
  - macOS: `adm-agent-<VERSION>-macos-<ARCH>.tar.gz`
  - Linux: `adm-agent-<VERSION>-linux-<ARCH>.tar.gz`
- **Download:** `<LINK>`

### 3) Client (`adm-agent-client`)
- **Who needs this:** Users who want browser automation via `client` bridge (especially without extension open).
- **Asset name pattern:**
  - Windows: `adm-agent-client-<VERSION>-windows-<ARCH>.zip`
  - macOS: `adm-agent-client-<VERSION>-macos-<ARCH>.tar.gz`
  - Linux: `adm-agent-client-<VERSION>-linux-<ARCH>.tar.gz`
- **Download:** `<LINK>`

## 🧭 Which One Should I Download?

- **Extension UI only** → download **Extension**.
- **Deploy server (REST/MCP)** → download **Backend**.
- **Need browser automation from user machine (no extension required)** → download **Client** (plus Backend on server side).
- **Full experience** → download all three.

## ⚙️ Quick Start

### Backend (`adm-agent`)
1. Extract package.
2. Run `adm-agent serve`.

### Client (`adm-agent-client`)
1. Extract package.
2. Run `adm-agent-client init` and provide `serve host/port`.
3. Configure one optional fetch command template:
   - `ADM_AGENT_CLIENT_FETCH_CMD='adm-agent-client fetch --url "{url}" --page-type "{page_type_hint}" --json'`
4. Run `adm-agent-client start --continuous`.
5. Stop with `adm-agent-client stop` (or `adm-agent-client stop --force` if needed).
6. Check or upgrade client:
   - `adm-agent-client version --verbose`
   - `adm-agent-client upgrade --check`

### Extension
1. Unzip extension package.
2. Open `chrome://extensions`.
3. Enable Developer mode and click "Load unpacked".

## ✅ Upgrade Notes

- Breaking changes: `<NONE / LIST>`
- Migration required: `<YES/NO>`
- Recommended order:
  1. Upgrade Backend
  2. Upgrade Client
  3. Upgrade Extension

## 🐞 Known Issues

- `<ISSUE 1>`
- `<ISSUE 2>`

## 🧪 Verification Checklist

- [ ] `serve` starts successfully.
- [ ] `/health` and `/clients` endpoints are reachable.
- [ ] `adm-agent-client` can connect and stay online.
- [ ] Browser automation crawl works on at least one index URL.
- [ ] Extension can connect to backend and run one crawl task.
