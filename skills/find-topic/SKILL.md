---
name: find-topic
description: Find the next YouTube video topic from evidence instead of guesses - competitor outliers measured against each channel's own median, search intent, a reachable step for a small channel, then a red-team pass. Use for "what should my next video be", "find topics", "what's working in my niche", "check this topic idea", or competitor research.
---

# Find a topic

A topic is a bet. This skill makes the bet with numbers: what beat its own channel, what people actually type,
and whether a channel your size can be seen there. Free tools only; no API key needed.

`$SKILL` below is this skill's folder (the base directory shown when the skill loads).

## 1. Scan channels, not topics

Guessing topics and then looking for proof wastes rounds. List 8-20 channels in the same niche and roughly the
same production style (include small ones), then let their catalogs tell you what worked:

```bash
python $SKILL/scripts/outliers.py @channelA @channelB @channelC --last 60 --min-multiple 2 --dates 10 --save catalog.json
```

- The multiple is views divided by **that channel's own median**. Raw views only rank channel size.
- Catalog views are YouTube's rounded counts (63M, not 63,214,871). Open the video before quoting a number.
- One video on one channel is a lead, not evidence. A topic is **double-sourced** when at least two independent
  channels beat their own median on it.
- The strongest evidence is a controlled comparison: the same channel, same format and same period, where only
  the topic changed. Look for one actively.
- Check why a healthy channel is healthy. If its numbers come from something you will not do (conspiracy framing,
  a celebrity host, an old viral wave), its outliers say nothing about you.

## 2. Measure demand and intent (free)

- YouTube search suggestions show what people really type: open
  `https://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q=<query>` (add `&hl=` and `&gl=`
  for a language and country). Try several phrasings; one query is never enough.
- Read the intent, not just the volume. If every suggestion for a query is a movie scene, the demand belongs to
  the movie.
- **Reachable step:** in the search results for the query, is there a video from a channel under ~50k
  subscribers with 10k+ views, recent and real (not a re-upload, Short or podcast clip)? A step means a small
  channel can be seen there. No step: either giants own it (a gap) or nobody wants it (a desert). Drop deserts.
- Search volume alone is not a gate: many topics live on recommendations, not search.

## 3. Shape the candidates

For each candidate write: the angle in the words people search, the evidence videos (id, views, channel median,
multiple, age), a second source, the demand signal, and a draft title with a concrete name or number.
Long videos usually need a story (a named actor, an arc, an outcome); a pure concept often works better short.

## 4. Red-team

Hand the raw candidate table (not your summary) to the `topic-skeptic` agent. It tries to kill each candidate:
already made, saturated, old or one-off evidence, policy risk, sources that do not exist. Keep only ALIVE ones.

## 5. Decide and record

The creator picks. Append one line to a `topic-decisions.md` log: the pick, the rejected candidates and why.
After ten videos, that log tells you which signals actually predicted a good video.

## Report back

At most 3 finalists, each with its evidence row and draft title, then `Dropped:` with one-line reasons and
`Not measured:`. Never present a number you did not measure in this run.
