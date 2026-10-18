---
name: find-topic
description: Find the next YouTube video topic from evidence instead of guesses - competitor outliers measured against each channel's own median, search intent, a reachable step for a small channel, curiosity against a proven topic, then a red-team pass. Use for "what should my next video be", "find topics", "what's working in my niche", "check this topic idea", or competitor research.
---

# Find a topic

A topic is a bet. This skill makes the bet with numbers: what beat its own channel, what people actually type,
and whether a channel your size can be seen there. Free tools only; no API key needed.

`$SKILL` below is this skill's folder (the base directory shown when the skill loads). Detailed rules:
`$SKILL/references/topic-rules.md` (candidate kinds, gates, scoring) and `$SKILL/references/demand.md`.

## 1. Scan channels, not topics

List 8-20 channels in the same niche and roughly the same production style (include small ones), then let their
catalogs tell you what worked:

```bash
python $SKILL/scripts/outliers.py @channelA @channelB @channelC --last 60 --min-multiple 2 --dates 10 --save catalog.json
```

- The multiple is views divided by **that channel's own median**. Raw views only rank channel size.
- Catalog views are YouTube's rounded counts. Open the video before quoting a number.
- A topic is **double-sourced** when at least two independent channels beat their own median on it. One video on
  one channel is a lead.
- The strongest evidence is a controlled comparison: same channel, same format, same period, only the topic
  changed. Look for one actively.
- "Possibly ad-pushed: not evidence" (likes under 0.35% of views) means the views were likely bought. Drop it.
- If a healthy channel's numbers come from something you will not do (conspiracy framing, a celebrity host, an
  old viral wave), its outliers say nothing about you.

## 2. Measure demand and intent (free)

```bash
python $SKILL/scripts/demand.py "<query>" --variant "<other phrasing>" --wiki "<Article>" --baseline "<Proven topic article>"
```

- **Suggestions** (YouTube autocomplete) show what people type. Read the intent: if most suggestions are a film
  scene or exam prep, the demand belongs there.
- **Reachable step:** a channel under 50k subscribers with 10k+ views, fresh and real (not a Short, stream,
  podcast or re-upload). The gate is at least one, on the query or a variant; exit 1 means none. Drop a desert
  (nothing reaches 10k); keep a gap (giants only) only with strong catalog evidence.
- **Wikipedia** gives curiosity as a multiple of your proven topic, not need: how-to topics fail it.
- Add Google Trends (YouTube Search property) by hand with `--source trends-yt=TOPIC/BASELINE`. Two sources more
  than 3x apart print **demand unclear**.
- Search volume alone is not a gate: many topics live on recommendations.

## 3. Shape the candidates

Three kinds (rules in `references/topic-rules.md`): catalog outliers, **calendar events** in the next 8 weeks
(certainty confirmed / reported / estimated, judged by growth and 2+ clean channels at 2x, not keyword score) and
**search patterns** ("how X makes money" with a stale top result). For each candidate write:

- the angle in searched words, evidence videos (id, views, channel median, multiple, age), a second source, the
  demand line from `demand.py`, and a draft title with a concrete name or a fresh number;
- a **durability tag**: durable, semi or news. News needs a big event and a backup title in durable form;
- the **two-question gate**: would most of the last video's audience watch it? Would a stranger click it? A "no"
  moves it to the backup list. Then: will the package still be clicked in 6 months, and still be true?
- the evidence video's **first 60 seconds**: first sentence, first number, first cut. Copy the opening mechanics.

Measure each finalist title itself as a query with `demand.py`; zero suggestions is the label "no query".

## 4. Red-team

Run the `topic-skeptic` agent **after** the evidence step, never in parallel, and hand it the raw candidate table
and data (not your summary). It kills what is made, saturated, old, ad-pushed, one-off or unsourced. If fewer than
two survive, run one more evidence round (two rounds at most). After round two the skeptic ranks the five
strongest and recommends one with "low confidence": a round never ends without a topic.

## 5. Decide and record

The creator picks. Append one line to a `topic-decisions.md` log: the pick, the rejected candidates and the gate
that dropped each ("not interesting" is not a reason). After ten videos, the log shows which signals predicted a
good video.

## Report back

At most 3 finalists, each with its evidence row, durability tag and draft title, then `Dropped:` with one-line
reasons and `Not measured:`. Never present a number you did not measure in this run.
