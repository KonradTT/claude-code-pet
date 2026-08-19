# Engine notes

Hard-won behaviour of the overlay itself. Read this before changing
`pet_overlay.py`, `petctl.py` or `merge_hooks.py` — most of it was found by things
breaking, not by reading docs.

## macOS window traps

**Tool windows hide themselves.** Qt marks `Qt.Tool` windows `hidesOnDeactivate`, so the
pet vanishes whenever its own process is not frontmost — which is always, since you work
in a terminal. It reports `on_screen: true` the whole time. The fix is
`WA_MacAlwaysShowToolWindow`. Without it the pet is simply invisible and nothing in the
logs says so.

**Qt re-anchors windows across mixed-DPI displays.** With a Retina laptop (2×) beside 1×
externals, a frameless tool window moved onto one display lands somewhere else a frame
later — measured drift of 1420px. The overlay stores `desired_pos` and re-asserts it every
tick unless the user is dragging. Do not remove that.

**Screen capture is blocked**, so the desktop cannot be screenshotted for verification.
Render the widget's own paint path offscreen with `widget.grab()` instead — that exercises
the real `paintEvent`, scaling and mask code.

**Full-screen Spaces** can still hide the overlay. That one is not solved.

## The window mask

The window is masked to the sprite so clicks in the empty corners pass through. Three
rules, each learned by breaking it:

1. **Mask the union of every frame, not the current one.** A mask built from one frame
   clips the next — a lifted leg or raised claw falls outside it.
2. **Re-apply the mask whenever the visible furniture changes.** Hover controls and the
   task card live outside the sprite silhouette; if the mask is not recomputed when they
   appear, they are painted into a hole and are invisible. This is exactly the bug that
   made the hover button never show.
3. **Keep a corridor between separated elements.** The control button sits below the pet
   with transparent space between. Without a masked corridor the cursor leaves the window
   on the way down, `leaveEvent` fires, and the button disappears before it can be
   clicked.

Verify with region arithmetic rather than by eye: for every frame, size and facing, assert
`QRegion(frame_mask).translated(pos).subtracted(window_mask).isEmpty()`.

## One instance only

Both the LaunchAgent and `bin/pet` can start the overlay, and they will race — two pets
fighting over position and state. `pet_overlay.py` takes an exclusive `flock` on
`~/.assistant-pet/overlay.lock` at startup and exits quietly if it cannot.

## The hook contract

Claude Code sends each hook a JSON payload on **stdin** and expects a prompt, non-blocking
response. Notable fields: `session_id`, `hook_event_name`, `prompt` (UserPromptSubmit),
`tool_name` + `tool_input` (Pre/PostToolUse), `message` (Notification).

- **Never block.** `PreToolUse` fires on every tool call. Read stdin behind a
  `select(..., 0.25)` so a silent or absent pipe returns immediately. Test with closed,
  empty, garbage and never-written stdin.
- **Stay cheap.** ~30 ms per invocation measured. Keep imports light.
- **Hooks apply without restarting Claude Code.** They were observed relaying the live
  session moments after `install.sh` ran.
- `merge_hooks.py` must be idempotent (strip its own entries before re-adding), back up
  first, preserve unrelated hooks, and refuse to touch a settings file that is not already
  valid JSON.

## Per-session state

`state.json` holds `tasks` keyed by `session_id`, one entry per live session, each with
`state`, `title` and `subtitle`. The aggregate the pet displays is the highest-priority
task: `needs_input > blocked > ready > running > idle`.

**The empty case is the one that breaks.** Removing the last task is the path least
exercised by testing and is where a `NameError` hid. Always test the full lifecycle down
to zero sessions.

**Hooks only ever add.** `SessionEnd` is the only thing that removes a task, and it does
not run when a session is killed rather than closed — closing a herdr tab, or any
`SIGKILL`, takes the process down with no chance to fire it. Left to itself the map grows
without limit and the list never shrinks: the row does not vanish, it loses the pane and
title herdr was supplying and falls through to the generic `"Working"` fallback, which
reads as a phantom session.

`sessions.py` fixes that by reconciling against Claude Code's own registry —
`~/.claude/sessions/<pid>.json`, one file per running session, carrying `sessionId`,
`pid` and a human `name`. A session id absent from it, whose pid is gone, is dead. Three
rules keep that safe: an unreadable or missing registry means *cannot tell*, so nothing is
reaped; ids that are not Claude-shaped UUIDs (`pet session start` invents `manual-<pid>`)
are never candidates; and a hook never reaps the session it is currently writing for.

A row must also **earn** its place: a paneless, unnamed, `idle` session has nothing to
show. Judging that on the state rather than on the subtitle matters, because
`SessionStart` used to write a placeholder `"Ready"` subtitle that gave every bare session
a row for free.

## What goes on the card

The title comes from the prompt and persists for the turn; the subtitle is the step in
flight. Turning a tool call into a line:

- `Bash` → use `tool_input.description`, **never** the raw command, which may hold secrets
- file tools → **basename only**, never the full path
- everything collapsed to one line and length-capped

`pet privacy on` replaces the title with "Working" and subtitles with generic phrases, for
when nothing from a prompt should be written to disk at all.

## Jumping to a session (herdr)

Sessions on this machine do not live in a terminal window. They are panes inside
**herdr**, a terminal workspace manager, spawned straight from launchd — no `.app`, no
entry in the System Events window list, and Terminal.app holds only one unrelated tab. So
there is no OS window to raise, and AppleScript tty-matching against Terminal or iTerm
finds nothing. Check what actually hosts a session by walking the process tree up from the
shell before assuming a terminal emulator.

What works instead: `herdr agent list` emits JSON in which every pane carries
`agent_session.value` — **the Claude session id**, the same one the hooks report. That
makes the mapping exact rather than heuristic. `herdr agent focus <pane_id>` then switches
to it, verified by focusing another pane, confirming `focused` moved, and focusing back.

`herdr_link.py` keeps this optional: every function degrades to empty or `False` when the
binary is absent, so the overlay is unchanged without it. Poll it **off the UI thread** —
it shells out, and a ~30 ms subprocess on a 25 fps timer stutters.

Background jobs are their own Claude sessions with their own ids, fire hooks normally, and
have **no pane at all**. Show them, but do not pretend they are reachable.

## Testing methods that actually caught things

- **Pixel-diff a region across frames** — proved "feet planted" objectively where the eye
  could not.
- **Region subtraction over every frame × size × facing** — caught mask clipping that only
  showed up mid-animation, and a mirrored-mask misalignment.
- **Deliberately broken fixtures** — inject a wandering baseline and an opaque background,
  and assert the validator flags them. A validator that has never failed proves nothing.
- **`SIGKILL` a real session** — the only honest test of reaping. A tab close may still let
  `SessionEnd` run, so it cannot tell you whether the reaper works; `kill -9` proves no hook
  fired, and the row must still go.
- **Synthetic Qt events** — construct `QMouseEvent` press/release to test that a button
  toggles and that clicking the body still drags.
- **Restore what you disturb.** Verifying focus meant actually moving the user's focused
  pane; capture what was focused first, switch, assert, and switch back.
- **Verbatim command replay** — run the exact command strings written into `settings.json`
  rather than an approximation, so path and quoting errors surface.
