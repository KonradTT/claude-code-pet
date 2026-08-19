#!/usr/bin/env python3
"""Drive the Claude pet overlay: activity state, visibility, size, session count."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from contextlib import contextmanager
import time
from datetime import datetime, timezone
from pathlib import Path

STATES = ("idle", "running", "needs_input", "ready", "blocked")
DEFAULT_MSG = {
    "idle": "Waiting for a task", "running": "Working…",
    "needs_input": "Needs your input", "ready": "Task ready",
    "blocked": "Something went wrong",
}
MIN_H, MAX_H = 48, 400


def pet_home() -> Path:
    configured = os.environ.get("ASSISTANT_PET_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".assistant-pet"


@contextmanager
def locked(home: Path):
    """Serialise read-modify-write so concurrent sessions don't lose a count."""
    home.mkdir(parents=True, exist_ok=True)
    fd = os.open(home / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)          # atomic: the overlay never sees a half-written file


MAX_TITLE, MAX_SUB = 64, 72
# Which session's state wins when several run at once (highest first).
PRIORITY = ["needs_input", "blocked", "ready", "running", "idle"]


def clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())          # collapse newlines/runs
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def describe_tool(name: str, inp: dict, private: bool) -> str:
    """One short human line for what Claude is doing right now."""
    if private:
        return {"Bash": "Running a command", "Read": "Reading a file",
                "Edit": "Editing a file", "Write": "Writing a file",
                "Grep": "Searching", "Glob": "Searching",
                "WebFetch": "Fetching a page", "WebSearch": "Searching the web",
                }.get(name, "Working")
    inp = inp if isinstance(inp, dict) else {}
    base = lambda p: str(p).rstrip("/").split("/")[-1] or str(p)
    if name == "Bash":
        # tool_input.description is a written summary; the raw command may hold secrets
        d = inp.get("description")
        return clip(f"Running {d[0].lower() + d[1:]}" if d else
                    f"Running {str(inp.get('command','')).split()[0] if inp.get('command') else 'a command'}",
                    MAX_SUB)
    if name in ("Read", "NotebookEdit"):
        return clip(f"Reading {base(inp.get('file_path','a file'))}", MAX_SUB)
    if name in ("Edit", "Write"):
        return clip(f"Editing {base(inp.get('file_path','a file'))}", MAX_SUB)
    if name in ("Grep", "Glob"):
        return clip(f"Searching for {inp.get('pattern','something')}", MAX_SUB)
    if name in ("WebFetch",):
        url = str(inp.get("url", ""))
        host = url.split("//")[-1].split("/")[0] if url else "the web"
        return clip(f"Fetching {host}", MAX_SUB)
    if name == "WebSearch":
        return clip(f"Searching for {inp.get('query','something')}", MAX_SUB)
    if name in ("Task", "Agent"):
        return clip(inp.get("description") or "Running a subagent", MAX_SUB)
    if name == "TodoWrite":
        return "Updating the plan"
    return clip(f"Using {name}" if name else "Working", MAX_SUB)


def read_hook_input() -> dict:
    """Claude Code sends hook payloads as JSON on stdin. Never block or throw -
    this runs on every tool call, so a hang here would stall the session."""
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    try:
        import select
        ready, _, _ = select.select([sys.stdin], [], [], 0.25)
        if not ready:
            return {}
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def set_state(home: Path, state: str, message: str | None, source: str) -> None:
    with locked(home):
        data = read(home / "state.json")
        data.update({
            "state": state,
            "message": message or DEFAULT_MSG[state],
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sessions": int(data.get("sessions", 0) or 0),
        })
        write(home / "state.json", data)


def recompute(data: dict) -> dict:
    """Roll the per-session tasks up into the aggregate the overlay draws."""
    tasks = data.get("tasks") or {}
    data["sessions"] = len(tasks)
    if not tasks:
        data.update({"state": "idle", "title": "", "subtitle": "",
                     "message": DEFAULT_MSG["idle"]})
        return data
    best = min(tasks.values(),
               key=lambda t: PRIORITY.index(t.get("state", "idle"))
               if t.get("state", "idle") in PRIORITY else len(PRIORITY))
    data.update({
        "state": best.get("state", "idle"),
        "title": best.get("title", ""),
        "subtitle": best.get("subtitle", ""),
        "message": best.get("subtitle") or DEFAULT_MSG.get(
            best.get("state", "idle"), ""),
    })
    return data


def update_task(home: Path, sid: str, state=None, title=None, subtitle=None,
                remove: bool = False, source: str = "claude") -> None:
    """One entry per live Claude session, so the pet can show how many are running."""
    sid = sid or "default"
    with locked(home):
        path = home / "state.json"
        data = read(path)
        tasks = data.get("tasks")
        if not isinstance(tasks, dict):
            tasks = {}
        if remove:
            tasks.pop(sid, None)
        else:
            t = tasks.get(sid) or {"state": "idle", "title": "", "subtitle": ""}
            if state is not None:
                t["state"] = state
            if title is not None:
                t["title"] = clip(title, MAX_TITLE)
            if subtitle is not None:
                t["subtitle"] = clip(subtitle, MAX_SUB)
            t["updated_at"] = datetime.now(timezone.utc).isoformat()
            tasks[sid] = t
        data["tasks"] = tasks
        data["source"] = source
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        write(path, recompute(data))


def set_activity(home: Path, state: str, title=None, subtitle=None,
                 source: str = "claude") -> None:
    """State plus the task card: a title that persists across the turn and a
    subtitle for the step in flight."""
    with locked(home):
        path = home / "state.json"
        data = read(path)
        if title is not None:
            data["title"] = clip(title, MAX_TITLE)
        if subtitle is not None:
            data["subtitle"] = clip(subtitle, MAX_SUB)
        data.update({
            "state": state,
            "message": data.get("subtitle") or DEFAULT_MSG[state],
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sessions": int(data.get("sessions", 0) or 0),
        })
        write(path, data)


def bump_sessions(home: Path, delta: int, source: str) -> int:
    with locked(home):
        data = read(home / "state.json")
        count = max(0, int(data.get("sessions", 0) or 0) + delta)
        data.update({
            "sessions": count,
            "state": data.get("state", "idle") if count else "idle",
            "message": data.get("message", DEFAULT_MSG["idle"]) if count
                       else DEFAULT_MSG["idle"],
            "source": source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        write(home / "state.json", data)
        return count


def patch_config(home: Path, **changes) -> dict:
    with locked(home):
        cfg = read(home / "config.json")
        cfg.update(changes)
        write(home / "config.json", cfg)
        return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command",
                    help="on | off | auto | toggle | size <px> | center [n] | screens | "
                         "pets | use <id> | sessions | focus <id> | animations | play <name> | "
                         "wave | jump | "
                         "hook <event> | privacy on|off | "
                         "celebrate | status | session start|end | "
                         "a state (%s)" % "|".join(STATES))
    ap.add_argument("value", nargs="?", help="message, pixel height, or start/end")
    ap.add_argument("--source", default="manual")
    ap.add_argument("--hook-ack", action="store_true",
                    help="print an empty JSON object so lifecycle hooks stay non-blocking")
    args = ap.parse_args()

    home = pet_home()
    cmd = args.command

    if cmd in STATES:
        set_state(home, cmd, args.value, args.source)
    elif cmd in ("on", "show"):
        # Explicitly on means on - visible whether or not a session is open.
        patch_config(home, enabled=True, always=True)
    elif cmd in ("off", "hide"):
        patch_config(home, enabled=False)
    elif cmd == "auto":
        patch_config(home, enabled=True, always=False)
    elif cmd == "toggle":
        cfg = read(home / "config.json")
        if bool(cfg.get("enabled", True)):
            patch_config(home, enabled=False)
            print("pet: off")
        else:
            patch_config(home, enabled=True, always=True)
            print("pet: on")
    elif cmd == "size":
        if not args.value or not args.value.lstrip("-").isdigit():
            print("size needs a pixel height, e.g. petctl.py size 180", file=sys.stderr)
            return 2
        patch_config(home, height=max(MIN_H, min(MAX_H, int(args.value))))
    elif cmd in ("play", "wave", "jump", "celebrate", "shrug", "angry"):
        # `pet wave` is shorthand for `pet play wave`
        name = args.value if cmd == "play" else cmd
        if not name:
            print("play needs an animation name", file=sys.stderr); return 2
        patch_config(home, play=name, play_id=time.time_ns())
    elif cmd == "hook":
        event = args.value or ""
        payload = read_hook_input()
        cfg = read(home / "config.json")
        private = bool(cfg.get("privacy", False))
        tool = payload.get("tool_name", "")
        inp = payload.get("tool_input", {})
        sid = str(payload.get("session_id") or "default")
        if event == "SessionStart":
            update_task(home, sid, state="idle", title="", subtitle="Ready")
        elif event == "SessionEnd":
            update_task(home, sid, remove=True)
        elif event == "UserPromptSubmit":
            prompt = "" if private else payload.get("prompt", "")
            update_task(home, sid, state="running",
                        title=clip(prompt, MAX_TITLE) or "Working",
                        subtitle="Getting started")
        elif event in ("PreToolUse", "PostToolUse"):
            update_task(home, sid, state="running",
                        subtitle=describe_tool(tool, inp, private))
        elif event in ("Notification", "PermissionRequest"):
            msg = "" if private else payload.get("message", "")
            update_task(home, sid, state="needs_input",
                        subtitle=clip(msg, MAX_SUB) or "Waiting for you")
        elif event == "Stop":
            update_task(home, sid, state="ready", subtitle="Done")
        else:
            update_task(home, sid, state="running",
                        subtitle=describe_tool(tool, inp, private))
    elif cmd == "privacy":
        cfg = read(home / "config.json")
        on = args.value if args.value in ("on", "off") else (
            "off" if cfg.get("privacy") else "on")
        patch_config(home, privacy=(on == "on"))
        print(f"privacy: {on}" + (" (generic messages only)" if on == "on" else ""))
    elif cmd == "sessions":
        import sys as _s, pathlib as _p
        _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
        try:
            import herdr_link
        except ImportError:
            herdr_link = None
        agents = herdr_link.agents() if herdr_link and herdr_link.available() else {}
        tasks = read(home / "state.json").get("tasks") or {}
        seen = set()
        for sid, a in agents.items():
            t2 = tasks.get(sid, {})
            seen.add(sid)
            print(f'  {"*" if a["focused"] else " "} {a["pane_id"]:8s} '
                  f'{a["state"]:<11} {(t2.get("title") or a["title"])[:44]}')
        for sid, t2 in tasks.items():
            if sid not in seen:
                print(f'    {"(no pane)":8s} {t2.get("state","idle"):<11} '
                      f'{(t2.get("title") or "background job")[:44]}')
        if not agents and not tasks:
            print("  no live sessions")
        elif not agents:
            print("\n  herdr not available - sessions cannot be focused")
        else:
            print("\n  focus one with:  pet focus <pane-id>")
    elif cmd == "focus":
        import sys as _s, pathlib as _p
        _s.path.insert(0, str(_p.Path(__file__).resolve().parent))
        try:
            import herdr_link
        except ImportError:
            print("herdr integration unavailable", file=sys.stderr); return 1
        if not args.value:
            print("focus needs a pane id or session id (see: pet sessions)",
                  file=sys.stderr); return 2
        agents = herdr_link.agents()
        sid = args.value
        if sid not in agents:
            sid = next((k for k, a in agents.items()
                        if a["pane_id"] == args.value), None)
        if not sid:
            print(f"no session matching {args.value!r}", file=sys.stderr); return 1
        print("focused" if herdr_link.focus(sid) else "could not focus that session")
    elif cmd == "pets":
        rt = read(home / "runtime.json")
        cur = read(home / "config.json").get("pet", "mumu")
        import pathlib as _p
        root = _p.Path(os.environ.get("CLAUDE_PETS_DIR",
                       _p.Path.home() / "Documents/Claude/Pets")).expanduser()
        found = sorted(d for d in root.glob("*/pet.json"))
        if not found:
            print(f"no pets in {root} - hatch one with the hatch-pet skill",
                  file=sys.stderr); return 1
        for f in found:
            meta = read(f)
            pid = meta.get("id", f.parent.name)
            n = len(meta.get("animations") or {})
            mark = "  <- active" if pid == cur else ""
            print(f'  {pid:14s} {meta.get("displayName", pid):20s} '
                  f'{n:2d} animations{mark}')
    elif cmd == "use":
        if not args.value:
            print("use needs a pet id (see: petctl.py pets)", file=sys.stderr); return 2
        import pathlib as _p
        root = _p.Path(os.environ.get("CLAUDE_PETS_DIR",
                       _p.Path.home() / "Documents/Claude/Pets")).expanduser()
        if not (root / args.value / "pet.json").is_file():
            print(f"no pet named {args.value!r} in {root}", file=sys.stderr); return 1
        patch_config(home, pet=args.value)
        print(f"pet: {args.value}")
    elif cmd == "animations":
        import pathlib as _p
        cur = read(home / "config.json").get("pet", "mumu")
        root = _p.Path(os.environ.get("CLAUDE_PETS_DIR",
                       _p.Path.home() / "Documents/Claude/Pets")).expanduser()
        meta = read(root / cur / "pet.json")
        data = meta.get("animations") or {}
        if not data:
            print(f"no animations for pet {cur!r}", file=sys.stderr); return 1
        print(f'  {meta.get("displayName", cur)}:')
        for n, spec in data.items():
            print(f'  {n:11s} {spec["frames"]:2d} frames  {spec["ms"]:3d}ms  '
                  f'{"loop" if spec.get("loop") else "once"}')
    elif cmd == "center":
        idx = int(args.value) if args.value and args.value.lstrip("-").isdigit() else None
        patch_config(home, recenter=True, recenter_screen=idx, enabled=True)
    elif cmd == "screens":
        rt = read(home / "runtime.json")
        if not rt.get("screens"):
            print("overlay not running - start it first", file=sys.stderr); return 1
        for sc in rt["screens"]:
            mark = "  <- pet is here" if sc.get("has_pet") else ""
            print(f'  [{sc["index"]}] {sc["name"]:<26} {sc["geometry"]}{mark}')
        print("\n  move him:  petctl.py center <index>")
    elif cmd == "always":
        cfg = read(home / "config.json")
        patch_config(home, always=not bool(cfg.get("always", False)))
    elif cmd == "session":
        if args.value not in ("start", "end"):
            print("session needs start or end", file=sys.stderr)
            return 2
        if args.value == "start":
            update_task(home, f"manual-{os.getpid()}", state="idle",
                        subtitle="Ready", source=args.source)
        else:
            with locked(home):
                path = home / "state.json"
                data = read(path)
                tasks = data.get("tasks") or {}
                manual = [k for k in tasks if k.startswith("manual-")]
                if manual:
                    tasks.pop(manual[-1])
                elif tasks:
                    tasks.pop(next(iter(tasks)))
                data["tasks"] = tasks
                write(path, recompute(data))
    elif cmd == "status":
        state, cfg = read(home / "state.json"), read(home / "config.json")
        runtime = read(home / "runtime.json")
        wanted = bool(cfg.get("enabled", True)) and (
            bool(cfg.get("always", False)) or int(state.get("sessions", 0) or 0) > 0)
        print(json.dumps({"wanted_on_screen": wanted, "config": cfg,
                          "state": state, "overlay": runtime or "not running"}, indent=2))
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 2

    if args.hook_ack:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
