---
name: morph-clips
description: Turn a video and its transcript into motion-graphic B-roll clips timed to the spoken words, in a one-shape style — a single element morphs from pill to card to terminal to chart and never cuts, a cursor drives every change, and every change lands on a word. Use when someone asks for motion graphics, B-roll, animated cutaways, explainer animations or overlay panels for a talking-head, screen-recording or presentation video.
---

# Morph clips

You make short motion-graphic clips for a creator's video. Every clip is **one shape
that never cuts**: it springs its size, corners and colour from state to state while
its content swaps with a short blur. A cursor drives the changes with real clicks and
drags, and every change lands on a spoken word. The creator drops the clips into their
own editor; you also give them a preview render and two review pages.

Everything is in this skill folder (`$SKILL` below):

- `cli/morph.mjs` — the only tool you run: `node $SKILL/cli/morph.mjs <command>`.
- `engine/` — the motion engine (`motion.js`) and page shell (`base.css`).
- `brands/` — `dark.json` (default) and `light.json`; the user's own brand is a JSON file in the same format.
- `reference/engine-api.md` — how to write a clip, and `reference/authoring.md` — placement, content
  rules, style defaults and gotchas. **Read both before your first clip.**
- `examples/` — three finished clips: full-frame cutaway, chapter card, transparent panel. They are the quality bar and good starting points.
- `templates/` — the review pages. `fonts/` — Inter and JetBrains Mono (OFL).

Work in a `motion/` folder in the user's project unless they name another.

## 1. Set up (first run)

`node $SKILL/cli/morph.mjs doctor`. If anything is missing, `node $SKILL/cli/morph.mjs setup` installs
Playwright into the skill folder and its Chromium into Playwright's browser cache. Needs Node 18+ and ffmpeg (with `prores_ks` for
transparent panels). ffprobe is optional.

## 2. Interview — one round

Ask with AskUserQuestion, skipping anything already answered: **the video** (ideally the final edit, so clip
times do not move), **the transcript** (word timestamps from Whisper are best; an SRT or VTT works, ±0.2 s per
word), **density** (light 1–3 clips per minute when the video has its own visuals, medium 4–7 for talking heads,
heavy), **look** (`dark`, `light` or their brand file, format in `reference/engine-api.md`), **layout** (face
cam, screen area, empty space) and **anything to avoid or include** (lines to keep on their face, real numbers).

## 3. Inspect the footage

```
node $SKILL/cli/morph.mjs inspect video.mp4 motion/work [--region x,y,w,h] [--protect x,y,w,h]
node $SKILL/cli/morph.mjs words transcript.srt > motion/work/words.txt
```

Look at `contact.png` yourself. If the frame has a face cam or a screen area, measure
the boxes and pass them; `layout-check.png` draws them on a real frame (blue = region,
green = protected) so you can confirm they fit. They go into `plan.json` `layout`.

## 4. Plan — get approval before any code

`node $SKILL/cli/morph.mjs new motion --video video.mp4 --brand dark`, then show a table
with one row per clip and wait for the user's OK:

| # | In–Out | The line it covers | What the shape does, on which words | Placement | Why |

Decide per clip how much of the frame it takes (**full**, **region**, **panel** or **nothing**) and why, and
follow the content rules: real objects, never invented numbers, short on-screen text, one idea per clip,
one change per spoken beat. Both are in `reference/authoring.md`.

## 5. Build

Write one fragment per clip in `motion/clips/NN-name.html` following the reference and
examples (copying and adapting an example is fine). Put each state change on its word:
clip-local time = word time − clip `in`. Use brand tokens (`var(--accent)`, `M.brand.accent`)
instead of hard-coded colours, so the same clip works in any brand.

If a subagent writes clips, tell it to write in small pieces with edits — a whole large
clip in one reply can hit the output limit.

## 6. Check stills on the key words

```
node $SKILL/cli/morph.mjs stills motion NN-name 0.4 1.2 2.1 …
```

Pick the times where each state has settled plus a couple of mid-morph moments. The tool
reports text that leaves the frame, is clipped by the shape or sits under a protected box,
and a cursor that leaves the frame — fix every `OVERFLOW` line. Then **look at the sheet yourself**: crowding,
readability, colour contrast, a label hidden by a neighbour, off-word timing. A clean
overflow report is not a visual approval.

## 7. Render and deliver

```
node $SKILL/cli/morph.mjs check motion
node $SKILL/cli/morph.mjs render motion [id …] [--parallel 2]
node $SKILL/cli/morph.mjs composite motion
node $SKILL/cli/morph.mjs pages motion
```

`render` writes motion-blurred `.mp4` files for full/region and ProRes 4444 `.mov` with alpha for panels,
named by in-point; it is slower than real time. `composite` makes `out/preview.mp4` with the original audio and
checks its length. Look at a few preview frames (clip in, middle, out) yourself.

The user gets `motion/out/`: the clips, `TIMING.md` (file, in, out, placement, the line,
what is illustrative), `preview.mp4`, `viewer.html` (step through clips) and `compare.html`
(original vs. edited, side by side, stacked or wipe). Say plainly that the preview is for
review: for the final cut they place the clips in their own editor.

## Style and gotchas

Style defaults (one accent, one UI font, springs without bounce, no made-up data) and the known engine
gotchas (blurry text under `will-change`, overlapping text swaps, cursor leaving the frame, SRT drift) are
in `reference/authoring.md`. Check them before you render.
