# Visuals and mix: building a render that passes

Rules for the stations before the render check, for animated explainers. Each one exists because its absence
showed up as a failed review.

## Visual grammar

- **One hero object.** A persistent element (the real chart, a map, the product) keeps the same screen box
  across sections; a new section moves to another window of it. Cuts then feel like camera moves in one world.
- **A fixed colour dictionary.** Decide once (up green, down red, the reference value gold, one colour per
  recurring group) and never reuse a colour for another meaning.
- **Punch in, then hold.** On each new element a 0.5-0.7 s ease-in-out push (1.08-1.18x), then still; widen at the
  paragraph end. No continuous slow zoom: it reads as a still picture and fails the hook band.
- **No empty frame at a paragraph start.** The scene shows something from its first frame, not from its first
  cue word.
- **Nothing fully still for 3 s** outside the end screen (`render_check.py` measures it).
- **One focus at a time.** When a counter, document or slam arrives, dim the rest to 30-35%. A value is written
  on screen once; labels never sit on lines or other labels.
- **40 px safe margin** (at 1920x1080) for every text and box, also at the peak of a punch-in.
- **Vary transitions.** Never the same transition twice in a row.
- **Clips play at their own speed.** A 24 fps clip slowed down stutters; interpolate frames (to 60 fps) instead.
- **Text is punctuation.** Text cards stay under 20% of the runtime, never two in a row, at most 2.5 s and three
  words each. To name a picture, use a small label chip, not a full-screen card (`text_dose.py` measures it).

## End screen

- The last 12-20 s hold text-free video slots and a subscribe area for YouTube's end-screen elements; no earlier
  caption hides under them; no looping animation.
- Sound runs to the very last frame. Music fades over the last 3 s; it never cuts.

## Mix

- **Duck the music under speech by at least 14 dB** with a sidechain compressor keyed by the voice.
- **Give the ducking a lookahead** of about 150 ms so it lands before the first syllable: delay the music and the
  voice by 150 ms, but not the copy of the voice that keys the compressor.
- **The last piece runs to the end** (pad or loop it), then the final fade.
- **Trap: `-ss` after `-i` with `afade`.** As an output option, `-ss` discards audio after the filters ran on the
  input's own timestamps. A fade-out timed from the cut point (`afade=t=out:st=10:d=3` on a cut that starts at
  30 s) fades at input second 10, so the whole output is silent. Seek with `-ss` before `-i`, or
  `atrim=start=30,asetpts=PTS-STARTPTS` before the fade, and measure the output after every mix step.
- **Normalize last.** `normalize_audio.py` on the final mix, then `render_check.py` measures it again.

## Highlight boxes on real pages

- Measure, never guess. Web page: a clean browser with no login, the phrase's DOM range
  `getBoundingClientRect()` multiplied by the screenshot scale (device pixel ratio and any resize). Screen
  recording or screenshot: the pixel position on the frame (a row or column profile).
- Draw every box on a check image and open it once before the render. When one line is shown, mask the crop so
  the neighbouring lines do not show.
- No personal data, logged-in pages or paywall workarounds on screen.

## Check stills before a full render

- A fix is verified on a frame, not on a full render. Render single stills at the seconds you changed
  (Remotion: `renderStill()` or `npx remotion still` with `--frame`; other tools: export those frames).
- Remotion's `bundle()` copies the whole `public/` folder on every call: keep `public/` to this video's assets,
  or bundle once and reuse the result for all stills.
- Judge an animation at about 30%, 75% and 93% of its length, never from one frame.
