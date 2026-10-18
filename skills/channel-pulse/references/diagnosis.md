# Reading the pulse: patterns, the diagnosis order, thresholds

Everything here is computed by `scripts/pulse.py`. The thresholds are constants at the top of the script with a
comment saying why. Tests pin each one at its boundary, so a changed value fails the tests until the tests and
this page are updated with it.

## What the API can and cannot see

| Wanted | Where it comes from |
|---|---|
| views, engaged views, average view %, subscribers gained | Analytics API, per video since publishing |
| retention at 30 s, opening and body percentiles | Analytics retention report, one call per judged video |
| traffic sources, search terms | Analytics `insightTrafficSourceType`, `insightTrafficSourceDetail` (YT_SEARCH) |
| age, gender, device | Analytics `ageGroup`, `gender` (`viewerPercentage`), `deviceType` |
| impressions, click-through rate | **YouTube Studio only** (Analytics > Reach); not in the API |
| current view count | Data API (no lag); Analytics lags 2-3 days |

`views` counts every start and, for Shorts, every replay; `engagedViews` is the stricter count, so the sample
guard uses it. Per-video `subscribersGained` counts only subscriptions from that video's watch page. The
"home feed and subscriptions" source (`SUBSCRIBER`) is one bucket; the API does not split it.

## Sample guards

| Constant | Value | Meaning |
|---|---|---|
| `MIN_AGE_HOURS` | 72 | younger videos are "too early"; the first three days swing a lot |
| `MIN_ENGAGED_VIEWS` | 100 | below this, retention and averages are noise; not judged |
| `NEVER_TESTED_AFTER_HOURS` | 168 | a week old and still under 100 engaged views is "never tested" |

Retention curves are fetched only for videos that pass both guards.

## The daily-views shape of a video

Views per day from the publish day to the last day Analytics has data (missing days count as zero).

| Pattern | Rule | What it means |
|---|---|---|
| never tested | best day under `TEST_MIN_PEAK_VIEWS` (30), or the week rule above | the feeds never showed it to a crowd: packaging or topic |
| tested and cut | burst (days at 50%+ of the peak) of at most `CUT_MAX_DAYS` (5), starting in the first `TEST_WINDOW_DAYS` (7), and the last `RECENT_DAYS` (3) average at most `CUT_RATIO` (25%) of the peak | YouTube found people for the topic and stopped: they did not click or did not stay. Packaging or the first 30 seconds, not the topic |
| still growing | the last three days average `GROWING_RATIO` (60%) of the peak or more, or the peak is one of the last three days | still being shown; wait |
| tapering off | anything else: a longer run that is slowing down | a normal life cycle; nothing to fix |

The report names the feed by the video's top traffic source: the home feed, the Shorts feed, suggested videos or
search.

## The diagnosis order

The script walks this list and stops at the first step that fires. "Judged" means not too early.

1. **Too early** - no recent video can be judged. Gives the date when the first one can be.
2. **Never tested** - at least half of the judged videos. Next step: impressions and CTR in Studio. Few
   impressions: the topic or title does not fit what people come for. Impressions but low CTR: title and
   thumbnail.
3. **Tested and cut** - at least half of the judged videos. Names the newest one. If its relative retention at
   30 s is under 0.30, start with the opening; otherwise read CTR for the test days (low: packaging; normal: the
   first 30 seconds).
4. **Low discovery** - under `DISCOVERY_MIN_SHARE` (30%) of views from home feed, suggested, search, Shorts feed
   and similar, on at least `DISCOVERY_MIN_VIEWS` (50) views. Long videos are judged on their own when the API
   splits them out, because the Shorts feed inflates the channel-wide share and does not carry over to long videos.
5. **Weak opening** - a judged video whose relative retention at 30 s is under 0.30 while the body (30-90% of the
   runtime) is at least 0.15 higher. If the body is weak too, the problem is not the opening alone.
6. **No subscriber growth** - under 1 new subscriber per 1,000 views, on more than 1,000 views in the window.
7. **Healthy** - keep the rhythm.

Relative retention (`relativeRetentionPerformance`) is a percentile among videos of similar length: 0.50 is the
median, 0.30 means 70% of similar videos hold viewers better at that point.

## Audience privacy thresholds

YouTube leaves out every age or gender group below its privacy threshold and spreads 100% over the groups it
returns. One returned group is reported as "too little data to interpret" with no percentage, because its 100%
is the threshold's trace. With some groups missing, the missing ones may be zero or hidden; the report says it
cannot tell which. Device shares need 50 views in total; the TV-to-mobile minutes-per-view ratio needs 50 views on
each.
