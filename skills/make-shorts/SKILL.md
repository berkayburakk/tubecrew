---
name: make-shorts
description: Turn a finished long video into YouTube Shorts - pick 3-5 self-contained cuts from the script and word timings (re-ranked by retention data when it exists), reframe them to vertical 1080x1920 with re-timed captions, and gate each Short in code (square or vertical, at most 3 minutes, an opening that moves in the first 2 seconds, captions out of YouTube's UI zones) before the render-auditor and a private upload. Use for "make Shorts from this video", "find clips for a Short", "cut a vertical version", "is this Short ready". Not a trailer maker.
---

# Make Shorts

A Short cut from a long video is a new video for a stranger who swipes. It carries one idea from its first line
to a payoff, and it is not a trailer: "watch the full video to find out" leaves the viewer with nothing. The
rules, the platform facts and their sources are in `$SKILL/references/shorts-rules.md`.

The flow, one gate at a time:

**pick** → the creator approves the cuts → **cut** → **check** → `render-auditor` → `upload-checker` →
`upload-private` (private)

`$SKILL` below is this skill's folder. Start from a finished long video: `script.md`, the narration's word-timed
transcript (`qa/voice-check-transcript.json` from check-voiceover) and the final render.

## 1. Pick

```bash
python $SKILL/scripts/shorts_pick.py <video> [--retention qa/retention.json] [--min 20 --max 60] [--count 5]
```

Runs of whole sentences are scored on measurable signals: the **pocket rule** (the first line works with no
context: no "And ...", "So ...", "This is why ...", no intro or "subscribe"; a window that breaks it never passes),
a number or a named thing first, a question the window answers, a short last line that loops back, one script
section, a length near 25-45 s. Times snap to word boundaries and sentence ends. Exit 1: no self-contained moment
exists, so write one rather than force a cut. After 72 hours, pick again with `--retention` (retention-by-scene's
`--json` report): rewatch bumps rank up, windows opening in a steep drop rank down.

Show the creator the table and let them choose. Cut nothing before they approve: a line is cheap to change, a
re-uploaded Short is a new video.

## 2. Cut

```bash
python $SKILL/scripts/shorts_cut.py render/final.mp4 --candidate 1 --words qa/voice-check-transcript.json \
    [--mode fit|crop] [--fill 0.2] [--x 640] [--burn] [--out PATH]
```

- `fit` (default): the whole picture on a blurred copy of itself; `--fill` trims the sides so it grows. Use it for
  charts and slides. `crop`: a 9:16 slice (`--center` or `--x`); use it when one face or object carries the shot.
- Captions are a separate `.srt`, re-timed to the cut (`--srt` re-times the long video's captions, `--words`
  builds short cues). Read them: transcripts mishear names and numbers. `--burn` draws them above YouTube's UI
  zones (needs ffmpeg with libass; without it the script stops and says so).
- Writes `shorts/<name>.mp4` and never touches the source. `--out` writes straight into the Short's own folder.

## 3. Check

```bash
python $SKILL/scripts/shorts_check.py shorts/<name>.mp4 --srt shorts/<name>.srt --words shorts/<name>.words.json \
    [--burned] [--ocr] [--profile live]
```

All of render_check.py's measurements (loudness, silence, black and frozen picture, the motion band, captions
against the words) with the whole Short as the hook and no end-screen allowance, plus:

| Check | Fails when |
|---|---|
| aspect | landscape (YouTube treats it as a regular video) |
| duration | over 180 s (WARN within 2 s of it) |
| opening | the first 2 s neither move nor show text (OCR, or captions burned in from the first second) |
| safe_zone | burned captions sit in the bottom 25% or right 15% (OCR finds there only warn) |

Resolution under 1080x1920 warns; the loop and music over 60 s are information. Writes
`qa/shorts-check-<name>.json` bound to the video's SHA-256. Exit 1: fix the Short. Exemptions work as in
check-render, except for aspect and duration.

## 4. Review and upload

Each Short is its own video, so it gets its own project folder for the reviews and the upload: `render/<name>.mp4`
(cut there with `--out`, or copy it), `upload.json` with `"short": true`, `qa/`. Run `shorts_check.py` there again.

1. `render-auditor`: the same contract (`VERDICT:` and `Video:` lines, saved as `qa/RENDER-AUDIT-<date>.md`).
   Tell it this is a Short: judge the first 2 seconds muted and with sound, the payoff and the loop, text in the
   UI zones, and expect no end screen. Give it `qa/shorts-check-<name>.json` instead of `render-check.json`.
2. `upload-checker`: title and description for a stranger (no "Part 2", no long-video chapters).
3. `upload-private`: the dry run checks the Short is square or vertical and at most 180 s and reads the Shorts
   gate; the thumbnail is not sent (Studio on a computer only). Then `--upload`: private, as always.

Then tell the creator what is left in Studio: the Short's thumbnail, the **related video** that links it to the
long video (Studio only), and when to publish. Spread Shorts over days, never several on one day, and publish a
cut only after its long video is public. Cadence and testing advice: `references/shorts-rules.md`.

## What to report back

The candidates table (or the approved cut), the path of each Short, its check verdict, and the single next action.
