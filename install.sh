#!/usr/bin/env bash
# Install the Claude Code Pet: dependencies, the reference pet, the hatch-pet
# skill, the `pet` command, autostart, and the Claude Code lifecycle hooks.
#
#   ./install.sh              full install
#   ./install.sh --no-hooks   skip editing ~/.claude/settings.json
#   ./install.sh --no-agent   skip the login-time LaunchAgent
#   ./uninstall.sh            undo everything
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENGINE="$ROOT/engine"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
PETS="${CLAUDE_PETS_DIR:-$HOME/Documents/Claude/Pets}"
PLIST="$HOME/Library/LaunchAgents/com.local.claude-pet.plist"
LABEL="com.local.claude-pet"

WANT_HOOKS=1
WANT_AGENT=1
for arg in "$@"; do
  case "$arg" in
    --no-hooks) WANT_HOOKS=0 ;;
    --no-agent) WANT_AGENT=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> Python environment"
if [ ! -x "$PY" ]; then
  if command -v uv >/dev/null; then
    uv venv --python 3.12 "$VENV"
  elif command -v python3 >/dev/null; then
    python3 -m venv "$VENV"
  else
    echo "need uv or python3" >&2; exit 1
  fi
fi
if command -v uv >/dev/null; then
  VIRTUAL_ENV="$VENV" uv pip install -q "PySide6>=6.7,<7" pillow
else
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q "PySide6>=6.7,<7" pillow
fi
echo "    $("$PY" -c 'import PySide6; print("PySide6", PySide6.__version__)')"

echo "==> Engine location"
mkdir -p "$HOME/.assistant-pet"
printf '%s' "$ENGINE" > "$HOME/.assistant-pet/engine"
echo "    $ENGINE"

echo "==> Pet library"
mkdir -p "$PETS"
for pet in "$ROOT"/pets/*/; do
  [ -f "$pet/pet.json" ] || continue
  name="$(basename "$pet")"
  if [ ! -f "$PETS/$name/pet.json" ]; then
    cp -R "$pet" "$PETS/$name"
    echo "    installed $name"
  else
    echo "    $name already present"
  fi
done
"$PY" "$ENGINE/hatch.py" list

echo "==> hatch-pet skill"
mkdir -p "$HOME/.claude/skills/hatch-pet"
cp -R "$ROOT/skill/." "$HOME/.claude/skills/hatch-pet/"
echo "    ~/.claude/skills/hatch-pet"

echo "==> pet command"
mkdir -p "$HOME/.local/bin"
# the wrapper needs to know where the engine lives
# rm first: `>` onto an existing symlink writes through it and clobbers the target
rm -f "$HOME/.local/bin/pet"
sed "s|__ENGINE__|$ENGINE|" "$ENGINE/bin/pet" > "$HOME/.local/bin/pet"
chmod +x "$HOME/.local/bin/pet"
if command -v pet >/dev/null 2>&1; then
  echo "    $(command -v pet)"
else
  echo "    WARNING: ~/.local/bin is not on your PATH - add it to your shell rc"
fi
mkdir -p "$HOME/.claude/commands"
rm -f "$HOME/.claude/commands/pet.md"
sed "s|__PET__|$HOME/.local/bin/pet|g" "$ENGINE/pet.md" > "$HOME/.claude/commands/pet.md"
echo "    /pet slash command installed"

if [ "$WANT_AGENT" = 1 ]; then
  echo "==> Autostart at login"
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$ENGINE/pet_overlay.py</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/claude-pet.log</string>
  <key>StandardErrorPath</key><string>/tmp/claude-pet-error.log</string>
</dict>
</plist>
PLIST_EOF
  plutil -lint "$PLIST" >/dev/null
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "    loaded $LABEL"
fi

if [ "$WANT_HOOKS" = 1 ]; then
  echo "==> Claude Code hooks"
  "$PY" "$ENGINE/merge_hooks.py" "$PY" "$ENGINE/petctl.py"
fi

echo
echo "Done."
echo "  toggle       : pet          (or /pet inside Claude Code)"
echo "  force on/off : pet on | pet off"
echo "  find it      : pet screens  then  pet center <n>"
echo "  resize       : pet size 180   (or scroll over it)"
echo "  perform      : pet wave | pet jump | pet celebrate"
echo "  sessions     : pet sessions | pet focus <pane-id>"
echo "  more pets    : pet pets | pet use <id>   (or ask Claude to hatch one)"
[ "$WANT_HOOKS" = 1 ] && echo "  Restart Claude Code so it picks the hooks up."
