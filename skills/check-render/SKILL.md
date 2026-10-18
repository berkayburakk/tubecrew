---
name: check-render
description: Gate a rendered video before anyone calls it done - a code check for loudness, silent tails, black frames, a frozen hook and subtitle timing, then an independent frame-by-frame review by the render-auditor agent. Use for "check the render", "is this video ready", "QA this export", or before any upload.
---

# Check the render

Two gates, in this order. Code measures what code can measure; an agent that did not build the video looks at
what only eyes can judge. Skip both for drafts and test renders; run both on the final render.

`$SKILL` below is this skill's folder.

## 1. Code gate

```bash
python $SKILL/scripts/render_check.py render/final.mp4 --srt voice/captions.srt
```

It measures in one pass and writes `qa/render-check.json`, bound to the video's SHA-256:

| Check | Fails when |
|---|---|
| Streams | no video or no audio stream |
| Audio length | audio ends more than 0.5 s before the picture |
| Silent tail | the last 3+ seconds are silent |
| Loudness | integrated loudness outside -20..-9 LUFS (warns outside -16..-12; YouTube plays at about -14) |
| Black frames | a black stretch of 1 s or more before the final second |
| Frozen hook | the picture stands still for 3+ s inside the first 45 s |
| Subtitles | a cue ends more than 0.5 s after the video, or the file has no readable cues |

Warnings (body freezes, short gaps, long subtitle lines) are listed but do not block. Use `--fast` while
iterating; run the full pass once on the final file. Exit 1 means fix the render.

## 2. Review gate

Give the `render-auditor` agent the video path, the project folder, `qa/render-check.json` and the script. It
reuses the code measurements instead of re-running them and spends its time on frames: empty or broken charts,
text cut off at the edges, overlays covering a number, pictures that contradict the narration, the end screen.

Save its report unchanged as `qa/RENDER-AUDIT-<date>.md`. The first lines are fixed:

```
VERDICT: PASS
Video: render/final.mp4 sha256:<first 12 hex>
```

A re-review is a new file (`-2`), never an edit. If the video changes after either gate, both gates run again:
the upload script compares hashes and refuses stale verdicts.
