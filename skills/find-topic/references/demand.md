# Measuring demand

`scripts/demand.py` reads three free signals. Each measures a different thing, and each fails in a known way, so
never decide on one of them alone.

```bash
python demand.py "<query>"                                    # suggestions + search results
python demand.py "<query>" --variant "<phrasing 2>" --variant "<phrasing 3>"
python demand.py "<query>" --no-serp --expand                 # suggestions only, with the long tail
python demand.py "<query>" --wiki "<Topic article>" --baseline "<Proven topic article>" \
    --source trends-yt=TOPIC/BASELINE --json --out research/<date>/demand.json
```

Reading the search results takes about half a minute per query (each result is read in full to get the channel
size and date), so add variants on purpose.

## 1. Suggestions (what people type)

YouTube autocomplete for the query and each variant; `--expand` adds `<query> a`..`z` and why/how/what/when
prefixes. `--hl` and `--gl` set language and country.

- Read the intent, not the count. When most suggestions name a film, a song or a game, or ask for exam notes or a
  translation, the demand belongs to that, not to your video. The script flags these words; you judge them.
- A query with no suggestions gets the label "no query": nobody types that phrasing. Rephrase the title; do not
  drop the topic for this alone.

## 2. Reachable step (can a channel your size be seen)

The top 18 results for the query. A **step** is a video from a channel under 50,000 subscribers with 10,000+
views. It counts only when it is **fresh** (within a year, `--fresh-days`) and **real**: not a Short or anything
under 3 minutes, not a stream replay, not a podcast episode, not a likely re-upload. Unknown channel sizes never
count.

- **reachable:** at least one fresh, real step. This is the gate (exit 0).
- **gap:** results reach 10,000 views but no fresh, real step: big channels own it, or the steps are old or
  flagged. Exit 1. Keep the topic only with strong catalog evidence and say "no step".
- **desert:** nothing reaches 10,000 views. Exit 1. Drop it; a desert does not even go to the skeptic.
- One phrasing is never enough: a step often exists for one variant (with a year, a name, "explained") and not for
  another. The gate passes when any variant has a step.
- The top result's age feeds the search-pattern rule in `topic-rules.md`.

## 3. Wikipedia pageviews (curiosity against a proven topic)

Daily views of the topic's article over the last 90 days (user traffic only, all devices) from the Wikimedia REST
API, divided by the same for a **baseline**: the article for one of your own topics that already worked (repeat
`--baseline` to average two or three). Absolute pageviews mean little; "0.4x our proven topic" means something.
Redirects are followed, so pass the title you would type.

Limits:
- **It measures curiosity, not need.** How-to and personal-money topics (fees, interest, fixing something) score
  low even when many people need them, because nobody learns them from an encyclopedia. History and famous events
  score high, because that is what an encyclopedia is for. Do not drop a how-to topic on this signal.
- It says nothing about saturation: a high multiple can sit on top of 200 copies.
- Pick the article that matches the angle: a person usually draws far more views than the concept they stand for.
- Wikimedia throttles bursts; the script pauses between calls and sends a descriptive User-Agent, as Wikimedia
  requires.

## 4. Google Trends (manual)

Google Trends has no stable public API, so it is a hand check:

1. Open trends.google.com, compare the topic with the same baseline term, set the last 90 days and the property
   to **YouTube Search**.
2. Read the average of each line.
3. Pass it as `--source trends-yt=TOPIC/BASELINE` (for example `--source trends-yt=18/42`).

A keyword tool's monthly volume for both terms works the same way (`--source keywords=9500/12000`). Every source
must compare the topic with the same baseline.

## 5. Triangulation

The script compares every topic/baseline multiple (Wikipedia plus each `--source`). Two sources more than **3x**
apart label the topic **demand unclear**: report it that way, not as "demand", and say which sources disagree.
One source alone is "single source"; add a second before calling demand a strength.
