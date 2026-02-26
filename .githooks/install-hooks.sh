#!/bin/bash
#
# Install Git hooks for UniAdmission Agent
# This script copies all hooks from .githooks/ to .git/hooks/ and makes them executable
#

set -e

echo "🔧 Installing Git hooks..."

# Check if we're in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "❌ Not in a git repository"
    exit 1
fi

# Get project root and hooks directory
PROJECT_ROOT=$(git rev-parse --show-toplevel)
GITHOOKS_DIR="$PROJECT_ROOT/.githooks"
GIT_HOOKS_DIR="$PROJECT_ROOT/.git/hooks"

# Check if .githooks directory exists
if [ ! -d "$GITHOOKS_DIR" ]; then
    echo "❌ .githooks directory not found at $GITHOOKS_DIR"
    exit 1
fi

# Install each hook
for hook_file in "$GITHOOKS_DIR"/*; do
    if [ -f "$hook_file" ] && [ "$(basename "$hook_file")" != "install-hooks.sh" ]; then
        hook_name=$(basename "$hook_file")
        target_file="$GIT_HOOKS_DIR/$hook_name"
        
        echo "📋 Installing $hook_name..."
        cp "$hook_file" "$target_file"
        chmod +x "$target_file"
        
        echo "  ✅ $hook_name installed and made executable"
    fi
done

echo ""
echo "🎉 Git hooks installed successfully!"
echo ""
echo "📝 Installed hooks:"
ls -la "$GIT_HOOKS_DIR" | grep -E "^-.*x.*" | awk '{print "  - " $9}' | grep -v "sample"

echo ""
echo "💡 These hooks will now run automatically:"
echo "  - pre-push: Runs pylint checks before pushing"
echo ""
echo "🚫 To temporarily bypass hooks, use: git push --no-verify"