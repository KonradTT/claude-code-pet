---
name: hatch-pet
description: Create a new animated desktop pet for the Claude Code overlay, or import an existing one. Use when the user asks to hatch, create, make, design or add a pet, wants a new companion/mascot/character on their screen, asks for a different pet, or wants to import a pet from Codex. With no description given, design one from what is known about the user's interests.
---

# Hatch a desktop pet

Creates a character for the Claude Code desktop pet overlay, installs it into the pet
library, and activates it. The overlay already exists — this adds a pet to it.

## What the pet does, so you know what you are hatching into

An always-on-top companion that appears while Claude Code is running. It shows a **task
card** — the prompt as a title, the current tool call as a subtitle — and plays an
animation matching what Claude is doing: thinking while working, shrugging when it needs
permission, celebrating when a turn finishes. Hovering it reveals a badge counting live
sessions; clicking that lists them, and clicking a row jumps to that session. It walks
when dragged and can be resized and toggled.

A pet is **11 animations**, one per row of a spritesheet.

## Locating the engine

```bash
ENGINE=$(cat ~/.assistant-pet/engine 2>/dev/null || echo ~/Documents/Claude/Tools/claude-pet)
PY="$ENGINE/.venv/bin/python"
HATCH="$ENGINE/hatch.py"
```

The pet library defaults to `~/Documents/Claude/Pets/<id>/`, overridable with
`$CLAUDE_PETS_DIR`. `pet pets` lists what is installed.

## The one hard constraint

**Claude Code cannot generate images.** Artwork comes from an image generator — Codex,
ChatGPT, or whatever the user prefers. Everything else is automated: the brief, the grid
detection, the slicing, the validation, the install, the activation.

Never claim to have drawn the artwork. Never fabricate a spritesheet. Say plainly that the
artwork step is theirs.

## Route A — import something that exists

Fastest, and how the reference pet `mumu` was made.

```bash
$PY $HATCH codex <codex-pet-name> --id <id> --name "<Name>" --desc "<one line>"
$PY $HATCH import ~/Downloads/sheet.png --id <id> --name "<Name>" --desc "<one line>"
```

The grid is detected from the transparent gutters; pass `--cols N --rows N` if that comes
out wrong. Then validate and activate — **do not skip the check**:

```bash
$PY $HATCH check <id>
pet use <id>
pet status
```

## Route B — design a new one

### 1. Decide the character

If the user described what they want, use it.

**If they did not, design from what is actually known about them** — this is the default
and it should be specific. Read, in order:

1. the auto-memory index for this project, `~/.claude/projects/<project>/memory/MEMORY.md`,
   and the memory files it points at
2. the project's `CLAUDE.md`
3. `$PY $HATCH list` — so the new one is not a retread

Pull out concrete hooks: what they build, their stack, recurring themes, enthusiasms.
Propose **2–3 distinct concepts**, one sentence each, say which you would pick and why,
and wait for them to choose. The artwork step costs them real effort — do not burn it on a
guess.

A usable concept names: what the creature or object is, its silhouette, its palette, one
memorable feature, and its resting expression. Vague concepts produce vague sheets.

### 2. Emit the brief

```bash
$PY $HATCH prompt --id <id> --name "<Name>" --desc "<the agreed character>"
```

Prints a ready-to-paste brief: exact grid, row order, frame counts, and the constraints
that matter. Hand it over, ask them to run it through an image generator and say where the
file landed.

### 3. Import, validate, activate

Same commands as Route A. **Read what `check` says** — see
[`references/spritesheet-contract.md`](references/spritesheet-contract.md) for what each
problem means and how to fix it. In short:

- `feet move Npx between frames` — the classic generated-sheet flaw; the pet bobs while
  standing. Regenerate that row with the baseline requirement emphasised.
- `frames do not share a baseline` — rows disagree; it jumps between animations.
- `background is not transparent` — needs a background-removal pass first.
- `walk rows mirror-match: 0.7` — informational; that is normal. Only < 0.35 is odd.

## Walk direction: expect to get it wrong

Which walk row reads as "right" **cannot be judged reliably by eye** from a three-quarter
character. It was got wrong in both directions while building `mumu` before being settled
by dragging it on screen.

So do not deliberate. Import, drag it, and if it walks backwards either right-click →
**Reverse walking direction**, or swap the two names in the `ROWS` table in `hatch.py` and
re-import. Tell the user this up front so it reads as expected rather than as a defect.

## Verify before declaring success

Never report a pet as working because the import printed no error.

```bash
$PY $HATCH check <id>        # baseline spread, transparency, empty frames
pet use <id> && pet status   # confirm the overlay switched and is on screen
pet wave                     # confirm a one-shot plays and hands back
```

Then hover it, drag it both ways, and look. The checks catch structural faults; character
drift and a wrong-looking pose are only visible to a human.

## What to report back

Name and id, where it landed, animation and frame counts, the baseline spread, anything
that fell back to `idle`, and that it is now active. Mention `pet use mumu` to go back.

## Going deeper

- [`references/spritesheet-contract.md`](references/spritesheet-contract.md) — the sheet
  spec, the row table, every failure mode and its fix, and why the mirror check is advisory.
- [`references/engine-notes.md`](references/engine-notes.md) — read before touching the
  overlay: the macOS tool-window and multi-monitor traps, the three window-mask rules, the
  single-instance lock, the hook contract, per-session state, jumping to a session, and the
  testing methods that actually caught bugs.
