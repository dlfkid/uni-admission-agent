---
name: uni-admission-install
description: Install, upgrade, or start (a) the adm-agent CLI binary, (b) the optional adm-agent-client browser-automation client, or (c) this plugin itself (skills + slash commands). Use when [[using-uni-admission-agent]] preflight reports cli=missing or server=down, when [[uni-admission-diagnose]] routes here to set up a client for anti-bot fallback, or when the user says "install", "set up", "upgrade", "update plugin", "重装", "升级", "更新插件", "怎么启动". Triggers on "我还没装", "帮我安装 adm-agent", "升级到新版本", "服务怎么启动", "更新这个插件", "装一个 client", "设置浏览器客户端".
---

# uni-admission-install — Install / Upgrade / Start adm-agent

You arrived here from **[[using-uni-admission-agent]]**. If not, go back first.

This skill handles the binary lifecycle: download → extract → configure → start. It runs entirely in the user's home directory — **never sudo, never system paths**.

---

## Decide: which sub-flow?

First, disambiguate **what** the user wants installed/upgraded:

- **The CLI binary** (`adm-agent` command, server, scraping engine) — §1 / §2 / §3
- **The browser-automation client** (`adm-agent-client`, a separate optional binary) — §5
- **This plugin itself** (the skills + slash commands you're reading right now) — §4

Phrases that map to "the plugin itself": "更新插件", "升级 skill", "刷新 plugin", "update plugin", "refresh skills". Phrases that map to "the client": "client", "浏览器客户端", "反爬" + "装"/"启动", or arriving here from [[uni-admission-diagnose]]'s client-mode remediation step. If unclear, ask.

For CLI binary lifecycle:

| Preflight state | User intent | Sub-flow |
|---|---|---|
| `cli=missing` | anything | **§1 Fresh install (CLI)** |
| `cli=ok`, `server=down` | crawl / preview / export | **§2 Start an existing install** |
| `cli=ok`, `server=ok` | "升级" / "upgrade" CLI | **§3 Upgrade CLI in place** |
| any | "重装" / "fix broken install" | **§1 Fresh install (CLI)** (overwrites existing) |
| any | "更新插件" / "update plugin" | **§4 Update the plugin itself** |
| any | "装 client" / anti-bot fallback needed | **§5 Set up adm-agent-client** |

---

## §1 Fresh install

### 1.1 Detect platform

```bash
uname -s   # Darwin / Linux / MINGW*-NT or use $env:OS=Windows on PowerShell
uname -m   # arm64 / x86_64
```

Map to release asset suffix:

| `uname -s` | `uname -m` | Asset suffix |
|---|---|---|
| `Darwin` | `arm64` | `macos-arm64.tar.gz` |
| `Linux` | `x86_64` | `linux-x86_64.tar.gz` |
| `MINGW*` / Windows | `x86_64` | `windows-x86_64.zip` |

If anything else (Linux ARM, macOS Intel): tell the user we don't ship a binary for their platform and offer to build from source via the GitHub README. **Do not attempt cross-platform install.**

### 1.2 Resolve version

If the user named a specific version (e.g., "装 v0.7.3"), use that and verify it exists, skipping the rest of this section:

```bash
gh release view "$VERSION" --repo dlfkid/uni-admission-agent >/dev/null
```

Otherwise resolve the latest release. **First try the "Latest" badge**:

```bash
LATEST=$(curl -sS https://api.github.com/repos/dlfkid/uni-admission-agent/releases/latest \
  | jq -r '.tag_name // empty')
```

GitHub's `/releases/latest` endpoint returns only the release explicitly tagged with the "Latest" badge — it excludes drafts and pre-releases unless the maintainer marked one as latest. **If `$LATEST` is empty, fall back to the most recent tag**:

```bash
if [ -z "$LATEST" ]; then
  echo "No 'Latest' release tagged — falling back to most recent tag"
  LATEST=$(curl -sS "https://api.github.com/repos/dlfkid/uni-admission-agent/releases?per_page=1" \
    | jq -r '.[0].tag_name // empty')
fi
if [ -z "$LATEST" ]; then
  echo "❌ No releases found at all. Cannot install."
  exit 1
fi
echo "Resolved version: $LATEST"
```

This fallback matters because:
- New projects with only `prerelease` tags would otherwise have `/latest` return 404
- A maintainer who forgot to re-tag "Latest" on a fresh release won't break installs

### 1.3 Show the plan, then confirm

Print verbatim to the user before downloading anything:

```
📦 即将安装 adm-agent <VERSION>

  来源:        https://github.com/dlfkid/uni-admission-agent/releases
  二进制:      adm-agent-<VERSION>-<OS>-<ARCH>.<EXT>
  安装目录:    ~/.uni-agent/bin/adm-agent  (no sudo, no PATH pollution)
  数据库:      ~/.uni-agent/admission.db  (SQLite, zero DB setup, auto-created)
  配置:        ~/.uni-agent/.env  (LLM key 写在这里)
  浏览器:      Chromium (Playwright) — crawl 必需，安装时一并装好
  软链:        ~/.local/bin/adm-agent → ~/.uni-agent/bin/adm-agent
               (你需要把 ~/.local/bin 加入 PATH，或手动调用全路径)

  没有任何 sudo 操作。所有文件都在 home 下。
```

Wait for explicit confirmation before proceeding. If the user says no, stop — don't suggest alternatives.

### 1.4 Download + extract

```bash
mkdir -p ~/.uni-agent/bin
cd /tmp
ARTIFACT="adm-agent-${VERSION}-${OS}-${ARCH}.${EXT}"
curl -fL -o "$ARTIFACT" \
  "https://github.com/dlfkid/uni-admission-agent/releases/download/${VERSION}/${ARTIFACT}"

# Extract
case "$EXT" in
  tar.gz) tar -xzf "$ARTIFACT" -C ~/.uni-agent/bin --strip-components=1 ;;
  zip)    unzip -o "$ARTIFACT" -d ~/.uni-agent/bin ;;
esac

chmod +x ~/.uni-agent/bin/adm-agent
```

On macOS only: clear the quarantine attribute so Gatekeeper doesn't block first launch:

```bash
xattr -dr com.apple.quarantine ~/.uni-agent/bin/adm-agent || true
```

### 1.5 Symlink onto PATH (best-effort)

```bash
mkdir -p ~/.local/bin
ln -sf ~/.uni-agent/bin/adm-agent ~/.local/bin/adm-agent
```

Then check if `~/.local/bin` is in PATH:

```bash
case ":$PATH:" in *":$HOME/.local/bin:"*) echo "PATH=ok" ;; *) echo "PATH=missing" ;; esac
```

If missing, tell the user (don't auto-edit shell rc files):

> `~/.local/bin` 不在 PATH。要么加这行到 `~/.zshrc` / `~/.bashrc`：
> ```
> export PATH="$HOME/.local/bin:$PATH"
> ```
> 要么直接用全路径调用：`~/.uni-agent/bin/adm-agent`。

### 1.6 Seed .env with one LLM key

If `~/.uni-agent/.env` doesn't exist, create it with a minimal template:

```bash
cat > ~/.uni-agent/.env <<'EOF'
# Database — leave commented for SQLite default (recommended)
# DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db

# LLM provider (at least ONE required — fill the key you have)
DEEPSEEK_API_KEY=
GEMINI_API_KEY=
VOLC_API_KEY=
CUSTOM_LLM_API_KEY=

LLM_PRIORITY_LIST=deepseek, gemini, volcengine, custom
EOF
```

Then ask the user which LLM they have a key for and which key value to write. **Write the value into `~/.uni-agent/.env` only after user explicitly provides it.** Never hard-code or hallucinate a key.

### 1.7 Install the browser (REQUIRED — not optional)

Crawling uses crawl4ai, which drives a real Chromium via Playwright. This is required for **every** crawl, including server-side mode — `adm-agent serve` hard-fails on startup if Chromium is missing. So install it now, as a mandatory step:

```bash
~/.uni-agent/bin/adm-agent browser-install
```

(Note the command is `browser-install`, not `install-browser`. It runs Playwright's bundled chromium download. First run takes 1-2 minutes and ~150MB.)

### 1.8 First-run check

```bash
~/.uni-agent/bin/adm-agent check
```

This validates: dependencies present, Chromium installed, SQLite reachable, **and at least one real LLM provider key in `.env`** (placeholder values like `your_..._here` are rejected). If `check` fails on the LLM key, the user didn't fill `~/.uni-agent/.env` in step 1.6 — go back and prompt them for a real key.

A passing `check` means the next `adm-agent serve` + crawl will actually work — no false green.

### 1.9 Report install success

```
✅ adm-agent <VERSION> 安装完成

  二进制:  ~/.uni-agent/bin/adm-agent  (link: ~/.local/bin/adm-agent)
  数据库:  ~/.uni-agent/admission.db (SQLite, auto-created on first run)
  配置:    ~/.uni-agent/.env
  浏览器:  Chromium ✓ installed

下一步：在你自己的终端跑 `adm-agent serve` 启动服务（你能看到日志、Ctrl-C 干净退出）。
跑起来之后告诉我，我继续你最初的请求。
```

**Do NOT auto-start the server.** Tell the user to start it in their own terminal. (Router skill enforces this.)

> ⚠️ **`.env` 位置很重要**：`.env` 必须在 `~/.uni-agent/.env`。二进制会先按当前工作目录向上找 `.env`，找不到再回退到 `~/.uni-agent/.env`，所以装好后从任意目录跑 `adm-agent serve` 都能读到 key。如果用户把 key 写到了别处（比如某个项目目录的 `.env`），从其他目录启动就会读不到——统一放 `~/.uni-agent/.env` 最稳。

---

## §2 Start an existing install

```bash
# Foreground (recommended — user sees logs, can Ctrl+C)
adm-agent serve

# OR: daemon mode (only if user explicitly asks) — a SEPARATE command,
# not a flag on `serve`. There is no `serve --daemon`.
adm-agent serve-install
```

`adm-agent serve` prints the startup banner directly to the terminal:

```
🚀 Starting server on 0.0.0.0:8910
   🌐 Web UI:  http://127.0.0.1:8910/ui/
   📚 API docs: http://127.0.0.1:8910/docs
   🩺 Health:   http://127.0.0.1:8910/health
```

`adm-agent serve-install` instead detaches immediately and prints only:

```
🚀 Server daemon started (PID <pid>)
   Log: <path>
   Stop: adm-agent serve-stop
```

(the banner above goes into that log file, not the terminal — the PID line
is your only synchronous confirmation). Either way, wait until `/health`
returns `200` before telling the user it's ready:

```bash
for i in 1 2 3 4 5 6 7 8 9 10; do
  curl -sS --max-time 1 http://127.0.0.1:8910/health >/dev/null && break
  sleep 1
done
```

Then tell the user "服务已就绪" and continue with the original request.

If the server doesn't come up in 10 seconds:
- Check for port collision: `lsof -i :8910` (mac/linux) or `netstat -ano | findstr 8910` (Win)
- Suggest a different port: `adm-agent serve --port 8911`

---

## §3 Upgrade in place

```bash
# 1. Stop the current server (Ctrl-C in user's terminal, or:)
adm-agent serve-stop

# 2. Run §1 (Fresh install) — it overwrites the binary atomically
#    Existing data + .env are untouched (they live in different dirs)

# 3. Restart per §2
```

Tell the user that data + config are preserved; only the binary is replaced. Show the version-to-version delta from GitHub Releases page if useful.

---

## §4 Update the plugin itself

The plugin (skills + slash commands you're reading right now) is **separate** from the `adm-agent` CLI binary. They update on different cadences and through different mechanisms.

### 4.1 Detect which CLI loaded this plugin

Check which of these paths exist on the user's machine:

```bash
[ -f "$HOME/.claude/plugins/installed_plugins.json" ] && echo "claude-code"
[ -d "$HOME/.agents/skills/using-uni-admission-agent" ] && echo "codex"
[ -d "$HOME/.config/opencode/skills/using-uni-admission-agent" ] && echo "opencode"
[ -d "$HOME/.openclaw/skills/using-uni-admission-agent" ] && echo "openclaw"
```

A user may have the plugin installed in multiple CLIs simultaneously — update each.

### 4.2 Update commands per CLI

**Claude Code** (uses native plugin marketplace):

```bash
claude plugin update uni-admission-agent
```

Then restart Claude Code to pick up changes.

**Codex / OpenCode / OpenClaw** (uses symlinks to a git clone — usually `~/.uni-admission-agent/`):

```bash
# Find the clone (default location is $HOME/.uni-admission-agent)
CLONE_DIR="${UNI_ADMISSION_HOME:-$HOME/.uni-admission-agent}"

# If user followed the standard install, this dir exists. Pull updates.
git -C "$CLONE_DIR" pull --ff-only

# Re-run installer to refresh any new symlinks (cheap, idempotent)
bash "$CLONE_DIR/install-plugin.sh"
```

If `$CLONE_DIR` doesn't exist, the user installed via manual symlink. Locate the source dir by following the symlink:

```bash
readlink ~/.agents/skills/using-uni-admission-agent              # Codex
readlink ~/.config/opencode/skills/using-uni-admission-agent     # OpenCode
readlink ~/.openclaw/skills/using-uni-admission-agent            # OpenClaw
```

…then `git pull` in whatever parent dir that points to.

### 4.3 Verify update applied

After updating, ask the user to:

1. Restart the CLI (skill content is loaded at session start in some CLIs)
2. Type `/uni-admission-agent:uni-admission-agent` and check that any recently-added phrasing appears in the response

If the user can't see the change, they likely didn't restart — that's the most common failure.

### 4.4 What gets updated

- ✅ Skill markdown (this file, crawl/diagnose/export skills, router)
- ✅ Slash command stubs in `commands/`
- ✅ Plugin manifests in `.claude-plugin/`
- ❌ The `adm-agent` CLI binary — that's §3, separate update

### 4.5 Don't confuse the two updates

| User says | Means | Sub-flow |
|---|---|---|
| "新版抓取功能" / "新爬虫特性" | Tool binary | §3 |
| "Skill 改了" / "新的 prompt 模板" / "router 更新了" | Plugin | §4 |
| "全都更新一下" | Both | §3 first, then §4 |

When in doubt, do §4 (it's faster and lower risk) and ask whether they also want §3.

---

## §5 Set up adm-agent-client (browser automation client, optional)

Only needed when the server's own headless browser gets blocked by a
target site's anti-bot measures, or [[uni-admission-diagnose]] routes here
after a `NO_MARKDOWN` / `raw=0` failure. Most users never need this — skip
it unless something pointed you here.

**What it is**: a separate binary from `adm-agent` that drives a REAL,
locally-installed Chrome/Edge browser (via CDP, not Playwright) on the
user's own machine, connects to the running `adm-agent serve` over
WebSocket (`/clients/ws`), and answers page-fetch RPCs when a crawl is
submitted with `browser_provider=client`.

### 5.1 Prerequisites

- `adm-agent serve` already running — the client connects TO it, doesn't
  replace it.
- A real Google Chrome or Microsoft Edge installed on the user's machine
  (**not** Playwright's bundled Chromium — the client launches the actual
  installed browser: `/Applications/Google Chrome.app/...` on macOS,
  `chrome.exe` on Windows, `google-chrome`/`google-chrome-stable` on
  Linux). If missing, tell the user to install real Chrome/Edge first —
  this skill does not install a browser for the client the way §1.7 does
  for the server.

### 5.2 Download + extract

Same release, same platform-detection table as §1.1 — asset name has a
`-client` suffix (`adm-agent-client-<VERSION>-<OS>-<ARCH>.<EXT>`):

```bash
mkdir -p ~/.uni-agent-client/bin
cd /tmp
ARTIFACT="adm-agent-client-${VERSION}-${OS}-${ARCH}.${EXT}"
curl -fL -o "$ARTIFACT" \
  "https://github.com/dlfkid/uni-admission-agent/releases/download/${VERSION}/${ARTIFACT}"
case "$EXT" in
  tar.gz) tar -xzf "$ARTIFACT" -C ~/.uni-agent-client/bin --strip-components=1 ;;
  zip)    unzip -o "$ARTIFACT" -d ~/.uni-agent-client/bin ;;
esac
chmod +x ~/.uni-agent-client/bin/adm-agent-client
xattr -dr com.apple.quarantine ~/.uni-agent-client/bin/adm-agent-client || true  # macOS only
```

### 5.3 Initialize + start

`init` is interactive (prompts for serve URL and client name) — run it
and answer the prompts yourself, don't try to script around it:

```bash
~/.uni-agent-client/bin/adm-agent-client init
#   Serve URL: http://127.0.0.1:8910   (or wherever adm-agent serve is running)
#   Client name: <accept the default — usually the machine hostname>
```

Then start it. Prefer background (`start-install`) unless the user wants
to watch its logs live in their own terminal:

```bash
~/.uni-agent-client/bin/adm-agent-client start-install
#   🚀 Client daemon started (PID ...)
#   Log: ~/.adm-agent/client.log
#   Stop: adm-agent-client stop
```

### 5.4 Verify it actually registered

Don't just trust the "started" message — confirm the *server* sees it:

```bash
curl -sS http://127.0.0.1:8910/clients
```

Expect a JSON array with one entry whose `capabilities.browser_automation`
is `true`. An empty array (`[]`) means the WebSocket handshake hasn't
completed yet (wait a couple seconds and retry) or failed outright — check
`~/.adm-agent/client.log` for `Client websocket connected` vs repeated
`Client websocket loop error`.

### 5.5 Known reliability issues — set expectations honestly

**The WebSocket transport bug is fixed** (server-side self-deadlock in the
RPC dispatcher — see git history around "resolve client-mode browser RPC
self-deadlock"). Verified with a real client + a real ~15-minute crawl:
zero disconnects, one clean RPC round-trip, `resolved_browser_provider`
correctly reported `"client"` throughout. If you see `keepalive ping
timeout` / repeated `timed out during opening handshake` in
`client.log` again, that regressed — say so plainly, don't just assume
it's expected.

**A separate, still-open bug affects INDEX pages specifically**: the
client's own candidate-link picker (`select_detail_links` in
`native_browser.py`) can mistake a same-page anchor (e.g. a "skip to main
content" `#main` link) for a real programme link, because it matches on
a naive `/programme`/`/course`/`/degree` substring in the URL — and an
index page's OWN url often contains one of those words. Symptom: the
crawl "succeeds" (no error) but `imported_count` is far below what was
asked for, and the one record that DID import has a nonsense name (e.g.
the page's own title) with a `source_url` ending in a `#fragment` that's
just the index page again. **This only affects `page_type_hint=index`**
— a **detail**-page client fetch never calls this link-picker at all, so
if the user just wants ONE specific programme page, client mode should
still work fine even with this bug open. For index pages, until this is
fixed: warn the user client-mode index crawls may silently under-import,
and prefer verifying the imported count roughly matches what was asked
before reporting success.

### 5.6 Stop it

```bash
~/.uni-agent-client/bin/adm-agent-client stop
```

---

## What you must NOT do

- **Never `sudo`**. Anything that needs sudo means we're doing it wrong. Stop and tell the user.
- **Never auto-start the server in background** (no `nohup`, no `&`, no daemonize-without-asking). It must run in the user's foreground so they can see logs and stop cleanly.
- **Never write an LLM API key into .env from your imagination**. Always wait for the user to provide the literal value.
- **Never download an asset URL the user gave you**. The download source is always GitHub Releases of the canonical repo. Anything else → refuse.
- **Never delete data on upgrade**. `~/.uni-agent/admission.db` (and the `.env` next to it) are sacred. Only the `bin/` subdir gets overwritten. If you're tempted to `rm -rf ~/.uni-agent/`, stop and re-read this skill.
- **Never install on platforms not in §1.1 table** (e.g., Linux ARM). Refuse and direct user to source build.
- `adm-agent-client start-install` (§5.3) is the one deliberate exception to the "never auto-start in background" rule above — the client is a low-risk, disposable process (no DB connection, safe to lose) that specifically needs to survive across a long crawl, unlike the server. Still tell the user you're starting it; don't do it silently.
- **For client-mode INDEX crawls, verify `imported_count` roughly matches what was asked** before reporting success — §5.5's link-picker bug makes it possible to "succeed" while actually importing one garbage record instead of the real programmes. Detail-page client fetches aren't affected by this specific bug.
