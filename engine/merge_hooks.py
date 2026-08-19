#!/usr/bin/env python3
"""Merge (or remove) the pet's lifecycle hooks in ~/.claude/settings.json.

Idempotent: re-running replaces the pet's own entries and leaves every other
hook untouched. Always writes a timestamped backup first, and refuses to touch
a settings file that is not already valid JSON.

    python3 merge_hooks.py <python-path> <petctl-path>   # install
    python3 merge_hooks.py --remove                      # uninstall
"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
MARK = "claude-pet/petctl.py"          # identifies the entries this script owns

# Every event routes through `petctl.py hook <Event>`, which parses the JSON
# payload Claude Code sends on stdin and turns it into the pet's task card.
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
          "Notification", "Stop", "SessionEnd"]


def strip_ours(hooks: dict) -> dict:
    """Drop previously-installed pet entries so re-running never duplicates them."""
    cleaned = {}
    for event, groups in hooks.items():
        kept = []
        for group in groups:
            inner = [h for h in group.get("hooks", [])
                     if MARK not in str(h.get("command", ""))]
            if inner:
                kept.append({**group, "hooks": inner})
            elif not group.get("hooks"):
                kept.append(group)          # preserve unrelated empty groups
        if kept:
            cleaned[event] = kept
    return cleaned


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    remove = "--remove" in sys.argv
    if not remove and len(args) < 2:
        print("usage: merge_hooks.py <python> <petctl.py>   |   merge_hooks.py --remove",
              file=sys.stderr)
        return 2

    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        try:
            data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"refusing to edit settings.json - it is not valid JSON: {exc}",
                  file=sys.stderr)
            return 1
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = SETTINGS.with_name(f"settings.json.bak-{stamp}")
        shutil.copy2(SETTINGS, backup)
        print(f"backed up -> {backup}")
    else:
        data = {}

    hooks = strip_ours(dict(data.get("hooks", {})))

    if not remove:
        python, script = args[0], args[1]
        for event in EVENTS:
            cmd = f'"{python}" "{script}" hook {event} --hook-ack'
            hooks.setdefault(event, []).append(
                {"hooks": [{"type": "command", "command": cmd}]})

    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)

    rendered = json.dumps(data, indent=2) + "\n"
    json.loads(rendered)                       # parse-check before it lands on disk
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(rendered, encoding="utf-8")
    tmp.replace(SETTINGS)

    print(("removed" if remove else "installed") + f" pet hooks in {SETTINGS}")
    print("events configured:", ", ".join(sorted(data.get("hooks", {}))) or "(none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
