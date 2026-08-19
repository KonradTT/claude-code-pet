# The spritesheet contract

What a sheet must satisfy to become a working pet, and what actually goes wrong.

## Layout

One animation per **row**, frames left-packed along each row, uniform cell size,
transparent gutters. The reference sheet (Mumu) is 8 columns × 11 rows of 192 × 208 px
cells — 1536 × 2288 total — but `hatch.py` detects the grid from the transparent gutters
rather than assuming it. Pass `--cols`/`--rows` when detection is wrong.

Frames per row are counted by scanning left to right for cells with content, so rows may
be different lengths. Unused cells must be **fully transparent**, not white.

## Row order

| # | Name | Must show | Loops |
| --- | --- | --- | --- |
| 0 | `idle` | standing still, small motion, feet flat | yes |
| 1 | `walk_right` | walking to the right | yes |
| 2 | `walk_left` | walking to the left | yes |
| 3 | `wave` | raising a limb and waving | yes |
| 4 | `jump` | crouch, launch, airborne, land | once |
| 5 | `celebrate` | happy, both limbs up | once |
| 6 | `shrug` | questioning, limbs out | yes |
| 7 | `think` | pondering, limb to the head | yes |
| 8 | `angry` | brow down, tense | yes |
| 9 | `turn_out` | turning away until facing fully away | once |
| 10 | `turn_in` | turning back to face the viewer | once |

Rows can be missing — those animations fall back to `idle` — but the order of the ones
present must match, because the importer maps by row index, not by content.

## The three non-negotiables

**1. One baseline.** Every frame of every row must have the character's feet on the same
row of its cell. This matters more than anything else and is the most common failure. If
the baseline wanders the pet bobs while standing and jumps when switching animation.
Mumu's 74 frames are all on row 202 — spread of 0.

**2. One character.** Identical palette, proportions, line weight, rendering style and
scale across all 74 frames. Image generators drift over a long sheet; the brief says this
explicitly and it still needs checking by eye.

**3. Real transparency.** Not a white or checkerboard background that merely looks
transparent. `hatch.py` samples the cell corners and fails the sheet if they are opaque.

## What `hatch.py check` reports, and what to do

| Message | Meaning | Fix |
| --- | --- | --- |
| `feet move Npx between frames` on a standing row | the classic drift | regenerate that row, emphasising a fixed baseline |
| `frames do not share a baseline (varies Npx)` | rows disagree with each other | regenerate; or crop rows individually to align |
| `background is not transparent` | opaque or matted background | run a background removal pass before importing |
| `a frame is completely empty` | the generator skipped a cell | regenerate that row |
| `the two walk rows do not look like a left/right pair` | rows 1 and 2 are unrelated | check the row order |
| `walk rows mirror-match: 0.7` | informational | normal for hand-drawn pairs; only < 0.35 is suspicious |

The mirror score is deliberately informational. A known-good sheet scores ~0.71 because
the two walk rows were drawn separately rather than flipped — an exact-mirror test would
reject good art.

## Walk direction

Which of rows 1 and 2 reads as "right" **cannot be judged reliably by eye** from a
front-leaning three-quarter character. It was got wrong in both directions while building
Mumu before being settled by dragging him on screen.

Do not agonise over it during generation. Import, drag him, and if he walks backwards:

- quick fix: right-click → **Reverse walking direction** (persists in config)
- permanent fix: swap the two names in the `ROWS` table in `hatch.py` and re-import

## Importing faithfully

Paste cells with an exact RGBA copy — `strip.paste(cell, pos)`, **not**
`strip.paste(cell, pos, cell)`. Using the cell as its own mask zeroes the RGB under
transparent pixels, which silently changes the file and breaks any byte-comparison against
the source.
