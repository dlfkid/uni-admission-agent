#!/usr/bin/env bash
# install-plugin.sh — cross-CLI installer for the uni-admission-agent plugin.
#
# Auto-detects which LLM CLI(s) are installed on this machine and registers
# the plugin / symlinks the skills accordingly. Safe to re-run — acts as
# both install and refresh.
#
# Usage:
#   # Standalone (clones repo first):
#   curl -fsSL https://raw.githubusercontent.com/dlfkid/uni-admission-agent/main/install-plugin.sh | bash
#
#   # OR: clone then run (preferred — you can read the script first):
#   git clone https://github.com/dlfkid/uni-admission-agent.git ~/.uni-admission-agent
#   bash ~/.uni-admission-agent/install-plugin.sh
#
#   # From inside an existing clone:
#   ./install-plugin.sh
#
# Override the clone location with UNI_ADMISSION_HOME if needed.

set -euo pipefail

REPO_URL="https://github.com/dlfkid/uni-admission-agent.git"
DEFAULT_INSTALL_DIR="$HOME/.uni-admission-agent"
SKILLS=(
  using-uni-admission-agent
  uni-admission-install
  uni-admission-crawl
  uni-admission-diagnose
  uni-admission-export
)

# ---------------------------------------------------------------------------
# Locate or clone the source tree
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"

if [ -f "$SCRIPT_DIR/.claude-plugin/plugin.json" ]; then
  # Script lives inside a clone — use that as the install dir.
  INSTALL_DIR="$SCRIPT_DIR"
  echo "📂 Running from existing clone: $INSTALL_DIR"
else
  # Remote install — clone (or update) into $UNI_ADMISSION_HOME or default.
  INSTALL_DIR="${UNI_ADMISSION_HOME:-$DEFAULT_INSTALL_DIR}"
  if [ -d "$INSTALL_DIR/.git" ]; then
    echo "🔄 Updating clone at $INSTALL_DIR..."
    git -C "$INSTALL_DIR" pull --ff-only
  else
    echo "📦 Cloning to $INSTALL_DIR..."
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi
fi

# Sanity check
if [ ! -d "$INSTALL_DIR/skills" ]; then
  echo "❌ skills/ directory missing in $INSTALL_DIR — install dir looks wrong." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Symlink helper — idempotent (replaces stale link, refuses non-symlink files)
# ---------------------------------------------------------------------------

link_skill() {
  local src="$1"
  local dst="$2"
  if [ -e "$dst" ] && [ ! -L "$dst" ]; then
    echo "  ⚠ $dst exists and is not a symlink — skipping (move it aside first)"
    return
  fi
  ln -sfn "$src" "$dst"
}

INSTALLED=()

# ---------------------------------------------------------------------------
# Claude Code — native plugin marketplace
# ---------------------------------------------------------------------------

if command -v claude >/dev/null 2>&1; then
  echo
  echo "✨ Detected Claude Code"
  # `marketplace add` may fail if it's already registered — that's fine,
  # we still try the install. The install step is the real success gate.
  claude plugin marketplace add "$REPO_URL" 2>&1 || \
    echo "  (marketplace may already be registered — continuing to install step)"
  if claude plugin install uni-admission-agent; then
    INSTALLED+=("Claude Code (via plugin marketplace)")
  else
    echo "  ⚠ Claude Code plugin install failed — see error above."
    echo "    (Most common cause: the marketplace manifest isn't on the main"
    echo "    branch yet, or the version was already installed and pinned.)"
  fi
fi

# ---------------------------------------------------------------------------
# Codex CLI — symlink skills into the universal ~/.agents/skills/ path
# ---------------------------------------------------------------------------

if command -v codex >/dev/null 2>&1 || [ -d "$HOME/.codex" ]; then
  echo
  echo "✨ Detected Codex CLI"
  mkdir -p "$HOME/.agents/skills"
  for s in "${SKILLS[@]}"; do
    link_skill "$INSTALL_DIR/skills/$s" "$HOME/.agents/skills/$s"
  done
  echo "  ↳ skills linked to ~/.agents/skills/"
  INSTALLED+=("Codex (symlink to ~/.agents/skills/)")
fi

# ---------------------------------------------------------------------------
# OpenCode — symlink skills into ~/.config/opencode/skills/
# ---------------------------------------------------------------------------

if command -v opencode >/dev/null 2>&1 || [ -d "$HOME/.config/opencode" ]; then
  echo
  echo "✨ Detected OpenCode"
  mkdir -p "$HOME/.config/opencode/skills"
  for s in "${SKILLS[@]}"; do
    link_skill "$INSTALL_DIR/skills/$s" "$HOME/.config/opencode/skills/$s"
  done
  echo "  ↳ skills linked to ~/.config/opencode/skills/"
  INSTALLED+=("OpenCode (symlink to ~/.config/opencode/skills/)")
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo

if [ ${#INSTALLED[@]} -eq 0 ]; then
  cat >&2 <<EOF
❌ No supported LLM CLI detected on this machine.

   Supported: claude, codex, opencode.

   If you have one of these installed under a non-standard path, set:
     export PATH="\$PATH:/path/to/your/cli"
   then re-run this script.

   Or follow the manual install steps in:
     $INSTALL_DIR/README.md  (section: Using with LLM CLIs)
EOF
  exit 1
fi

echo "✅ Plugin installed for: ${INSTALLED[*]}"
echo "   Source: $INSTALL_DIR"
echo
echo "Update later:"
echo "  • Claude Code:        claude plugin update uni-admission-agent"
echo "  • Codex / OpenCode:   bash $INSTALL_DIR/install-plugin.sh"
echo "                        (re-runs git pull + refreshes symlinks)"
echo
echo "Try it: ask your CLI to crawl a university — e.g. \"帮我爬利兹大学硕士课程\""
