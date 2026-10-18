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
- `reference/engine-api.md` — how to write a clip. **Read it before your first clip.**
- `examples/` — three finished clips: full-frame cutaway, chapter card, transparent panel. They are the quality bar and good starting points.
- `templates/` — the review pages. `fonts/` — Inter and JetBrains Mono (OFL).

Work in a `motion/` folder in the user's project unless they name another.

## 1. Set up (first run)

`node $SKILL/cli/morph.mjs doctor`. If anything is missing, `setup` installs Playwright
into the skill folder and its Chromium into Playwright's browser cache. Needs Node 18+ and ffmpeg (with `prores_ks` for
transparent panels). ffprobe is optional.

## 2. Interview — one round

Ask with AskUserQuestion, skipping anything already answered:

- **The video** — a path. Ideally the final edit, so clip times do not move later.
- **The transcript** — word timestamps (JSON from Whisper/faster-whisper, or any
  `[{word,start,end}]`) are best; an SRT or VTT from their editor, Descript or YouTube
  works (±0.2 s per word).
- **Density** — light 1–3 clips per minute (default when the video already has its own
  visuals, e.g. slides or screen recordings), medium 4–7 (default for talking heads),
  heavy: most lines covered with short gaps of face.
- **Look** — `dark`, `light`, or their brand: colours and fonts in a brand file
  (format in `reference/engine-api.md`). Read any logo or screenshot they give you.
- **Layout** — is there a face cam, a screen area, or empty space in the frame?
- **Anything to avoid or include** — lines to keep on their face, real numbers to show.

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

**Decide how much of the frame each clip takes — per clip, with the reason:**

- **full** — a full-frame cutaway for the length of one idea (2–12 s), then give the
  picture back. For something to *see*: a process, a comparison, a value changing, a
  chapter change. Not in the first second of the hook; not on personal, emotional or
  opinion lines. Protected boxes (a face cam) are restored on top in the composite; the
  engine then centres the clip in the largest free area and scales it to fit, so do not
  set `center` on full clips unless you mean to override that.
- **region** — the clip replaces only a screen area (a slide or app window in a
  recording with a face cam). Render at the region size (rounded down to even numbers).
- **panel** — a transparent clip in space the edit already leaves empty, beside the
  speaker. Can run long (15 s+) as one continuous morph, because the speaker stays on
  screen. Keep it clear of the speaker at every moment.
- **nothing** — the line is about the speaker, the video is already showing that thing
  (its own slide animation, a live demo), or it is too close to another clip.

**Content rules:**

- Show the subject's real objects: the folder, the terminal, the file, the product, the
  result. Map each idea to an interaction from the vocabulary in `reference/engine-api.md`.
- **Never invent numbers, quotes, prices or results.** Use relative bars, skeleton text
  lines or labels taken from the transcript, and record what is illustrative in the clip's
  `illustrative` field. Real figures go in only when the user gives them.
- Keep on-screen text short: names, commands, terms. If the video will be dubbed or
  captioned into other languages, prefer universal terms over sentences — dubbing does not
  translate burned-in text. Let the shape and motion carry the meaning.
- One idea per clip; leave ~2 s of original picture between cutaways.
- Pace to speech, not music: one change per spoken beat, about 0.4–1.2 s apart.

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

`render` uses the plan's fps, takes 4 sub-frames per frame across a 180° shutter (motion
blur), writes `.mp4` for full/region and ProRes 4444 `.mov` with alpha for panels, and names
each file by its in-point (`04-master-prompt_0m32s40.mp4`). It is slower than real time.
`composite` makes `out/preview.mp4` with hard cuts and the original audio and checks its
length against the source. Look at a few preview frames (clip in, middle, out) yourself.

The user gets `motion/out/`: the clips, `TIMING.md` (file, in, out, placement, the line,
what is illustrative), `preview.mp4`, `viewer.html` (step through clips) and `compare.html`
(original vs. edited, side by side, stacked or wipe). Say plainly that the preview is for
review: for the final cut they place the clips in their own editor.

## Style defaults (the user's brand overrides them)

- **Colour:** canvas, one shape colour, ink and muted text, **one accent**; success and
  error colours only for results and problems.
- **Type and icons:** one UI font, one mono font for code, files and terminals; one icon
  set with one stroke weight (`M.icon`).
- **Motion:** springs with at most a tiny overshoot. Leading and trailing edges ride
  different springs so indicators stretch. The camera zooms so each state fills the frame.
- **Banned:** bouncy easing, particles, glows, gradients on UI chrome, mixed icon
  strokes, dead time, anything that looks like a template, made-up data.

## Gotchas

- No `will-change` on anything the camera scales; text renders blurry.
- Text swapping inside a morphing container needs its own enter/exit timing (`M.show`
  `delay/enter/exit`), or old and new text overlap.
- A `.blend` (`mix-blend-mode: difference`) label must sit in the layer that has the
  blend mode; a filtered parent isolates it.
- Items sliding under a highlight need `M.SPRING.FAST`, or the highlight sits empty for a moment.
- Keep the cursor inside the frame at every zoom, including mid-morph: world x × zoom
  must stay within half the width.
- Narrow slices and neighbouring labels collide; the overflow check does not see it.
- A slice or row in the same colour as its background disappears — check both brands if you ship clips for both.
- Clips are built into `dist/`, so a relative image path in a clip breaks: inline small images
  (SVG or a `data:` URI) or use an absolute path.
- Brand colours must be hex or `rgb()`; `check` rejects names and `hsl()` (the engine springs RGB channels).
- SRT word times drift inside a cue; put important changes on the first or last word of a cue.
- A clip ends by holding its last state; if its slot is longer, the composite holds the last frame.
- If the edit changes after rendering, re-measure the times; clip files are named by in-point.
