# Topic rules

Read this when shaping candidates, scoring them or running the red-team round. Every rule here came from a topic
that looked good on one signal and failed on another.

## Order of a topic round

1. **Evidence step (the miner):** catalogs with `outliers.py`, demand with `demand.py`, 6-8 candidates with their
   raw data saved to a dated folder. Never overwrite an older round's raw data.
2. **Red-team (the `topic-skeptic` agent):** runs after the miner has finished, never in parallel. It must see the
   miner's real output, not a summary and not a guess at what the miner will find.
3. **Judge (the main session):** applies the gates below, scores the survivors and prepares at most 3 finalists.

At least two candidates must survive the skeptic. If not, run one more evidence round; two rounds is the limit.
After round two, even if everything died, the five strongest are ranked and one is recommended with "low
confidence". The round never ends without a topic; the creator still makes the pick.

## Candidate kinds

### Catalog outliers (the default)
Videos at 2x or more of their own channel's median, in channels close to the creator's format. Go from channels
to topics, not from guessed topics to proof: scanning catalogs finds winners that guessing misses.

### Calendar-event candidates
- List dated events in the next 8 weeks that the audience will care about: launches, filings, earnings, rulings,
  anniversaries, releases.
- Give each a certainty label: **confirmed** (the organizer published the date), **reported** (a credible outlet
  says so), **estimated** (inferred from earlier cycles).
- Judge them by **growth** (is interest rising in pageviews or Trends over the last weeks?) and by evidence (did at
  least **2 clean channels get 2x their median** on this kind of event in the last 90 days?). A keyword tool's
  score lags a dated event, so a low score is not a reason to drop one.
- Count copies before committing (see saturation below). The event picks the publishing window, not the title and
  not the body.

### Search-pattern candidates
"How X makes money", "What does X actually do" and similar. Finalist only when all three hold:
1. the top search result is more than 12 months old (`demand.py` prints its age) and the subject changed since;
2. within 12 months, a small channel (under 50k subscribers) has a matching video with 3,000-10,000+ views;
3. the title carries a recognizable name and a fresh number.
Report the 60-day copy count anyway.

## Gates every candidate passes

### Two-question gate
1. Would most (think 80%) of the people who liked the last published video watch this one?
2. Would a stranger who has never heard of the channel click this package?
A "no" to either moves the candidate to the backup list. Write both answers, with the reason, into the decision
log.

### Durability tag and the six-month question
Ask: "Will this package still be clicked in 6 months, and will the video still be true?" The answer is the tag:
- **durable:** yes to both;
- **semi:** a dated event behind a lasting question, with numbers said "as of" a date;
- **news:** the next quarter or event makes the video wrong.

A news candidate becomes a finalist only for a big event, and its package carries a backup title in durable form,
ready to swap in if the news angle fades after publishing. Durable title forms: "How X pays for...", "Why X
needs...", "Who gets paid when...", "What happens when...". News forms to avoid: "X just filed", "X's Q3
numbers", "... unlocks soon".

### Title and query
- Measure the **finalist title itself** as a search query, not just the topic: extract its core phrase and run
  `demand.py` on it. Zero suggestions is the label "no query" (information, not a gate).
- When a recognizable subject and a typed query compete for the title, check the channel's traffic sources. Most
  long-form channels get almost all views from the home page and suggestions; then the recognizable subject (a
  person, company or event name) wins. If search is a large share (say 10% or more), the query wins.

### Opening mechanics
Watch the first 60 seconds of every evidence video at 2x or more of its median and write three lines: the first
sentence, the second at which the first concrete number or object appears, and the first cut. Copy the mechanics
(structure and timing), never the words, title or thumbnail.

## Evidence quality

- **Saturation:** count videos with the same promise uploaded in the last 60 days (last 7 days when the news is
  under a week old). Seven or more copies from small channels, all under about 150 views, means the angle is
  priced in. AI-made copies of a hot news angle appear within hours; a small channel a day late is the late one.
- **Ad-pushed views:** likes under 0.35% of views (outliers.py flags them), above all with sponsorship or "in
  partnership with" wording, is likely paid promotion. Not evidence.
- **Single-channel evidence** caps evidence strength at 3/5. So does the lack of a controlled comparison (same
  channel and format, different topic).
- An outlier older than 12 months, a one-off news wave or a channel healthy for a reason the creator will not copy
  is weak evidence.
- Rich source material (archives, documents) makes a topic easier to produce. It says nothing about demand or
  evidence; keep it out of those scores.

## Scoring (the judge)

Score each survivor 1-5 on five axes and rank by the total:

| Axis | 5 means |
|---|---|
| Demand | suggestions, a fresh real step and a consistent multiple of a proven topic |
| Evidence strength | 2+ clean channels at 2x+, plus a controlled comparison (caps above apply) |
| Borrowed authority | a name, company or event the stranger already knows can sit in the title |
| Format fit | the creator's format can tell it; long videos need a story (actor, arc, outcome) |
| Producibility | primary documents and visual material are reachable |

Once the channel has 4 or more published videos, its own results outrank competitor data: a candidate that repeats
a pattern from the channel's winners gains a point of evidence, one that repeats a loser's pattern loses one. Say
so in the reasoning.
