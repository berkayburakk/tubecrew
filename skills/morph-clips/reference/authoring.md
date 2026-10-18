# Authoring rules

Read this with `engine-api.md` before planning and building clips.

## Placement: how much of the frame each clip takes

Decide per clip, with the reason:

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

## Content rules

- Show the subject's real objects: the folder, the terminal, the file, the product, the
  result. Map each idea to an interaction from the vocabulary in `engine-api.md`.
- **Never invent numbers, quotes, prices or results.** Use relative bars, skeleton text
  lines or labels taken from the transcript, and record what is illustrative in the clip's
  `illustrative` field. Real figures go in only when the user gives them.
- Keep on-screen text short: names, commands, terms. If the video will be dubbed or
  captioned into other languages, prefer universal terms over sentences — dubbing does not
  translate burned-in text. Let the shape and motion carry the meaning.
- One idea per clip; leave ~2 s of original picture between cutaways.
- Pace to speech, not music: one change per spoken beat, about 0.4–1.2 s apart.

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
