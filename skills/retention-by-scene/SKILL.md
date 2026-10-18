---
name: retention-by-scene
description: Find where viewers leave a published YouTube video and which scene, chapter or sentence was playing - pulled straight from the YouTube Analytics API with the creator's own login, compared with YouTube's own benchmark for similar videos. Use for "why do people stop watching", "analyze my retention", "which part lost viewers", "what should I clip", or a video post-mortem.
---

# Retention by scene

The retention curve is the most honest feedback YouTube gives. This skill pulls it directly (no CSV export),
lays it over the video's chapters or script, and names the moments that cost viewers.

`$SKILL` below is this skill's folder.

## Setup

Same OAuth client as `upload-private` (see its `references/oauth-setup.md`), with a read-only analytics login:

```bash
python $SKILL/scripts/retention.py --login --profile mychannel
```

## Run

A video under 72 hours old or under 100 engaged views gets "too early to judge" and a date to come back instead
of a report: a curve from a handful of viewers is noise. Write no lesson from it. `--anyway` shows the numbers,
marked NOT JUDGED.

```bash
python $SKILL/scripts/retention.py <video_id> --profile mychannel --srt voice/captions.srt
```

Sections come from the chapters in the description, or `--timeline sections.json`, or the captions grouped into
30-second windows.

## Read it in this order

1. **Opening** - how much is lost by 30 seconds, and YouTube's relative retention at 10 and 30 seconds
   (0.5 is the median of similar-length videos). The first seconds can be inflated by feed autoplay previews, so
   trust the relative number here. An opening problem is fixed in the script's first 15 seconds, never in the edit.
2. **Cliffs** - the steepest drops after 30 seconds, with what was being said. A cliff is a moment: a topic turn
   with no signpost, a long setup, an ad read.
3. **Slide** - steady loss per minute by section. A flat slide is pacing; the fix is cutting, not rewriting.
4. **Rewatch bumps** - where the curve rises, people rewound. Those spans are your best clip and Short candidates.

If the opening holds and the slide is gentle but views are low, the problem is the packaging (title and
thumbnail), not the video.

## Report back

The single biggest leak, the one change it suggests for the next video, and one line for the channel's lessons.
Not a list of ten.
