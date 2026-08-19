#!/usr/bin/env python3
"""Which Claude sessions are still alive, and what to call them.

The pet learns about sessions from lifecycle hooks, and hooks only ever *add*:
``SessionEnd`` is the sole reaper and it does not run when a session is killed
rather than closed - shutting a herdr tab takes the pane down with a signal
Claude Code never gets to handle. Left alone the task map grows forever, so a
closed tab does not remove its row; it only loses the pane and title herdr was
supplying and falls back to a generic "Working" line.

Claude Code keeps its own registry of running sessions - one small JSON file per
process under ``~/.claude/sessions`` - so that is the ground truth we reconcile
against. Everything here degrades to "cannot tell, change nothing" when the
registry is missing, so the pet still works on a machine that has no such thing.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# Hook payloads key tasks by Claude's session id. `pet session start` invents
# ids of its own ("manual-1234") and no registry knows about those, so only
# ids in Claude's own shape are ever candidates for reaping.
SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                        r"[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def registry_dir() -> Path:
    """Where Claude Code records its running sessions."""
    home = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(home).expanduser() if home else Path.home() / ".claude"
    return base / "sessions"


def _running(pid: int) -> bool:
    """Signal 0 asks the kernel about a pid without touching the process."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True          # someone else's process, but a process nonetheless
    return True


def live() -> dict[str, str] | None:
    """Session id -> the name Claude Code gave it, for every running session.

    Returns None when the registry cannot be read at all. That is deliberately
    distinct from an empty dict: "no sessions are alive" reaps everything,
    "I cannot tell" must reap nothing.
    """
    directory = registry_dir()
    if not directory.is_dir():
        return None
    try:
        files = list(directory.glob("*.json"))
    except OSError:
        return None
    found: dict[str, str] = {}
    readable = False
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        readable = True
        sid, pid = data.get("sessionId"), data.get("pid")
        if not isinstance(sid, str) or not isinstance(pid, int):
            continue
        if _running(pid):
            found[sid] = str(data.get("name") or "")
    # A directory full of files we could not parse is a registry we do not
    # understand - safer to claim ignorance than to reap on a bad reading.
    if files and not readable:
        return None
    return found


def prune(tasks: dict, alive: dict[str, str] | None,
          keep: set[str] | None = None) -> dict:
    """Drop tasks whose Claude session is no longer running.

    `keep` protects ids we must not touch - above all the session whose hook is
    running right now, which would otherwise delete the entry it just wrote if
    this machine's registry does not list it.
    """
    if alive is None:
        return dict(tasks)
    protected = keep or set()
    return {sid: t for sid, t in tasks.items()
            if sid in alive or sid in protected or not SESSION_ID.match(sid)}


def _name_for(sid: str, registry_name: str) -> str:
    """A registry name worth showing, or "" when it is just an id in disguise."""
    name = (registry_name or "").strip()
    if not name or sid.startswith(name) or name == sid:
        return ""
    return name


def merge_rows(herdr_agents: dict, tasks: dict, alive: dict[str, str] | None,
               max_rows: int | None = None) -> list[tuple[str, dict]]:
    """Every live Claude session as one row: herdr's panes plus hook-only ones.

    herdr knows about sessions that started before the hooks existed and carries
    the pane id needed to focus them; the hooks carry the richer title and the
    current step. Merge, preferring hook text, then drop anything that is not
    running any more.
    """
    merged: dict[str, dict] = {}
    for sid, a in herdr_agents.items():
        merged[sid] = {"state": a.get("state", "idle"), "title": a.get("title", ""),
                       "subtitle": "", "pane": a.get("pane_id"),
                       "focused": a.get("focused", False)}
    for sid, t in prune(tasks, alive).items():
        e = merged.setdefault(sid, {"pane": None, "focused": False})
        e["state"] = t.get("state", e.get("state", "idle"))
        e["title"] = t.get("title") or e.get("title") or ""
        e["subtitle"] = t.get("subtitle") or ""
    # A hook-only session that is idle and unnamed has nothing to show and
    # nowhere to go - drop it rather than pad the list with blanks. Judging that
    # on the state rather than on the subtitle also clears the placeholder rows
    # written by earlier versions.
    rows = [(sid, e) for sid, e in merged.items()
            if e.get("pane") or e.get("title") or e.get("state", "idle") != "idle"]
    # Only now, for rows that earned their place, borrow a name from the
    # registry - so a job that outlived its herdr tab still says which job it is
    # instead of falling back to the generic "Working".
    for sid, e in rows:
        if not e.get("title"):
            e["title"] = _name_for(sid, (alive or {}).get(sid, ""))
    rows.sort(key=lambda kv: (not kv[1].get("pane"), kv[1].get("title", "")))
    return rows if max_rows is None else rows[:max_rows]


if __name__ == "__main__":
    found = live()
    if found is None:
        print(f"no session registry at {registry_dir()}")
    else:
        print(f"{len(found)} live session(s) in {registry_dir()}")
        for sid, name in sorted(found.items(), key=lambda kv: kv[1]):
            print(f"  {sid}  {name!r}")
