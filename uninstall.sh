#!/usr/bin/env bash
# Remove the Claude Code Pet. Leaves the pet library and settings backups alone.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$ROOT/engine"
PY="$ROOT/.venv/bin/python"
PLIST="$HOME/Library/LaunchAgents/com.local.claude-pet.plist"

echo "==> Stopping"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
pkill -f "pet_overlay.py" 2>/dev/null || true
rm -f "$PLIST"

echo "==> Hooks"
if [ -x "$PY" ]; then "$PY" "$ENGINE/merge_hooks.py" --remove
else python3 "$ENGINE/merge_hooks.py" --remove; fi

echo "==> Commands and skill"
rm -f "$HOME/.local/bin/pet" "$HOME/.claude/commands/pet.md"
rm -rf "$HOME/.claude/skills/hatch-pet"
rm -f "$HOME/.assistant-pet/engine"

echo
echo "Removed. Your pets are still in ${CLAUDE_PETS_DIR:-$HOME/Documents/Claude/Pets},"
echo "and settings.json backups are still in ~/.claude."
