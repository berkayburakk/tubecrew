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
captions. Re-render, or confirm the edit was cosmetic.

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

Black and frozen frames are sampled at 2 fps and 160 px wide. The motion band, loudness and subtitle checks
still run: they are cheap and the hook matters most on a preview.
