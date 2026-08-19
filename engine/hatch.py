#!/usr/bin/env python3
"""Hatch a desktop pet from a spritesheet.

    hatch.py prompt  --id <id> --name "<Name>" --desc "<character>"
    hatch.py import  <sheet.png|webp> --id <id> --name "<Name>" [--desc ...]
    hatch.py codex   <codex-pet-name>  --id <id> --name "<Name>"
    hatch.py check   <id>
    hatch.py list

The sheet layout is one animation per ROW, frames left-packed along each row,
every frame the same cell size, with the character's feet on a consistent
baseline. The grid is detected from the transparent gutters; pass --cols/--rows
if detection is wrong.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from PIL import Image

# The animation set the overlay understands. Order IS the row order.
ROWS = [
    ("idle",       True,  190, "standing still, small breathing motion, feet flat"),
    ("walk_right", True,   95, "walking to the right, seen from the side/three-quarter"),
    ("walk_left",  True,   95, "walking to the left, the mirror of the row above"),
    ("wave",       True,  150, "raising one limb and waving hello"),
    ("jump",       False,  90, "a single hop: crouch, launch, airborne, land"),
    ("celebrate",  False, 110, "a happy celebration, both limbs up"),
    ("shrug",      True,  140, "a questioning shrug, limbs out, asking"),
    ("think",      True,  160, "pondering, one limb to the head, looking up"),
    ("angry",      True,  150, "annoyed or frustrated, brow down, tense"),
    ("turn_out",   False,  55, "turning away from the viewer until facing fully away"),
    ("turn_in",    False,  55, "turning back toward the viewer, the reverse of the row above"),
]
COLS = 8


def pets_dir() -> Path:
    """Library location: $CLAUDE_PETS_DIR, else the default beside the workspace."""
    configured = os.environ.get("CLAUDE_PETS_DIR")
    return (Path(configured).expanduser() if configured
            else Path.home() / "Documents/Claude/Pets")


def detect_grid(im: Image.Image, cols: int | None, rows: int | None):
    """Find the cell size from fully transparent gutters between content blocks."""
    a = im.getchannel("A")
    W, H = im.size
    px = a.load()
    col_has = [any(px[x, y] > 8 for y in range(H)) for x in range(W)]
    row_has = [any(px[x, y] > 8 for x in range(W)) for y in range(H)]

    def count_blocks(flags):
        n, prev = 0, False
        for f in flags:
            if f and not prev:
                n += 1
            prev = f
        return n

    c = cols or count_blocks(col_has)
    r = rows or count_blocks(row_has)
    if not c or not r or W % c or H % r:
        raise SystemExit(
            f"cannot split {W}x{H} into {c} x {r} whole cells - "
            f"pass --cols/--rows explicitly")
    return c, r, W // c, H // r


def frames_in_row(im: Image.Image, row: int, cols: int, cw: int, ch: int) -> int:
    a = im.getchannel("A")
    px = a.load()
    last = 0
    for c in range(cols):
        filled = 0
        for y in range(row * ch, (row + 1) * ch, 2):
            for x in range(c * cw, (c + 1) * cw, 2):
                if px[x, y] > 8:
                    filled += 1
                    if filled > 20:
                        break
            if filled > 20:
                break
        if filled > 20:
            last = c + 1
    return last


def baseline(im: Image.Image, row: int, col: int, cw: int, ch: int):
    a = im.getchannel("A"); px = a.load()
    for y in range(ch - 1, -1, -1):
        for x in range(cw):
            if px[col * cw + x, row * ch + y] > 8:
                return y
    return None


def do_import(sheet_path: Path, pet_id: str, name: str, desc: str,
              cols: int | None, rows: int | None, source: str) -> int:
    if not sheet_path.exists():
        print(f"no such sheet: {sheet_path}", file=sys.stderr)
        return 1
    im = Image.open(sheet_path).convert("RGBA")
    c, r, cw, ch = detect_grid(im, cols, rows)
    print(f"sheet {im.size[0]}x{im.size[1]} -> {c} cols x {r} rows, cell {cw}x{ch}")
    if r > len(ROWS):
        print(f"  note: sheet has {r} rows, only the first {len(ROWS)} are used")
    if r < len(ROWS):
        print(f"  note: sheet has {r} rows, expected {len(ROWS)}; "
              f"missing animations fall back to idle")

    dest = pets_dir() / pet_id
    frames_dir = dest / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)

    anims, baselines, problems = {}, [], []
    for row in range(min(r, len(ROWS))):
        anim, loop, ms, _ = ROWS[row]
        n = frames_in_row(im, row, c, cw, ch)
        if n == 0:
            problems.append(f"row {row} ({anim}) is empty")
            continue
        strip = Image.new("RGBA", (cw * n, ch), (0, 0, 0, 0))
        for i in range(n):
            strip.paste(im.crop((i * cw, row * ch, (i + 1) * cw, (row + 1) * ch)),
                        (i * cw, 0))
        strip.save(frames_dir / f"{anim}.png")
        anims[anim] = {"file": f"frames/{anim}.png", "frames": n,
                       "ms": ms, "loop": loop}
        for i in range(n):
            b = baseline(im, row, i, cw, ch)
            if b is not None:
                baselines.append(b)
        print(f"  {anim:11s} {n} frames")

    if not anims:
        print("no animations found in that sheet", file=sys.stderr)
        return 1

    spread = max(baselines) - min(baselines) if baselines else 0

    meta = {"id": pet_id, "displayName": name,
            "description": desc or f"A desktop pet called {name}.",
            "source": source, "cell": [cw, ch],
            "baseline": max(baselines) if baselines else None,
            "animations": anims}
    (dest / "pet.json").write_text(json.dumps(meta, indent=2) + "\n")

    print(f"\nhatched {name} -> {dest}")
    print(f"  {len(anims)} animations, {sum(a['frames'] for a in anims.values())} frames")
    problems += validate(dest, meta)
    for p in problems:
        print(f"  PROBLEM: {p}")
    if not problems:
        print("  no problems found")
    print(f"\n  switch to him with:  pet use {pet_id}")
    return 0


def do_prompt(pet_id: str, name: str, desc: str) -> int:
    cw, ch = 192, 208
    lines = [
        f"# Spritesheet brief for '{name}'",
        "",
        "Generate ONE transparent-background PNG spritesheet with this exact layout:",
        "",
        f"- Grid: {COLS} columns x {len(ROWS)} rows of {cw} x {ch} px cells "
        f"(total {COLS*cw} x {len(ROWS)*ch} px).",
        "- One animation per row, frames filled from the LEFT; leave unused cells "
        "fully transparent.",
        "- The character must be the SAME character in every frame: identical "
        "palette, proportions, line weight and rendering style.",
        "- Draw it at a consistent scale, horizontally centred in its cell, with the "
        "feet resting on the SAME baseline row in every single frame. This matters "
        "more than anything else - if the feet wander the pet bobs while standing.",
        "- Fully transparent background. No floor, no shadow, no text, no frame "
        "borders, no grid lines.",
        "",
        f"## The character",
        "",
        desc or "(describe the character here)",
        "",
        "## Rows, in order",
        "",
    ]
    for i, (anim, loop, _, note) in enumerate(ROWS):
        kind = "loops seamlessly" if loop else "plays once"
        lines.append(f"{i}. **{anim}** - {note}. 4-8 frames, {kind}.")
    lines += [
        "",
        "Rows 1 and 2 must be true mirrors of each other so the walk reads correctly "
        "in both directions. Rows 9 and 10 must be exact reverses of each other.",
        "",
        f"Save it, then run:  hatch.py import <file> --id {pet_id} --name \"{name}\"",
    ]
    print("\n".join(lines))
    return 0


def alpha_iou(a: Image.Image, b: Image.Image) -> float:
    from PIL import ImageChops
    aa = a.getchannel("A").point(lambda v: 255 if v > 24 else 0)
    bb = b.getchannel("A").point(lambda v: 255 if v > 24 else 0)
    inter = ImageChops.darker(aa, bb).point(lambda v: 1 if v else 0)
    union = ImageChops.lighter(aa, bb).point(lambda v: 1 if v else 0)
    si = sum(inter.histogram()[1:]); su = sum(union.histogram()[1:])
    return si / su if su else 0.0


def anim_frames(root: Path, spec: dict):
    im = Image.open(root / spec["file"]).convert("RGBA")
    n = max(1, int(spec.get("frames", 1)))
    w = im.width // n
    return [im.crop((i * w, 0, (i + 1) * w, im.height)) for i in range(n)]


def validate(root: Path, meta: dict) -> list[str]:
    """The checks that actually catch bad generated sheets.

    Calibrated against a known-good sheet: its walk rows are only ~0.7 IoU when
    mirrored, because they were drawn separately rather than flipped - so an
    exact-mirror test gives false alarms. Baseline drift and an opaque
    background are the failures worth failing on.
    """
    problems, notes = [], []
    anims = meta.get("animations") or {}
    all_baselines = []

    for name, spec in anims.items():
        frames = anim_frames(root, spec)
        baselines = []
        for f in frames:
            a = f.getchannel("A")
            bb = a.getbbox()
            if bb is None:
                problems.append(f"{name}: a frame is completely empty")
                continue
            baselines.append(bb[3])
        if not baselines:
            continue
        all_baselines += baselines
        spread = max(baselines) - min(baselines)
        # A standing animation whose feet move is the classic generated-sheet flaw
        if name in ("idle", "think", "shrug", "angry", "wave") and spread > 4:
            problems.append(f"{name}: feet move {spread}px between frames - "
                            f"he will bob while standing still")
        # background must be genuinely transparent
        first = frames[0]
        corners = [first.getpixel(p)[3] for p in
                   [(0, 0), (first.width - 1, 0), (0, first.height - 1),
                    (first.width - 1, first.height - 1)]]
        if max(corners) > 8:
            problems.append(f"{name}: background is not transparent "
                            f"(corner alpha {max(corners)}) - the sheet needs its "
                            f"background removed")

    if all_baselines:
        spread = max(all_baselines) - min(all_baselines)
        notes.append(f"baseline spread across every frame: {spread}px"
                     + ("" if spread <= 6 else "  <- too much"))
        if spread > 6:
            problems.append(f"frames do not share a baseline (varies {spread}px) - "
                            f"he will jump between animations")

    if "walk_left" in anims and "walk_right" in anims:
        L, R = anim_frames(root, anims["walk_left"]), anim_frames(root, anims["walk_right"])
        pairs = min(len(L), len(R))
        score = sum(alpha_iou(L[i], R[i].transpose(Image.FLIP_LEFT_RIGHT))
                    for i in range(pairs)) / max(1, pairs)
        notes.append(f"walk rows mirror-match: {score:.2f} "
                     f"(~0.7 is normal for hand-drawn pairs)")
        if score < 0.35:
            problems.append("the two walk rows do not look like a left/right pair - "
                            "check they are not two unrelated animations")
    for n in notes:
        print(f"  {n}")
    return problems


def do_check(pet_id: str) -> int:
    root = pets_dir() / pet_id
    meta_path = root / "pet.json"
    if not meta_path.exists():
        print(f"no pet {pet_id!r} in {pets_dir()}", file=sys.stderr)
        return 1
    meta = json.loads(meta_path.read_text())
    print(f"{meta.get('displayName', pet_id)}  ({pet_id})")
    print(f"  {meta.get('description','')}")
    ok = True
    total = 0
    for anim, spec in (meta.get("animations") or {}).items():
        f = root / spec["file"]
        if not f.exists():
            print(f"  MISSING {spec['file']}"); ok = False; continue
        im = Image.open(f)
        n = spec["frames"]
        good = im.width % n == 0
        total += n
        print(f"  {anim:11s} {n:2d} frames  {im.width}x{im.height}"
              f"{'' if good else '   BAD: width not divisible by frame count'}")
        ok &= good
    missing = [a for a, *_ in ROWS if a not in (meta.get("animations") or {})]
    if missing:
        print(f"  missing animations (will fall back to idle): {', '.join(missing)}")
    problems = validate(root, meta)
    for pr in problems:
        print(f"  PROBLEM: {pr}")
    ok &= not problems
    print(f"  {total} frames total -> {'OK' if ok else 'PROBLEMS FOUND'}")
    return 0 if ok else 1


def do_list() -> int:
    root = pets_dir()
    found = sorted(root.glob("*/pet.json"))
    if not found:
        print(f"no pets in {root}")
        return 0
    for f in found:
        meta = json.loads(f.read_text())
        print(f"  {meta.get('id', f.parent.name):14s} "
              f"{meta.get('displayName',''):20s} "
              f"{len(meta.get('animations') or {}):2d} animations   "
              f"{meta.get('description','')[:50]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["prompt", "import", "codex", "check", "list"])
    ap.add_argument("target", nargs="?", help="sheet path, codex pet name, or pet id")
    ap.add_argument("--id", help="pet id (folder name)")
    ap.add_argument("--name", help="display name")
    ap.add_argument("--desc", default="", help="one-line character description")
    ap.add_argument("--cols", type=int, help="override detected column count")
    ap.add_argument("--rows", type=int, help="override detected row count")
    a = ap.parse_args()

    if a.command == "list":
        return do_list()
    if a.command == "check":
        return do_check(a.target or a.id or "")
    if a.command == "prompt":
        if not (a.id and a.name):
            print("prompt needs --id and --name", file=sys.stderr); return 2
        return do_prompt(a.id, a.name, a.desc)

    if not (a.id and a.name):
        print("import/codex need --id and --name", file=sys.stderr); return 2
    if a.command == "codex":
        sheet = Path.home() / ".codex/pets" / (a.target or "") / "spritesheet.webp"
        source = f"codex:{a.target}"
    else:
        sheet = Path(a.target or "").expanduser()
        source = str(sheet)
    return do_import(sheet, a.id, a.name, a.desc, a.cols, a.rows, source)


if __name__ == "__main__":
    raise SystemExit(main())
