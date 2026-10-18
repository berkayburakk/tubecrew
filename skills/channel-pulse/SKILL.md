---
name: channel-pulse
description: Check how a YouTube channel is doing from its own Analytics - recent videos, where views come from, the searches that bring viewers, whether YouTube tested each new video or cut it - and end with one priority diagnosis. Use for "how is my channel doing", a weekly check, before a topic round, or when a decision's T+30 or T+90 row is due.
---

# Channel pulse

One command reads the channel's own Analytics and ends with **one** diagnosis, not a list. The script does the
counting; your job is to read the diagnosis, say what it rests on, and turn it into the single next action.

`$SKILL` below is this skill's folder.

## Setup

Same OAuth client and the same read-only analytics login as `retention-by-scene` (one login serves both):

```bash
python $SKILL/scripts/pulse.py --login --profile mychannel
```

If there is no OAuth client yet, follow the `upload-private` skill's `references/oauth-setup.md`.

## Run

```bash
python $SKILL/scripts/pulse.py --profile mychannel                    # last 10 videos, last 28 days
python $SKILL/scripts/pulse.py --profile mychannel --videos 5 --days 90 --out notes/
```

It prints the report and writes `pulse-<YYYY-MM-DD>.json` to `--out` (default: the current folder). Keep those
files: they are the source for the decisions ledger. `--json` prints the same JSON.

## When

- The creator asks how the channel is doing, or a weekly check.
- Before a topic round: the search terms and the "tested and cut" pattern say what to fix (packaging, opening) and
  what not to blame (the topic).
- When a decision in the ledger reaches T+30 or T+90 (see `produce-video`, `references/learning-loop.md`).

## Read it in this order

1. **Too early?** Videos under 72 hours or under 100 engaged views are not judged. If nothing can be judged, the
   diagnosis says so and gives the date to come back. Say that and stop; do not interpret noise.
2. **The diagnosis.** It is the first real problem in a fixed order: never tested, tested and cut, low discovery,
   weak opening, no subscriber growth, healthy. Report it with its "based on" line (how many videos, which
   window). `references/diagnosis.md` explains each step and its thresholds.
3. **Per-video patterns.** "Tested and cut" (a burst of a few days, then a quarter of the peak or less) means
   YouTube found people for the topic and viewers did not click or stay: fix packaging or the first 30 seconds,
   not the topic. "Never tested" means the feeds never picked it up: packaging or topic. "Still growing" and
   "tapering off after a run" need nothing.
4. **Search terms.** These are searches that actually brought viewers. Title and tag ideas start here, before any
   keyword tool. An empty list does not mean nobody searched: YouTube hides rare terms.
5. **CTR is not in the API.** When the diagnosis asks for impressions or click-through rate, ask the creator to
   read them in YouTube Studio > Analytics > Reach. Do not guess them.
6. **Audience.** Age, gender and device print only when YouTube returns enough groups; "too little data to
   interpret" means exactly that. One returned age group showing 100% is the privacy threshold, not the audience.

## Rules

- **Every number comes from the tool.** If a figure you need is not in the report or the JSON, it is unknown.
  Add it to the script (with a test) rather than computing it by hand.
- **Analytics lags 2-3 days.** Data API view counts are current; a gap between the two is lag, not a drop.
- **One video is one observation.** The diagnosis says so when it rests on a single video.
- **Findings are warnings, not gates.** The creator may knowingly ignore one; the script never exits 1.

## Fill the decisions ledger

When a T+30 or T+90 row is due, fill it only from a `pulse-<date>.json` (and `retention-by-scene` for the curve),
name the file and date in the row, and write the platform with every view count. A row whose numbers are not in
a tool output stays `_open_`. Never edit a filled row; a correction is a new row.

## Report back

One sentence on where the channel stands, the diagnosis with its single next action, what it could not see
(too-early videos, CTR, hidden search terms), and the ledger rows you filled. Not a list of twenty numbers.
