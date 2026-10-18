#!/usr/bin/env python3
"""Channel pulse: how your YouTube channel is doing right now, ending in one priority diagnosis.

What it does: reads your own channel with the YouTube Analytics API v2 and the Data API v3 (read-only) and reports
- the last N public videos (default 10): views and engaged views, average view percentage, retention at 30 s,
  subscribers gained, the main traffic source, and the shape of the daily views: "the feed tested it and cut it",
  "never tested", "still growing" or "tapering off after a run";
- where the views of the last D days (default 28) came from (`insightTrafficSourceType`) and the discovery share,
  the part YouTube itself recommended (home feed, suggested videos, search, Shorts feed);
- the YouTube searches that brought viewers (`insightTrafficSourceDetail` for YT_SEARCH), where title and tag
  ideas should start;
- age, gender and device, only when YouTube returns enough of them to read;
then ONE diagnosis: the first real problem in a fixed order, with the single next step.

Why: a dashboard shows twenty numbers and no priority. The daily-views shape of each video separates "the topic
found no audience" from "YouTube tested it and viewers did not click or stay", which totals alone cannot.

Guards: a video younger than 72 hours or with fewer than 100 engaged views is "too early" and not judged; a video
a week old that is still under 100 engaged views is "never tested". Impressions and click-through rate are not in
the Analytics API (YouTube Studio only); when the diagnosis needs them, the report asks you to read them there.

Run:
    python pulse.py --profile mychannel                          report + pulse-<YYYY-MM-DD>.json in this folder
    python pulse.py --profile mychannel --videos 5 --days 90 --out notes/ --json
    python pulse.py --login --profile mychannel                  one-time browser login (same token as retention.py)

Credentials: `~/.tubecrew/client_secret.json` and `~/.tubecrew/tokens/<profile>-analytics.json`
(scopes yt-analytics.readonly + youtube.readonly); `TUBECREW_HOME` moves the folder. Needs
`pip install google-auth google-auth-oauthlib requests`.

Read-only: it never writes to YouTube and never saves a refreshed token. The only file it writes is
`pulse-<YYYY-MM-DD>.json` in `--out` (default: the current folder); that file holds no local paths.

Exit codes: 0 report printed (also when it is too early to judge); 2 usage, credential or API error. There is no
gate here, so it never exits 1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Optional

# --- sample guards ------------------------------------------------------------------------------------------------
MIN_AGE_HOURS = 72          # the first three days swing a lot: a video that looks dead at 6 hours can be the best by day 3
MIN_ENGAGED_VIEWS = 100     # under 100 engaged views one viewer moves an average by a point or more: averages are noise
NEVER_TESTED_AFTER_HOURS = 7 * 24  # feeds test a new upload in its first days; a week on and still under 100 = no test

# --- daily-views shape --------------------------------------------------------------------------------------------
TEST_MIN_PEAK_VIEWS = 30    # a feed test shows a video to hundreds of people in a day or two; a best day under 30 is none
TEST_WINDOW_DAYS = 7        # the feed test of a new upload happens in its first week; a later spike is a different event
TEST_HIGH_RATIO = 0.5       # a day at half the best day or more is part of the burst
CUT_MAX_DAYS = 5            # a burst that ends within 5 days is a test that was stopped; a longer run that tapers is normal
CUT_RATIO = 0.25            # after a cut, daily views fall to a quarter of the best day or less
GROWING_RATIO = 0.6         # the last days still at 60% or more of the best day: YouTube is still showing it
RECENT_DAYS = 3             # "now" is the mean of the last three days with data, so one odd day decides nothing
DAILY_SHOWN = 14            # the text report prints the first two weeks of each daily series (all of it is in the JSON)

# --- opening, discovery, subscribers ------------------------------------------------------------------------------
OPENING_S = 30.0
WEAK_OPENING_PERCENTILE = 0.30  # relative retention at 30 s under 0.30 = bottom 30% of similar-length videos
OPENING_BODY_GAP = 0.15     # the body must beat the opening by this much to blame the opening alone
BODY_RANGE = (0.30, 0.90)   # "body" = 30%-90% of the runtime: past the opening, before the end-screen exodus
DISCOVERY_MIN_SHARE = 0.30  # under 30% from YouTube's own recommendations, the channel lives on people who already know it
DISCOVERY_MIN_VIEWS = 50    # below 50 views in the window a share is a handful of people, not a measurement
SUB_RATE_MIN = 0.001        # fewer than 1 new subscriber per 1,000 views: viewers come and go
SUB_RATE_MIN_VIEWS = 1000   # judged only above 1,000 views; below that one subscriber more or less flips the verdict
MIN_SEGMENT_VIEWS = 50      # device shares and the TV/mobile ratio need 50+ views, else one long session swings them

# --- API limits and output ----------------------------------------------------------------------------------------
SEARCH_TERMS_MAX = 25       # the API's maximum for insightTrafficSourceDetail reports
SEARCH_TERMS_SHOWN = 10
PER_VIDEO_TERMS_SHOWN = 5
MAX_PLAYLIST_PAGES = 4      # 200 uploads is plenty to find the last N public videos and stops a runaway loop
DEFAULT_VIDEOS = 10
DEFAULT_DAYS = 28           # Studio's default window: recent enough to describe the channel as it is now
PACIFIC_OFFSET_H = 7        # Analytics days are Pacific time; UTC-7 never places a publish on a day too early

ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly",
                    "https://www.googleapis.com/auth/youtube.readonly"]
DATA_API = "https://www.googleapis.com/youtube/v3"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
PIP_HINT = "pip install google-auth google-auth-oauthlib requests"

SOURCE_LABELS = {
    "SUBSCRIBER": "home feed and subscriptions", "RELATED_VIDEO": "suggested videos", "YT_SEARCH": "YouTube search",
    "SHORTS": "Shorts feed", "YT_CHANNEL": "your channel page", "PLAYLIST": "playlists",
    "EXT_URL": "external links and Google search", "NO_LINK_OTHER": "direct or unknown",
    "NO_LINK_EMBEDDED": "embedded players", "NOTIFICATION": "notifications", "YT_OTHER_PAGE": "other YouTube pages",
    "END_SCREEN": "end screens", "ADVERTISING": "ads", "HASHTAGS": "hashtag pages", "SOUND_PAGE": "Shorts sound pages",
    "PROMOTED": "YouTube promotion", "ANNOTATION": "annotations", "CAMPAIGN_CARD": "campaign cards",
    "LIVE_REDIRECT": "live redirects", "PRODUCT_PAGE": "product pages", "VIDEO_REMIXES": "remixes",
    "WATCH_WITH": "watch-with streams",
}
# Sources where YouTube's own systems put the video in front of people. Notifications, links, the channel page and
# playlists reach people who already know the channel, so they are not discovery.
DISCOVERY_SOURCES = {"SUBSCRIBER", "RELATED_VIDEO", "YT_SEARCH", "SHORTS", "HASHTAGS", "SOUND_PAGE", "YT_OTHER_PAGE",
                     "PROMOTED"}
FEED_NAMES = {"SUBSCRIBER": "the home feed", "SHORTS": "the Shorts feed", "RELATED_VIDEO": "suggested videos",
              "YT_SEARCH": "search"}
AGE_GROUPS = ("age13-17", "age18-24", "age25-34", "age35-44", "age45-54", "age55-64", "age65-")
GENDERS = ("female", "male", "user_specified")
TYPE_LABELS = {"long": "long videos", "short": "Shorts", "live": "live streams", "all": "all videos"}

CTR_NOTE = ("Impressions and click-through rate are not in the YouTube Analytics API. Read them in YouTube Studio > "
            "Analytics > Reach when the diagnosis asks for them.")
LAG_NOTE = ("Analytics lags 2-3 days behind the Data API view counts; a gap between the two is lag, not a drop.")
SEARCH_NOTE = ("YouTube hides rare search terms, so a short or empty list does not mean nobody searched.")

_PROFILE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


class EnvError(Exception):
    """Usage, credential or API problem (exit 2)."""


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# --- pure helpers -------------------------------------------------------------------------------------------------

def parse_iso_duration(value: str) -> float:
    """ISO-8601 duration from the Data API (`PT8M12S`, `PT1H2M`, `P0D`) -> seconds. ValueError if bad."""
    m = _DURATION.match(value or "")
    if not m or value in ("P", "PT") or value.endswith("T"):
        raise ValueError(f"not an ISO-8601 duration: {value!r}")
    days, hours, minutes, seconds = m.groups()
    return int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def parse_time(value) -> Optional[dt.datetime]:
    """`2026-10-01T12:00:00Z` -> aware UTC datetime; None when unreadable."""
    text = str(value or "").strip().replace("Z", "+00:00")
    # Python 3.9's fromisoformat takes only 3 or 6 fraction digits
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        t = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def hours_since(published, now: dt.datetime) -> Optional[float]:
    t = parse_time(published)
    return None if t is None else (now - t).total_seconds() / 3600


def analytics_start(published_at) -> Optional[str]:
    """startDate for a video: one day before its UTC publish date, because Analytics dates are Pacific time."""
    t = parse_time(published_at)
    return None if t is None else (t.date() - dt.timedelta(days=1)).isoformat()


def num(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def table(report) -> list:
    """Analytics answer -> [{column: value}]. Columns are found by name (their order is not guaranteed)."""
    names = [h.get("name") for h in (report or {}).get("columnHeaders") or []]
    return [dict(zip(names, row)) for row in (report or {}).get("rows") or []
            if isinstance(row, (list, tuple)) and len(row) == len(names)]


def content_type(value) -> Optional[str]:
    """creatorContentType (`videoOnDemand`, `VIDEO_ON_DEMAND`, `shorts`, ...) -> long | short | live | other."""
    if not isinstance(value, str) or not value:
        return None
    key = value.lower().replace("_", "")
    return {"videoondemand": "long", "shorts": "short", "livestream": "live"}.get(key, "other")


def daily_series(rows: list, first_day: dt.date, last_day: dt.date) -> list:
    """[{day, views}] -> views per day from first_day to last_day; missing days are zero (the API skips them)."""
    by_day = {}
    for r in rows:
        v = num(r.get("views"))
        try:
            by_day[dt.date.fromisoformat(str(r.get("day")))] = int(v or 0)
        except ValueError:
            continue
    if last_day < first_day:
        return []
    return [by_day.get(first_day + dt.timedelta(days=i), 0) for i in range((last_day - first_day).days + 1)]


def classify_pattern(series: list, age_hours: Optional[float], engaged: Optional[float]) -> dict:
    """Shape of a video's daily views -> {"label", ...}.

    Labels: too_early (under 72 h, or under 100 engaged views and less than a week old), never_tested (the feeds did
    not pick it up), tested_and_cut (a burst of at most 5 days in the first week, then a quarter of the peak or
    less), still_growing (the last days hold 60%+ of the best day, or the best day is one of the last days),
    tapering (a longer run that is slowing down: a normal life cycle, nothing to fix).
    """
    out = {"label": "too_early", "peak": None, "peak_day": None, "burst_days": None, "recent_mean": None}
    if age_hours is None or age_hours < MIN_AGE_HOURS:
        return {**out, "why": f"under {MIN_AGE_HOURS} hours old"}
    if engaged is None or engaged < MIN_ENGAGED_VIEWS:
        if age_hours >= NEVER_TESTED_AFTER_HOURS:
            return {**out, "label": "never_tested",
                    "why": f"a week or more old with under {MIN_ENGAGED_VIEWS} engaged views"}
        return {**out, "why": f"under {MIN_ENGAGED_VIEWS} engaged views"}
    if not series:
        return {**out, "why": "no daily data yet"}
    peak = max(series)
    p = series.index(peak)
    recent = series[-RECENT_DAYS:]
    mean = sum(recent) / len(recent)
    out.update(peak=peak, peak_day=p, recent_mean=round(mean, 1))
    if peak < TEST_MIN_PEAK_VIEWS:
        return {**out, "label": "never_tested", "why": f"best day {peak} views (under {TEST_MIN_PEAK_VIEWS})"}
    if len(series) - 1 - p < RECENT_DAYS or mean >= GROWING_RATIO * peak:
        return {**out, "label": "still_growing", "why": "the last days hold most of the best day"}
    high = [i for i, v in enumerate(series) if v >= TEST_HIGH_RATIO * peak]
    burst = high[-1] - high[0] + 1
    out["burst_days"] = burst
    if high[0] < TEST_WINDOW_DAYS and burst <= CUT_MAX_DAYS and mean <= CUT_RATIO * peak:
        return {**out, "label": "tested_and_cut", "why": f"a {burst}-day burst, then a quarter of the peak or less"}
    return {**out, "label": "tapering", "why": f"a {burst}-day run that is slowing down"}


def curve(report: dict, metric: str) -> list:
    """Retention rows -> sorted [(elapsed ratio, value)]; bad rows are skipped."""
    points = []
    for r in table(report):
        x, y = num(r.get("elapsedVideoTimeRatio")), num(r.get(metric))
        if x is not None and y is not None:
            points.append((x, y))
    return sorted(points)


def value_at(points: list, seconds: float, duration: float) -> Optional[float]:
    """Curve value at `seconds` by linear interpolation; None outside the video or for an empty curve."""
    if not points or not duration or seconds < 0 or seconds > duration:
        return None
    x = seconds / duration
    if x <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def retention_summary(report: dict, duration: Optional[float]) -> Optional[dict]:
    """Retention at 30 s (audienceWatchRatio, %), and YouTube's relative retention at 30 s (the opening) and over
    30-90% of the runtime (the body). 0.5 = the median of similar-length videos. None without a curve."""
    watch, rel = curve(report, "audienceWatchRatio"), curve(report, "relativeRetentionPerformance")
    if not watch or not duration:
        return None
    at30 = value_at(watch, OPENING_S, duration)
    opening = value_at(rel, OPENING_S, duration) if rel else None
    body = [y for x, y in rel if BODY_RANGE[0] <= x <= BODY_RANGE[1]]
    return {"retention_30s_pct": None if at30 is None else round(at30 * 100, 1),
            "opening_percentile": None if opening is None else round(opening, 2),
            "body_percentile": round(sum(body) / len(body), 2) if body else None}


def traffic_summary(rows: list) -> dict:
    """Traffic rows -> views per source for all videos and, when the API split them, per content type."""
    split = any("creatorContentType" in r for r in rows)
    groups = {}
    for r in rows:
        src = r.get("insightTrafficSourceType")
        if not isinstance(src, str):
            continue
        views = int(num(r.get("views")) or 0)
        keys = ["all"]
        kind = content_type(r.get("creatorContentType"))
        if kind:
            keys.append(kind)
        for key in keys:
            bucket = groups.setdefault(key, {})
            bucket[src] = bucket.get(src, 0) + views
    out = {}
    for key, bucket in groups.items():
        total = sum(bucket.values())
        disc = sum(v for s, v in bucket.items() if s in DISCOVERY_SOURCES)
        out[key] = {"sources": dict(sorted(bucket.items(), key=lambda kv: -kv[1])), "total": total,
                    "discovery": disc, "discovery_share": round(disc / total, 3) if total else None}
    return {"split_by_type": split, "groups": out}


def discovery_basis(traffic: dict) -> Optional[dict]:
    """The group the discovery verdict reads: long videos when the API split them out (the Shorts feed inflates the
    channel-wide share and does not carry over to long videos), otherwise all videos."""
    groups = (traffic or {}).get("groups") or {}
    if (traffic or {}).get("split_by_type") and (groups.get("long") or {}).get("total"):
        return {"group": "long", **groups["long"]}
    if (groups.get("all") or {}).get("total"):
        return {"group": "all", **groups["all"]}
    return None


def search_terms(rows: list) -> list:
    """[{insightTrafficSourceDetail, views, estimatedMinutesWatched}] -> [{term, views, minutes}], most views first."""
    terms = [{"term": r["insightTrafficSourceDetail"], "views": int(num(r.get("views")) or 0),
              "minutes": num(r.get("estimatedMinutesWatched"))}
             for r in rows if isinstance(r.get("insightTrafficSourceDetail"), str)]
    return sorted(terms, key=lambda t: (-t["views"], t["term"]))


def share_summary(rows: list, dim: str, allowed: tuple) -> dict:
    """ageGroup or gender with viewerPercentage -> {status, readable, shares, missing}.

    YouTube drops every group below its privacy threshold and spreads 100% over the groups it returns, so one
    returned group shows as 100%: that is the threshold's trace, not the audience. Status: no_data (nothing
    returned), below_threshold (one group; no percentage is kept), partial (missing groups are zero or hidden, which
    cannot be told apart), complete.
    """
    shares = {}
    for r in rows:
        key, pct = r.get(dim), num(r.get("viewerPercentage"))
        if key in allowed and pct is not None:
            shares[key] = shares.get(key, 0.0) + pct
    if not shares:
        status = "no_data"
    elif len(shares) == 1:
        status = "below_threshold"
    elif len(shares) < len(allowed) and not (dim == "gender" and {"female", "male"} <= set(shares)):
        status = "partial"
    else:
        status = "complete"
    readable = status in ("partial", "complete")
    return {"status": status, "readable": readable,
            "shares": {k: round(shares[k], 1) for k in allowed if k in shares} if readable else {},
            "returned": [k for k in allowed if k in shares], "missing": [k for k in allowed if k not in shares]}


def device_summary(rows: list) -> dict:
    """deviceType rows -> view shares, minutes per view, and TV/mobile minutes-per-view ratio (when both have
    MIN_SEGMENT_VIEWS views). Not readable at all under MIN_SEGMENT_VIEWS views in total."""
    devices = {}
    for r in rows:
        name = r.get("deviceType")
        if not isinstance(name, str):
            continue
        d = devices.setdefault(name, {"views": 0, "minutes": 0.0})
        d["views"] += int(num(r.get("views")) or 0)
        d["minutes"] += num(r.get("estimatedMinutesWatched")) or 0.0
    total = sum(d["views"] for d in devices.values())
    for d in devices.values():
        d["share"] = round(d["views"] / total, 3) if total else 0.0
        d["minutes_per_view"] = round(d["minutes"] / d["views"], 2) if d["views"] else None
    tv, mobile = devices.get("TV"), devices.get("MOBILE")
    ratio, why = None, None
    if not tv or not mobile:
        why = "no TV or no mobile views"
    elif min(tv["views"], mobile["views"]) < MIN_SEGMENT_VIEWS:
        why = f"too few views (TV {tv['views']}, mobile {mobile['views']}; needs {MIN_SEGMENT_VIEWS} each)"
    elif not mobile["minutes_per_view"]:
        why = "no mobile watch time"
    else:
        ratio = round(tv["minutes_per_view"] / mobile["minutes_per_view"], 1)
    return {"readable": total >= MIN_SEGMENT_VIEWS, "total_views": total,
            "devices": dict(sorted(devices.items(), key=lambda kv: -kv[1]["views"])),
            "tv_mobile_ratio": ratio, "ratio_missing_because": why}


def _ready_at(v: dict) -> Optional[dt.datetime]:
    t = parse_time(v.get("published_at"))
    if t is None:
        return None
    age = v.get("age_hours")
    return t + dt.timedelta(hours=MIN_AGE_HOURS if age is None or age < MIN_AGE_HOURS else NEVER_TESTED_AFTER_HOURS)


def diagnose(videos: list, traffic: dict, totals: dict, days: int) -> dict:
    """ONE priority diagnosis: the first real problem in a fixed order (see references/diagnosis.md)."""
    judged = [v for v in videos if v["pattern"]["label"] != "too_early"]
    n_all = len(videos)

    def result(code, title, message, ctr=False, based_on=""):
        return {"code": code, "title": title, "message": message, "needs_studio_ctr": ctr,
                "based_on": based_on or f"{len(judged)} of {n_all} recent videos judged, last {days} days"}

    if not videos:
        return result("too_early", "Nothing to judge yet", "The channel has no public videos.")
    if not judged:
        ready = [r for r in (_ready_at(v) for v in videos) if r is not None]
        when = f" Run it again after {min(ready).date().isoformat()}." if ready else ""
        return result("too_early", "Too early to judge",
                      f"None of the last {n_all} videos is old enough ({MIN_AGE_HOURS} hours) and watched enough "
                      f"({MIN_ENGAGED_VIEWS} engaged views) to judge.{when}")

    half = math.ceil(len(judged) / 2)

    def having(label):
        return [v for v in judged if v["pattern"]["label"] == label]

    def one_note(n):
        return " One video is one observation, not a pattern." if n == 1 else ""

    never = having("never_tested")
    if len(never) >= half:
        return result(
            "never_tested", "YouTube is not testing your videos",
            f"{len(never)} of the {len(judged)} judged videos never got a feed test: their best day stayed under "
            f"{TEST_MIN_PEAK_VIEWS} views, or a week went by with under {MIN_ENGAGED_VIEWS} engaged views. This is a "
            "packaging or topic problem, not a script problem. Next step: read impressions and click-through rate "
            "for these videos in Studio. Few impressions: the topic or title does not match what people come to "
            "this channel for. Plenty of impressions but a low CTR: change the title and thumbnail."
            + one_note(len(never)), ctr=True)

    cut = having("tested_and_cut")
    if len(cut) >= half:
        v = cut[0]
        p = v["pattern"]
        feed = FEED_NAMES.get(v.get("top_source"), "YouTube's feeds")
        r = v.get("retention") or {}
        opening = r.get("opening_percentile")
        if opening is not None and opening < WEAK_OPENING_PERCENTILE:
            step = (f"Its opening is weak (relative retention at 30 s {opening:.2f}; 0.50 is the median), so start "
                    "with the next video's first 30 seconds, and check CTR in Studio for the test days.")
        else:
            step = ("Next step: read CTR in Studio for the test days. A low CTR: the title and thumbnail. A normal "
                    "CTR: the first 30 seconds (run retention-by-scene on it).")
        return result(
            "tested_and_cut", "The feed tested your videos and cut them",
            f"{len(cut)} of the {len(judged)} judged videos got a burst of views and then dropped to a quarter of "
            f"the peak or less. Newest: \"{v['title'][:60]}\": {feed} showed it for {p['burst_days']} day(s) "
            f"(best day {p['peak']} views, now about {p['recent_mean']:.0f} a day). YouTube found people for the "
            "topic and stopped because they did not click or did not stay, so fix the packaging or the first 30 "
            f"seconds, not the topic. {step}" + one_note(len(cut)), ctr=True)

    basis = discovery_basis(traffic)
    if basis and basis["total"] >= DISCOVERY_MIN_VIEWS and basis["discovery_share"] is not None \
            and basis["discovery_share"] < DISCOVERY_MIN_SHARE:
        label = TYPE_LABELS.get(basis["group"], basis["group"])
        return result(
            "discovery_low", f"YouTube is not recommending your {label}",
            f"Only {basis['discovery_share'] * 100:.0f}% of the views of {label} in the last {days} days came from "
            "YouTube's own recommendations (home feed, suggested videos, search, Shorts feed); the rest came from "
            "people who already know the channel (channel page, notifications, links, playlists). More videos of "
            "the same kind will not change that. Next step: compare impressions and CTR in Studio, and make the next "
            "title answer something people already search for (see the search terms above).", ctr=True)

    weak = [v for v in judged if (v.get("retention") or {}).get("opening_percentile") is not None
            and v["retention"]["opening_percentile"] < WEAK_OPENING_PERCENTILE
            and v["retention"].get("body_percentile") is not None
            and v["retention"]["body_percentile"] >= v["retention"]["opening_percentile"] + OPENING_BODY_GAP]
    if weak:
        v = weak[0]
        r = v["retention"]
        return result(
            "opening_weak", "The first 30 seconds lose people",
            f"\"{v['title'][:60]}\": relative retention at 30 s is {r['opening_percentile']:.2f} (0.50 = the median "
            f"of similar-length videos) while the body holds at {r['body_percentile']:.2f}. Viewers who get past the "
            "opening stay, so the next video's first 30 seconds are the one thing to change. Run retention-by-scene "
            "on it to see what was said." + one_note(len(weak)))

    views, subs = totals.get("views") or 0, totals.get("subscribers_gained")
    if subs is not None and views > SUB_RATE_MIN_VIEWS and subs / views < SUB_RATE_MIN:
        return result(
            "no_subscriber_growth", "Views are not turning into subscribers",
            f"{subs} new subscribers from {views:,} views in the last {days} days ({subs / views * 1000:.2f} per "
            "1,000). People watch and leave. Next step: end each video with a reason to come back (the question the "
            "next video answers), and make sure the recent videos read as one clear promise.")

    return result("healthy", "Nothing stands out",
                  "The judged videos are being tested and kept, discovery is healthy and the openings hold. Keep the "
                  "rhythm: the next video matters more than another look at the numbers.")


# --- text report --------------------------------------------------------------------------------------------------

def _fmt(value, pct=False, digits=1) -> str:
    if value is None:
        return "-"
    if pct:
        return f"{value:.{digits}f}%"
    return f"{int(value):,}"


def pattern_text(v: dict) -> str:
    p = v["pattern"]
    label = p["label"]
    if label == "too_early":
        return f"too early to judge ({p.get('why')})"
    if label == "never_tested":
        return f"never tested: the feeds did not pick it up ({p.get('why')})"
    if label == "tested_and_cut":
        feed = FEED_NAMES.get(v.get("top_source"), "YouTube's feeds")
        return (f"tested and cut: {feed} showed it for {p['burst_days']} day(s), then stopped (best day {p['peak']}, "
                f"now ~{p['recent_mean']:.0f}/day). Packaging or first-30-seconds problem, not the topic")
    if label == "still_growing":
        return f"still growing: ~{p['recent_mean']:.0f}/day over the last {RECENT_DAYS} days, best day {p['peak']}"
    return f"tapering off after a {p['burst_days']}-day run (best day {p['peak']}); a normal life cycle"


def render_text(r: dict) -> str:
    ch, w = r["channel"], r["window"]
    lines = [f"Channel pulse: {ch['title']} ({ch['id']}), {r['date']}",
             f"subscribers {_fmt(ch['subscribers'])} | window: last {w['days']} days | Analytics data through "
             f"{w['last_data_day'] or '?'}", LAG_NOTE, ""]
    t = r["totals"]
    lines.append(f"LAST {w['days']} DAYS: {_fmt(t['views'])} views ({_fmt(t['engaged_views'])} engaged), "
                 f"{_fmt(t['minutes_watched'])} minutes watched, +{_fmt(t['subscribers_gained'])} subscribers")
    lines += ["", f"RECENT VIDEOS (newest first; judged only from {MIN_AGE_HOURS} h and {MIN_ENGAGED_VIEWS} engaged "
                  "views)"]
    if not r["videos"]:
        lines.append("  no public videos")
    for i, v in enumerate(r["videos"], 1):
        a = v.get("analytics") or {}
        ret = v.get("retention") or {}
        age = v.get("age_hours")
        age_txt = "?" if age is None else (f"{age:.0f} h old" if age < 48 else f"{age / 24:.0f} days old")
        subs = a.get("subscribers_gained")
        lines.append(f"  {i}. {v['title'][:70]} ({v['id']})")
        lines.append(f"     {v['published'] or '?'}, {v.get('content_type') or 'type unknown'}, {age_txt} | views "
                     f"{_fmt(a.get('views', v.get('views_data_api')))} (engaged {_fmt(a.get('engaged_views'))}) | "
                     f"avg viewed {_fmt(a.get('average_view_percentage'), pct=True)} | at 30 s "
                     f"{_fmt(ret.get('retention_30s_pct'), pct=True)} | subs "
                     f"{'-' if subs is None else '+' + _fmt(subs)}")
        lines.append(f"     {pattern_text(v)}")
        if v.get("daily_views"):
            shown = " ".join(str(x) for x in v["daily_views"][:DAILY_SHOWN])
            more = " ..." if len(v["daily_views"]) > DAILY_SHOWN else ""
            lines.append(f"     daily views from {v['daily_start']}: {shown}{more}")
    lines += ["", f"TRAFFIC SOURCES (views, last {w['days']} days; 'home feed and subscriptions' is one bucket)"]
    groups = (r["traffic"] or {}).get("groups") or {}
    allg = groups.get("all")
    if not allg or not allg["total"]:
        lines.append("  no data")
    else:
        for src, views in list(allg["sources"].items())[:8]:
            lines.append(f"  {SOURCE_LABELS.get(src, src):34} {views:>8,}  {views / allg['total'] * 100:4.0f}%")
        lines.append("  discovery share = views YouTube itself recommended (home feed, suggested, search, Shorts feed)")
        for key in ("long", "short", "all"):
            g = groups.get(key)
            if g and g["total"] and (key != "all" or not r["traffic"]["split_by_type"]):
                lines.append(f"  -> {TYPE_LABELS[key]}: {g['discovery_share'] * 100:.0f}% of {g['total']:,} views")
        if not r["traffic"]["split_by_type"]:
            lines.append("     (not split by content type: Shorts can inflate this share)")
    lines += ["", "SEARCH TERMS THAT BROUGHT VIEWERS (start title and tag ideas here)"]
    st = r.get("search_terms")
    if st is None:
        lines.append("  not available")
    else:
        for term in st[:SEARCH_TERMS_SHOWN]:
            lines.append(f"  {term['views']:>6,}  {term['term']}")
        if not st:
            lines.append("  none returned")
        for v in r["videos"]:
            if v.get("search_terms"):
                lines.append(f"  [{v['title'][:40]}] " + ", ".join(
                    f"{x['term']} ({x['views']})" for x in v["search_terms"][:PER_VIDEO_TERMS_SHOWN]))
        lines.append(f"  {SEARCH_NOTE}")
    lines += ["", "AUDIENCE"]
    lines += audience_lines(r.get("audience") or {})
    lines += ["", "NOTES", f"  {CTR_NOTE}"] + [f"  {n}" for n in r.get("notes") or []]
    d = r["diagnosis"]
    lines += ["", f"DIAGNOSIS: {d['title']}", f"  {d['message']}", f"  (based on {d['based_on']})"]
    if d["needs_studio_ctr"]:
        lines.append("  This diagnosis needs impressions and CTR from YouTube Studio (Analytics > Reach).")
    return "\n".join(lines) + "\n"


def audience_lines(aud: dict) -> list:
    out = []
    for dim, label, names in (("age", "age", AGE_GROUPS), ("gender", "gender", GENDERS)):
        s = aud.get(dim)
        if s is None:
            out.append(f"  {label}: not available")
        elif not s["readable"]:
            why = ("YouTube returned nothing" if s["status"] == "no_data"
                   else f"YouTube returned only {s['returned'][0]}; the rest is below its privacy threshold")
            out.append(f"  {label}: too little data to interpret ({why})")
        else:
            shown = ", ".join(f"{k.replace('age', '')} {pct:.0f}%" for k, pct in s["shares"].items())
            out.append(f"  {label} (share of signed-in viewers): {shown}")
            if s["status"] == "partial":
                out.append(f"     missing: {', '.join(s['missing'])} (zero or below the privacy threshold; "
                           "cannot tell which)")
    dev = aud.get("device")
    if dev is None:
        out.append("  device: not available")
    elif not dev["readable"]:
        out.append(f"  device: too little data to interpret ({dev['total_views']} views; needs {MIN_SEGMENT_VIEWS})")
    else:
        out.append("  device (views): " + ", ".join(f"{k.lower()} {d['share'] * 100:.0f}%"
                                                    for k, d in dev["devices"].items()))
        out.append(f"     TV vs mobile minutes per view: {dev['tv_mobile_ratio']}x" if dev["tv_mobile_ratio"]
                   else f"     TV vs mobile: not computed ({dev['ratio_missing_because']})")
    return out


# --- network ------------------------------------------------------------------------------------------------------

def tubecrew_home() -> Path:
    env = os.environ.get("TUBECREW_HOME")
    return Path(env).expanduser() if env else Path.home() / ".tubecrew"


def token_path(profile: str) -> Path:
    return tubecrew_home() / "tokens" / f"{profile}-analytics.json"


def login_command(profile: str) -> str:
    """The login command to suggest. Never an absolute path: it can land in reports that get shared."""
    script = "pulse.py"
    if sys.argv and sys.argv[0].endswith(".py"):
        try:
            script = Path(sys.argv[0]).resolve().relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            script = Path(sys.argv[0]).name
    return f'python "{script}" --login --profile {profile}'


def new_session():
    try:
        import requests
    except ImportError as e:
        raise EnvError(f"missing Python package (requests): {PIP_HINT}") from e
    return requests.Session()


def load_credentials(path: Path, login_cmd: str):
    """Loads an authorized-user token and refreshes it in memory (never written back: this script is read-only)."""
    try:
        from google.auth.exceptions import GoogleAuthError, RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        import requests  # noqa: F401
    except ImportError as e:
        raise EnvError(f"missing Python package ({e.name}): {PIP_HINT}") from e
    if not path.is_file():
        raise EnvError(f"no analytics token ({path.name}). Log in once: {login_cmd}")
    try:
        cred = Credentials.from_authorized_user_file(str(path))
    except (OSError, ValueError) as e:
        raise EnvError(f"token file {path.name} is unreadable ({type(e).__name__}). Log in again: {login_cmd}") from e
    if cred.refresh_token:
        try:
            cred.refresh(Request())
        except RefreshError as e:
            why = ("the login was revoked or has expired (invalid_grant)" if "invalid_grant" in str(e)
                   else f"refreshing the token failed ({str(e)[:160]})")
            raise EnvError(f"{why}. Log in again: {login_cmd}") from e
        except GoogleAuthError as e:
            raise EnvError(f"could not reach Google to refresh the token ({type(e).__name__})") from e
    elif not cred.valid:
        raise EnvError(f"token {path.name} has expired and has no refresh token. Log in again: {login_cmd}")
    return cred


def _get(session, token: str, url: str, params: dict) -> dict:
    r = session.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        raise ApiError(r.status_code, f"HTTP {r.status_code} from {url.split('?')[0]}: {str(r.text)[:300]}")
    return r.json()


def analytics(session, token: str, start: str, end: str, metrics: str, dimensions: Optional[str] = None,
              filters: Optional[str] = None, sort: Optional[str] = None, max_results: Optional[int] = None) -> dict:
    params = {"ids": "channel==MINE", "startDate": start, "endDate": end, "metrics": metrics}
    for key, value in (("dimensions", dimensions), ("filters", filters), ("sort", sort), ("maxResults", max_results)):
        if value:
            params[key] = value
    return _get(session, token, ANALYTICS_API, params)


def traffic_report(session, token: str, start: str, end: str, filters: Optional[str] = None) -> dict:
    """Traffic sources split by content type; without the split if the API refuses it (HTTP 400)."""
    try:
        return analytics(session, token, start, end, "views,estimatedMinutesWatched",
                         dimensions="insightTrafficSourceType,creatorContentType", filters=filters)
    except ApiError as e:
        if e.status != 400:
            raise
        return analytics(session, token, start, end, "views,estimatedMinutesWatched",
                         dimensions="insightTrafficSourceType", filters=filters)


def search_report(session, token: str, start: str, end: str, video_id: Optional[str] = None) -> dict:
    filters = "insightTrafficSourceType==YT_SEARCH" + (f";video=={video_id}" if video_id else "")
    return analytics(session, token, start, end, "views,estimatedMinutesWatched",
                     dimensions="insightTrafficSourceDetail", filters=filters, sort="-views",
                     max_results=SEARCH_TERMS_MAX)


def retention_report(session, token: str, video_id: str, start: str, end: str) -> dict:
    """Retention curve; falls back to audienceWatchRatio alone if the relative metric is refused (HTTP 400)."""
    params = dict(dimensions="elapsedVideoTimeRatio", filters=f"video=={video_id}")
    try:
        return analytics(session, token, start, end, "audienceWatchRatio,relativeRetentionPerformance", **params)
    except ApiError as e:
        if e.status != 400:
            raise
        return analytics(session, token, start, end, "audienceWatchRatio", **params)


def fetch_channel(session, token: str) -> Optional[dict]:
    items = _get(session, token, f"{DATA_API}/channels",
                 {"part": "snippet,statistics,contentDetails", "mine": "true"}).get("items") or []
    return items[0] if items else None


def fetch_recent_videos(session, token: str, uploads: str, n: int) -> list:
    """The newest n public uploads (scheduled, private and unlisted videos are left out)."""
    public, page_token = [], None
    for _ in range(MAX_PLAYLIST_PAGES):
        params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
        if page_token:
            params["pageToken"] = page_token
        page = _get(session, token, f"{DATA_API}/playlistItems", params)
        ids = [(it.get("contentDetails") or {}).get("videoId") for it in page.get("items") or []]
        ids = [i for i in ids if i]
        if ids:
            data = _get(session, token, f"{DATA_API}/videos",
                        {"part": "snippet,contentDetails,statistics,status", "id": ",".join(ids)})
            public += [v for v in data.get("items") or [] if (v.get("status") or {}).get("privacyStatus") == "public"]
        page_token = page.get("nextPageToken")
        if len(public) >= n or not page_token:
            break
    public.sort(key=lambda v: (v.get("snippet") or {}).get("publishedAt") or "", reverse=True)
    return public[:n]


def _optional(fn, notes: list, label: str):
    """Runs an optional fetch; an API refusal becomes a note instead of stopping the whole report."""
    try:
        return fn()
    except ApiError as e:
        notes.append(f"{label}: not available (HTTP {e.status}).")
        return None


def video_entry(session, token: str, video: dict, now: dt.datetime, today: dt.date,
                last_data_day: Optional[dt.date], notes: list) -> dict:
    snippet = video.get("snippet") or {}
    vid = video.get("id") or "?"
    published_at = snippet.get("publishedAt")
    age = hours_since(published_at, now)
    try:
        duration = parse_iso_duration((video.get("contentDetails") or {}).get("duration", ""))
    except ValueError:
        duration = None
    views_data = num(_to_number((video.get("statistics") or {}).get("viewCount")))
    entry = {"id": vid, "title": snippet.get("title", ""), "published_at": published_at,
             "published": str(published_at or "")[:10], "age_hours": None if age is None else round(age, 1),
             "duration_s": duration, "views_data_api": None if views_data is None else int(views_data),
             "content_type": None, "analytics": None, "retention": None, "top_source": None, "sources": {},
             "daily_start": None, "daily_views": [], "search_terms": []}
    if age is None or age < MIN_AGE_HOURS:
        entry["pattern"] = classify_pattern([], age, None)
        return entry
    start = min(analytics_start(published_at) or today.isoformat(), today.isoformat())
    end = today.isoformat()
    f = f"video=={vid}"
    tot = (table(analytics(session, token, start, end, "views,engagedViews,averageViewPercentage,"
                                                       "averageViewDuration,subscribersGained,estimatedMinutesWatched",
                           filters=f)) or [{}])[0]
    entry["analytics"] = {"views": _int(tot.get("views")), "engaged_views": _int(tot.get("engagedViews")),
                          "average_view_percentage": _round(tot.get("averageViewPercentage")),
                          "average_view_duration_s": _round(tot.get("averageViewDuration")),
                          "subscribers_gained": _int(tot.get("subscribersGained")),
                          "minutes_watched": _round(tot.get("estimatedMinutesWatched"))}
    a = entry["analytics"]
    sample = a["engaged_views"] if a["engaged_views"] is not None else a["views"]

    daily = table(analytics(session, token, start, end, "views,engagedViews", dimensions="day", filters=f,
                            sort="day"))
    days_seen = sorted(d for d in (_date(r.get("day")) for r in daily) if d)
    pub = parse_time(published_at)
    first = (pub - dt.timedelta(hours=PACIFIC_OFFSET_H)).date()
    if days_seen:
        first = min(first, days_seen[0])
    last = last_data_day or (days_seen[-1] if days_seen else None)
    if last is not None:
        entry["daily_start"] = first.isoformat()
        entry["daily_views"] = daily_series(daily, first, last)

    traffic = traffic_summary(table(traffic_report(session, token, start, end, filters=f)))
    allg = traffic["groups"].get("all") or {}
    entry["sources"] = allg.get("sources") or {}
    entry["top_source"] = next(iter(entry["sources"]), None)
    kinds = [k for k in traffic["groups"] if k != "all"]
    if kinds:
        entry["content_type"] = max(kinds, key=lambda k: traffic["groups"][k]["total"])

    entry["pattern"] = classify_pattern(entry["daily_views"], age, sample)
    if sample is not None and sample >= MIN_ENGAGED_VIEWS:
        rep = _optional(lambda: retention_report(session, token, vid, start, end), notes,
                        f"retention curve of {vid}")
        entry["retention"] = retention_summary(rep, duration) if rep else None
        if entry["sources"].get("YT_SEARCH"):
            rep = _optional(lambda: search_report(session, token, start, end, vid), notes, f"search terms of {vid}")
            entry["search_terms"] = search_terms(table(rep)) if rep else []
    return entry


def _to_number(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> Optional[int]:
    v = num(value)
    return None if v is None else int(v)


def _round(value, digits: int = 1) -> Optional[float]:
    v = num(value)
    return None if v is None else round(v, digits)


def _date(value) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        return None


def collect(session, token: str, n_videos: int, days: int, now: dt.datetime) -> dict:
    """Everything the report says, fetched read-only. Core calls raise ApiError; optional sections become notes."""
    notes = []
    today = now.astimezone().date()
    start, end = (today - dt.timedelta(days=days - 1)).isoformat(), today.isoformat()
    channel = fetch_channel(session, token)
    if channel is None:
        raise EnvError("this token sees no YouTube channel; log in with the account (and channel) you publish on")
    stats = channel.get("statistics") or {}
    uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    videos = fetch_recent_videos(session, token, uploads, n_videos) if uploads else []

    daily = table(analytics(session, token, start, end, "views,engagedViews,estimatedMinutesWatched,"
                                                         "subscribersGained", dimensions="day", sort="day"))
    seen = sorted(d for d in (_date(r.get("day")) for r in daily) if d)
    last_data_day = seen[-1] if seen else None
    totals = {"views": sum(_int(r.get("views")) or 0 for r in daily),
              "engaged_views": sum(_int(r.get("engagedViews")) or 0 for r in daily) if daily and all(
                  "engagedViews" in r for r in daily) else None,
              "minutes_watched": round(sum(num(r.get("estimatedMinutesWatched")) or 0 for r in daily)),
              "subscribers_gained": sum(_int(r.get("subscribersGained")) or 0 for r in daily)}
    traffic = traffic_summary(table(traffic_report(session, token, start, end)))
    search = _optional(lambda: search_report(session, token, start, end), notes, "channel search terms")
    age = _optional(lambda: analytics(session, token, start, end, "viewerPercentage", dimensions="ageGroup"),
                    notes, "age groups")
    gender = _optional(lambda: analytics(session, token, start, end, "viewerPercentage", dimensions="gender"),
                       notes, "gender")
    device = _optional(lambda: analytics(session, token, start, end, "views,estimatedMinutesWatched",
                                         dimensions="deviceType"), notes, "device types")
    entries = [video_entry(session, token, v, now, today, last_data_day, notes) for v in videos]
    if not traffic["split_by_type"] and traffic["groups"]:
        notes.append("Traffic sources came back without the content-type split; the discovery share covers all "
                     "videos together.")
    report = {
        "tool": "channel-pulse", "date": today.isoformat(), "generated_at": now.isoformat(timespec="seconds"),
        "channel": {"title": (channel.get("snippet") or {}).get("title", ""), "id": channel.get("id"),
                    "subscribers": _int(_to_number(stats.get("subscriberCount"))),
                    "videos": _int(_to_number(stats.get("videoCount")))},
        "window": {"days": days, "start": start, "end": end,
                   "last_data_day": last_data_day.isoformat() if last_data_day else None},
        "totals": totals, "videos": entries, "traffic": traffic,
        "search_terms": search_terms(table(search)) if search is not None else None,
        "audience": {"age": share_summary(table(age), "ageGroup", AGE_GROUPS) if age is not None else None,
                     "gender": share_summary(table(gender), "gender", GENDERS) if gender is not None else None,
                     "device": device_summary(table(device)) if device is not None else None},
        "notes": notes, "ctr_note": CTR_NOTE,
        "thresholds": {"min_age_hours": MIN_AGE_HOURS, "min_engaged_views": MIN_ENGAGED_VIEWS,
                       "never_tested_after_hours": NEVER_TESTED_AFTER_HOURS,
                       "test_min_peak_views": TEST_MIN_PEAK_VIEWS, "cut_max_days": CUT_MAX_DAYS,
                       "cut_ratio": CUT_RATIO, "growing_ratio": GROWING_RATIO,
                       "discovery_min_share": DISCOVERY_MIN_SHARE, "weak_opening_percentile": WEAK_OPENING_PERCENTILE},
    }
    report["diagnosis"] = diagnose(entries, traffic, totals, days)
    return report


def do_login(profile: str) -> int:
    """Browser login for the profile's read-only analytics token. The only path that opens a browser."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise EnvError(f"missing Python package ({e.name}): {PIP_HINT}") from e
    secret = tubecrew_home() / "client_secret.json"
    if not secret.is_file():
        raise EnvError(f"no OAuth client file at {secret}. Create a Desktop app OAuth client in Google Cloud and "
                       "save its JSON there (see the upload-private skill, references/oauth-setup.md).")
    cred = InstalledAppFlow.from_client_secrets_file(str(secret), scopes=ANALYTICS_SCOPES).run_local_server(port=0)
    path = token_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cred.to_json(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    print(f"Saved the analytics token for profile '{profile}' ({path.name}).")
    try:
        channel = fetch_channel(new_session(), cred.token)
        if channel:
            print(f"Channel: {(channel.get('snippet') or {}).get('title', '?')} ({channel.get('id')})")
    except Exception as e:  # noqa: BLE001 - the token is saved; the channel name is a courtesy
        print(f"Could not read the channel name ({type(e).__name__}).")
    return 0


# --- main ---------------------------------------------------------------------------------------------------------

def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    ap = argparse.ArgumentParser(description="How your YouTube channel is doing, ending in one diagnosis.")
    ap.add_argument("--profile", default="default", help="credential profile, one per channel (default: default)")
    ap.add_argument("--token", type=Path, help="use this authorized-user token JSON instead of the profile token")
    ap.add_argument("--videos", type=int, default=DEFAULT_VIDEOS, help=f"recent videos to check (default "
                                                                       f"{DEFAULT_VIDEOS}, max 50)")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"channel window in days (default {DEFAULT_DAYS})")
    ap.add_argument("--out", type=Path, default=Path("."), help="folder for pulse-<YYYY-MM-DD>.json (default: here)")
    ap.add_argument("--json", action="store_true", help="print the machine-readable report")
    ap.add_argument("--login", action="store_true", help="open the browser once to authorize this profile")
    a = ap.parse_args(argv)

    def usage(message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return 2

    if not _PROFILE.fullmatch(a.profile):
        return usage("--profile may only use letters, digits, '.', '_' and '-'")
    if not 1 <= a.videos <= 50:
        return usage("--videos must be between 1 and 50")
    if not 1 <= a.days <= 365:
        return usage("--days must be between 1 and 365")
    try:
        if a.login:
            return do_login(a.profile)
        cred = load_credentials(a.token.expanduser() if a.token else token_path(a.profile), login_command(a.profile))
        try:
            report = collect(new_session(), cred.token, a.videos, a.days, _now())
        except ApiError as e:
            return usage(str(e))
        except OSError as e:
            return usage(f"network error ({type(e).__name__}); check the connection and try again")
    except EnvError as e:
        return usage(str(e))

    print(json.dumps(report, ensure_ascii=False, indent=2) if a.json else render_text(report), end="\n" if a.json
          else "")
    name = f"pulse-{report['date']}.json"
    try:
        a.out.mkdir(parents=True, exist_ok=True)
        (a.out / name).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return usage(f"could not write {name}: {e}")
    print(f"wrote {name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
