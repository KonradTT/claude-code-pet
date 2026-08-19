#!/usr/bin/env python3
"""Optional integration with herdr, the terminal workspace manager.

Claude sessions here run as herdr panes rather than in a terminal window, so
"jump to that session" means focusing a herdr agent, not raising an OS window.
Everything in this module degrades to empty/False when herdr is absent, so the
pet works exactly as before without it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

TIMEOUT = 2.0
# herdr's agent_status -> the pet's state vocabulary
STATUS = {"working": "running", "blocked": "needs_input",
          "idle": "idle", "error": "blocked"}


def _binary() -> str | None:
    found = shutil.which("herdr")
    if found:
        return found
    local = Path.home() / ".local/bin/herdr"
    return str(local) if local.exists() else None


def available() -> bool:
    return _binary() is not None


def _run(*args) -> dict | None:
    exe = _binary()
    if not exe:
        return None
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True,
                           timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def agents() -> dict[str, dict]:
    """Live Claude agents keyed by their Claude session id."""
    data = _run("agent", "list")
    if not data:
        return {}
    out = {}
    for a in (data.get("result", {}) or {}).get("agents", []):
        if a.get("agent") != "claude":
            continue
        sid = (a.get("agent_session") or {}).get("value")
        if not sid:
            continue
        out[sid] = {
            "pane_id": a.get("pane_id"),
            "tab_id": a.get("tab_id"),
            "title": a.get("terminal_title_stripped") or a.get("terminal_title") or "",
            "state": STATUS.get(a.get("agent_status", "idle"), "idle"),
            "focused": bool(a.get("focused")),
            "cwd": a.get("cwd", ""),
        }
    return out


def focus(session_id: str) -> bool:
    """Bring the pane running that Claude session to the front."""
    info = agents().get(session_id)
    if not info or not info.get("pane_id"):
        return False
    exe = _binary()
    try:
        r = subprocess.run([exe, "agent", "focus", info["pane_id"]],
                           capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "focus":
        print("focused" if focus(sys.argv[2]) else "could not focus that session")
    else:
        found = agents()
        print(f"herdr available: {available()}   claude agents: {len(found)}")
        for sid, a in found.items():
            print(f"  {sid[:20]}… {a['state']:<11} {a['pane_id']:8s} "
                  f"{'[focused]' if a['focused'] else '         '} {a['title'][:38]!r}")
