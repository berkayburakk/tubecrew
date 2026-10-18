# Code gate details

Read this when a check fails and you need to know what it measured, or when the creator asks whether a threshold
fits their genre. The numbers live as constants at the top of each script; this page explains them.

## Hook motion band (`hook_motion`, `motion`)

The picture is sampled at 4 fps as 96x54 gray frames. `d` is the mean absolute difference between consecutive
frames (0-255). Three numbers, for the hook window (first `--hook-seconds`, default 45) and for the whole video:

| Metric | Definition |
|---|---|
| events/min | frames with `d > 18` per minute: cuts, punch-ins, text pops |
| frozen % | share of the time inside runs of 3 s or more with `d < 0.15` (the same frame) |
| slow % | share of the time inside runs of 3 s or more with `d < 1.2` (looks still to the eye) |

The reference band (events 38+, frozen at most 2.2%, slow at most 4.9%) was calibrated on reference faceless
explainers that hold attention well; drafts that lost viewers measured 6-12 events/min and 57-90% slow. A slow
zoom on a still image measures `d` around 0.3-0.9: freezedetect calls it moving, the eye and this band call it
still. The thresholds only hold at this sampling.

- Hook: FAIL above the frozen or slow limit, WARN when only events/min is low (that one depends on the genre).
- Whole video: WARN outside the band (INFO with `--profile live`); the longest slow windows say where to look.
- `--motion-band 20,2.2,8` changes the band for a calmer genre. Say why in the channel notes.

## Sound to the end, total silence and dead air (`total_silence`, `trailing_silence`, `silence_gap`)

Two `silencedetect` passes run in the same decode, at two levels:

| Level | Meaning | Rule |
|---|---|---|
| -60 dB | total silence: a clip without audio, a cut mix, digital zero | over 1 s anywhere (body or tail) FAILs |
| -50 dB | dead air: near silence | the last 3+ s FAIL, 2-3 s warn; a 2 s gap in the body warns |

Why two levels: a natural fade-out still reads as silence at -50 dB (it can measure over a second on a clean
ending), so a 1 s rule at -50 dB would raise false alarms. At -60 dB a fade is gone and only real dropouts remain.
The -50 dB rules stay for what -60 dB misses: a long near-silent tail with sound just above digital zero. A -50 dB
gap that overlaps a failed total silence is the same gap and is reported once, under `total_silence`.

`--music` is for a soundtrack that is music only, with no narration. Music has no speech pauses, so any dead air
is a hole in the mix: a body dip below -50 dB of 0.5 s warns and of 1 s fails (`silence_gap`). The last 3 s may
fade out. Without `--music`, pauses under 2 s are normal speech rhythm and are not reported.

The report keeps the raw runs (`measured.silences` at -50 dB, `measured.total_silences` at -60 dB) and a
`measured.silence_summary`: the longest total silence in the body and in the tail (the last 25 s, at most the
second half of a short video), the levels, and with `--music` the holes found.

## Frozen picture (`freeze`)

`freezedetect` (noise 0.003, at least 2 s) on 5 fps frames (2 fps with `--fast`). The end screen is the last
20 s. `animated`: 3 s or more still outside the end screen FAILs, 2-3 s warns. `live`: 3 s in the hook FAILs,
the body warns at 8 s.

## Subtitles against the spoken words (`subtitle_words`, `subtitle_drift`, `subtitle_text`)

`--words` takes Whisper-style JSON (segments with `words`, or a `words` list, keys `word`/`start`/`end`), such as
the transcript check-voiceover saves. Its times are relative to the narration file; `--words-offset` is where the
narration starts in the video. Cues are matched to words by text, so "$54.2" still matches "fifty-four point two".

- Drift: the median of (cue start - first word start). Over 0.25 s FAIL, over 0.15 s WARN; over 0.5 s the words
  probably belong to another audio version. A shared offset is reported once, not on every cue.
- Each cue: its last word's end minus the cue's end, after removing a lead (ahead) drift. Over 0.15 s FAILs.
- Under 50% of subtitle words found in the words file: FAIL, the files belong to different versions.
- Text rules (WARN): a number split from its unit across lines or cues ("54.2 | billion"); speech-recognition
  hyphen gaps ("non -cash").

## Freshness (`freshness`)

The subtitle or words file was saved after the video: the render may not contain the latest narration or
captions. Re-render, or confirm the edit was cosmetic. The gap is given in seconds, minutes or hours ("2 min
newer"); an input saved within a second of the render does not count.

## Text dose (`text_dose.py`)

OCR on a frame every 2 s. A frame is a text card when boxes at least 4.5% of the frame height tall, outside the
bottom subtitle band, together cover 4% of it: full-screen type, word cards, giant numbers. Small document lines,
chart labels and burned-in subtitles do not count. Limits: 20% of the runtime, 60% of the worst 30 s, an 18 s
run, 3.5 entries a minute; change one with `--limit NAME=VALUE` for a text-heavy genre.

## Transitions (`transitions.py`, a review aid)

Not a gate: it always exits 0 (2 only for usage or environment errors) and never changes the verdict. It marks
places for the render-auditor to look. The picture is sampled at 10 fps as 320x180 gray frames (180x320 for a
vertical video); each pair of consecutive frames gives an absolute difference, averaged over a 32x18 grid of
10x10-pixel cells (0-255).

| Mark | Definition |
|---|---|
| overlay pop | 4+ cells jump by 25+ in one step, the same cells stay under 3 for 0.5 s before and after, and the whole frame stays under 8: a label, box or blur appeared or vanished with no fade |
| screen jump | the whole frame changes by 14+ in one step; with `--cuts`, only within 0.15 s of a listed cut |

Marks closer than 0.3 s are one moment. A fade or a slide spreads the change over several steps and is not
marked; a moving cursor or scrolling text stays under 4 cells. A big, high-contrast box can push the whole frame
over 8 and then reads as motion, not as an overlay: trust the frame pairs, not the count.

- `--cuts 12.5,40,1:02.3` or `--cuts cuts.json` (a JSON list, `{"cuts": [...]}`, or plain numbers): the edit
  points. Without it, every hard jump is marked.
- `--mask X,Y,W,H` (video pixels, repeatable) ignores a region, such as a face cam whose cuts are expected.
- `--frames DIR` saves a before | after PNG pair for each mark (0.2 s before, 0.1 s after).
- `--jump` and `--step` change the two thresholds for a busier or calmer style.

`qa/transitions.json` lists every mark with its time, kind, cell count or overall difference, the region it
covers (fractions of the frame), whether it sits at a cut, and the frame pair's path.

## Exemptions

Only the creator decides. `qa/RENDER-EXEMPTION-<YYYY-MM-DD>.md`:

```
Video: render/final.mp4 sha256:<first 12 hex of this render>
Video: render/final-v2.mp4 sha256:<another render the same reason covers>
Checks: freeze, hook_motion
Reason: screen recording; the face cam moves while the screen stays still. Decided by the creator, 2026-10-17.
```

Matching FAILs of the named checks become WARN with `"exempted": true` and the reason; the report lists the file
under `exemptions`. A file without a matching hash, a `Reason:` line or a valid check name is not applied (the
report says why). `decode` cannot be exempted. Never move a pixel or add invisible motion to slip past a check:
that hides the problem from the creator.

## What `--fast` changes

Black and frozen frames are sampled at 2 fps and 160 px wide. The motion band, loudness, silence and subtitle
checks still run: they are cheap and the hook matters most on a preview.
