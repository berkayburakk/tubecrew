---
name: check-render
description: Gate a rendered video before anyone calls it done - normalize the final mix to -14 LUFS, a code check for silent tails, black and frozen frames, a hook that does not move, subtitles against the spoken words and the on-screen text dose, then an independent frame-by-frame review by the render-auditor agent. Use for "check the render", "is this video ready", "QA this export", "normalize the audio", or before any upload.
---

# Check the render

Code measures what code can measure; an agent that did not build the video looks at what only eyes can judge.
On drafts and previews run the code gate with `--fast` only. The full pass, the text dose and the review are for
the final render. Building the render: `references/visuals-and-mix.md`. What each check measures and how its
threshold was calibrated: `references/code-gate.md`.

`$SKILL` below is this skill's folder.

## 1. Final mix

```bash
python $SKILL/scripts/normalize_audio.py render/raw.mp4 --out render/final.mp4
```

Two-pass loudnorm to -14 LUFS with the true peak under -1 dBTP; the video stream is copied and the input is never
overwritten. Exit 1 means a target was missed. A "dynamic mode" warning means loudnorm compressed the mix
instead of applying one gain: tame the loudest peaks and run it again.

## 2. Code gate

```bash
python $SKILL/scripts/render_check.py render/final.mp4 --srt voice/captions.srt \
    --words qa/voice-check-transcript.json [--words-offset 1.5] [--profile live]
```

One decode pass; writes `qa/render-check.json` bound to the video's SHA-256.

| Check | Fails when |
|---|---|
| Streams, sound | no video or audio; audio ends 0.5 s early; the last 3+ s are silent |
| Loudness | outside -20..-9 LUFS (warns outside -16..-12) |
| Black frames | a black stretch of 1 s or more before the final second |
| Frozen picture | 3+ s still in the hook, or anywhere outside the end screen (`animated`) |
| Hook motion | the first 45 s stand still or only zoom slowly on a still image |
| Subtitles | a cue ends after the video; the file has no readable cues |
| Subtitles vs words | a cue closes over 0.15 s before its last word ends; the median drift is over 0.25 s |

Warnings do not block: body motion outside the reference band, few cuts in the hook, a number split from its
unit, "non -cash" hyphen gaps, long lines, and inputs saved after the render (freshness).

- `--profile live` for camera and screen recordings, where a face or cursor carries the picture: the hook band
  only warns and body freezes warn at 8 s.
- `--words` is the narration's word-timed transcript (check-voiceover saves one); `--words-offset` is where the
  narration starts in the video. Without it, cue ends against speech are not measured.
- `--motion-band EVENTS,FROZEN,SLOW` loosens the band for a calmer genre; write down why.

Exit 1 means fix the render.

## 3. Text dose (long-form, final render)

```bash
pip install rapidocr-onnxruntime     # optional, only this script needs it
python $SKILL/scripts/text_dose.py render/final.mp4
```

OCR on a frame every 2 s; a frame is a text card when large text (4.5%+ of the height, outside the subtitle band)
covers 4% of it. Fails when cards pass 20% of the runtime, 60% of the worst 30 s, an 18 s run or 3.5 entries a
minute. Writes `qa/text-dose.json`. Run it on a low-resolution preview too, before the full render.

## When a check is wrong for this video

The creator decides, not the agent. They write `qa/RENDER-EXEMPTION-<YYYY-MM-DD>.md`:

```
Video: render/final.mp4 sha256:<first 12 hex>
Checks: freeze, hook_motion
Reason: screen recording; the face cam moves while the screen stays still (decided by the creator)
```

`render_check.py` then reports those FAILs as WARN, marked exempted with the reason, while the hash matches. A new
render needs a new `Video:` line. Never add invisible motion or move pixels to slip past a check.

## 4. Review gate

Give the `render-auditor` agent the video path, the project folder, `qa/render-check.json`, `qa/text-dose.json`
and the script. It reuses the code measurements and spends its time on frames: empty or broken charts, text cut
off at the edges, overlays covering a number, pictures that contradict the narration, the end screen.

Save its report unchanged as `qa/RENDER-AUDIT-<date>.md`. The first lines are fixed:

```
VERDICT: PASS
Video: render/final.mp4 sha256:<first 12 hex>
```

A re-review is a new file (`-2`), never an edit. If the video changes after any gate, every gate runs again: the
upload script compares hashes and refuses stale verdicts.
