#!/usr/bin/env python3
"""Find where viewers leave one of your YouTube videos, and which section or sentence was playing at the time.

What it does: pulls the audience retention curve (`audienceWatchRatio` and `relativeRetentionPerformance` by
`elapsedVideoTimeRatio`) and the view totals from the YouTube Analytics API v2, plus title, duration and
description from the Data API v3. Then it reports retention at fixed seconds, the opening loss by 30 s, YouTube's
relative retention at 10 and 30 s, the five steepest drops after 30 s, rewatch bumps (clip and Short candidates)
and the loss per minute of every section. Sections come from the chapters in the description (the default when
present), or a `--timeline` JSON, or `--srt` captions grouped into ~30 s windows. With `--srt` it quotes what was
said around each steep drop.

Why: the retention curve is the most honest feedback YouTube gives, but a curve alone does not say which scene
or sentence lost people. Lining it up with the chapters or the script turns "people leave" into "people leave
here, while this was said", which is something the next script can fix.

Run:
    python retention.py <video_id> --profile mychannel [--srt voice/captions.srt] [--timeline sections.json]
    python retention.py <video_id> --token path/to/authorized_user.json --json
    python retention.py --login --profile mychannel          one-time browser login (read-only scopes)

`--timeline` JSON: [{"id": "s1", "start": 0, "end": 42.5, "text": "cold open"}, ...] (seconds; text optional).

Credentials: `~/.tubecrew/client_secret.json` and `~/.tubecrew/tokens/<profile>-analytics.json`
(scopes yt-analytics.readonly + youtube.readonly); `TUBECREW_HOME` moves the folder. Needs
`pip install google-auth google-auth-oauthlib requests`.

Read-only: it never writes to YouTube, never saves a refreshed token, and writes a file only with `--out`
(JSON when the name ends in .json, the text report otherwise); that file holds no absolute paths.

Exit codes: 0 report printed, or no retention data yet; 2 usage, credential or API error. There is no gate here,
so it never exits 1.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

MARKS = (5, 10, 15, 20, 30, 45, 60, 90, 120)
OPENING_S = 30.0
DROPS_N = 5
BUMP_MIN_PP = 0.5
BUMPS_SHOWN = 5  # the human report lists the biggest few; on a small video most small rises are one or two viewers
WINDOW_S = 30.0
QUOTE_MARGIN_S = 3.0

ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly",
                    "https://www.googleapis.com/auth/youtube.readonly"]
DATA_API = "https://www.googleapis.com/youtube/v3"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
PIP_HINT = "pip install google-auth google-auth-oauthlib requests"

NO_DATA = ("No retention data yet: YouTube Analytics lags 2-3 days and very small videos may return none.")
AUTOPLAY_NOTE = ("Note: the first seconds can be inflated by home-feed autoplay previews, so for the opening trust "
                 "relativeRetentionPerformance more than the absolute values.")

_PROFILE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")
_CHAPTER = re.compile(r"^\s*(?:[-*•]\s+)?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+(\S.*)$")
_SRT_TIME = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


class EnvError(Exception):
    """Usage, credential or API problem (exit 2)."""


# --- pure helpers -------------------------------------------------------------------------------------------------

def parse_iso_duration(value: str) -> float:
    """ISO-8601 duration from the Data API (`PT8M12S`, `PT1H2M`, `P1DT2H`, `P0D`) -> seconds. ValueError if bad."""
    m = _DURATION.match(value or "")
    if not m or value in ("P", "PT") or value.endswith("T"):
        raise ValueError(f"not an ISO-8601 duration: {value!r}")
    days, hours, minutes, seconds = m.groups()
    return int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


def curve_points(report: dict, metric: str) -> list:
    """Analytics rows -> sorted [(elapsed ratio, value)]. Columns are found by name; bad rows are skipped."""
    names = [h.get("name") for h in (report or {}).get("columnHeaders") or []]
    if "elapsedVideoTimeRatio" not in names or metric not in names:
        return []
    xi, yi = names.index("elapsedVideoTimeRatio"), names.index(metric)
    points = []
    for row in (report or {}).get("rows") or []:
        if not isinstance(row, (list, tuple)) or len(row) <= max(xi, yi):
            continue
        x, y = row[xi], row[yi]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (x, y)):
            continue
        points.append((float(x), float(y)))
    return sorted(points)


def ratio_to_seconds(ratio: float, duration: float) -> float:
    return ratio * duration


def value_at(points: list, seconds: float, duration: float) -> Optional[float]:
    """Curve value at `seconds` by linear interpolation; None outside the video or for an empty curve."""
    if not points or duration <= 0 or seconds < 0 or seconds > duration:
        return None
    x = seconds / duration
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


def retention_marks(points: list, duration: float, marks=MARKS) -> dict:
    """{second: ratio} at the fixed marks that fall inside the video."""
    return {t: value_at(points, t, duration) for t in marks if t <= duration and points}


def opening_loss(points: list, duration: float, at: float = OPENING_S) -> Optional[float]:
    """Curve start minus the value at 30 s (ratio units); None when the video is shorter than 30 s."""
    later = value_at(points, at, duration)
    return None if later is None or not points else points[0][1] - later


def steepest_drops(points: list, duration: float, after: float = OPENING_S, n: int = DROPS_N) -> list:
    """The n steepest drops that start at or after `after` seconds, by loss per second.

    Each pick starts as one curve step and grows into neighbouring steps that drop at least half as fast (up to
    20 s or three steps, whichever is longer), so one cliff spanning several steps is one entry while a steady
    slide stays a series of short pieces. Values are percentage points (pp).
    """
    segs = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        t0, t1 = ratio_to_seconds(x0, duration), ratio_to_seconds(x1, duration)
        if t0 >= after and t1 > t0:
            segs.append((t0, t1, (y0 - y1) * 100))
    if not segs:
        return []
    max_span = max(20.0, 3 * (segs[0][1] - segs[0][0]))

    def rate(i: int) -> float:
        return segs[i][2] / (segs[i][1] - segs[i][0])

    used = [False] * len(segs)
    drops = []
    for i in sorted(range(len(segs)), key=lambda k: -rate(k)):
        if len(drops) == n:
            break
        if used[i] or segs[i][2] <= 0:
            continue
        lo = hi = i
        used[i] = True
        while lo > 0 and not used[lo - 1] and rate(lo - 1) >= rate(i) / 2 \
                and segs[hi][1] - segs[lo - 1][0] <= max_span:
            lo -= 1
            used[lo] = True
        while hi + 1 < len(segs) and not used[hi + 1] and rate(hi + 1) >= rate(i) / 2 \
                and segs[hi + 1][1] - segs[lo][0] <= max_span:
            hi += 1
            used[hi] = True
        start, end = segs[lo][0], segs[hi][1]
        loss = sum(s[2] for s in segs[lo:hi + 1])
        drops.append({"start": start, "end": end, "loss_pp": loss, "per_second_pp": loss / (end - start)})
    return drops


def rewatch_bumps(points: list, duration: float, min_rise_pp: float = BUMP_MIN_PP) -> list:
    """Spans where the curve rises by more than `min_rise_pp` points: people rewound. Clip/Short candidates."""
    bumps, run_start = [], None
    for i in range(1, len(points) + 1):
        rising = i < len(points) and points[i][1] > points[i - 1][1]
        if rising and run_start is None:
            run_start = i - 1
        elif not rising and run_start is not None:
            rise = (points[i - 1][1] - points[run_start][1]) * 100
            if rise > min_rise_pp:
                bumps.append({"start": ratio_to_seconds(points[run_start][0], duration),
                              "end": ratio_to_seconds(points[i - 1][0], duration),
                              "rise_pp": rise, "label": "clip/Short candidate"})
            run_start = None
    return sorted(bumps, key=lambda b: -b["rise_pp"])


def parse_chapters(description) -> list:
    """Timestamp lines in a description (`0:00 Intro`, `1:02:03 Q&A`) -> [(seconds, title)]."""
    out = []
    for line in (description or "").splitlines():
        m = _CHAPTER.match(line)
        if m and int(m.group(3)) < 60:
            out.append((int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3)), m.group(4).strip()))
    return out


def chapter_sections(description, duration: float) -> list:
    """Sections from description chapters (at least two, increasing); [] when there are none."""
    chapters = [c for c in parse_chapters(description) if c[0] < duration]
    if len(chapters) < 2 or any(b[0] <= a[0] for a, b in zip(chapters, chapters[1:])):
        return []
    sections = []
    if chapters[0][0] > 0:
        sections.append({"id": "ch0", "start": 0.0, "end": float(chapters[0][0]), "text": "(before the first chapter)"})
    for i, (start, title) in enumerate(chapters):
        end = chapters[i + 1][0] if i + 1 < len(chapters) else duration
        sections.append({"id": f"ch{i + 1}", "start": float(start), "end": float(end), "text": title})
    return sections


def load_timeline(data) -> list:
    """Validates `--timeline` JSON: [{"id", "start", "end", "text"?}] with end > start. ValueError if bad."""
    if not isinstance(data, list) or not data:
        raise ValueError("the timeline must be a non-empty JSON list of {id, start, end, text?}")
    sections = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "start" not in item or "end" not in item:
            raise ValueError(f"timeline item {i + 1} needs start and end")
        start, end = item["start"], item["end"]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (start, end)) or end <= start:
            raise ValueError(f"timeline item {i + 1}: start and end must be seconds with end > start")
        sections.append({"id": str(item.get("id", f"s{i + 1}")), "start": float(start), "end": float(end),
                         "text": str(item.get("text", ""))})
    return sorted(sections, key=lambda s: s["start"])


def parse_srt(text: str) -> list:
    """SRT captions -> [{"start", "end", "text"}] in seconds, sorted by start."""
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", (text or "").lstrip("﻿")):
        lines = [ln.strip() for ln in block.strip().splitlines()]
        for k, line in enumerate(lines):
            m = _SRT_TIME.search(line)
            if m:
                g = [int(v) for v in m.groups()]
                start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 10 ** len(m.group(4))
                end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 10 ** len(m.group(8))
                words = re.sub(r"<[^>]+>|\{[^}]+\}", "", " ".join(lines[k + 1:])).strip()
                if words:
                    cues.append({"start": start, "end": end, "text": re.sub(r"\s+", " ", words)})
                break
    return sorted(cues, key=lambda c: c["start"])


def srt_windows(cues: list, duration: float, window: float = WINDOW_S) -> list:
    """Captions grouped into ~30 s sections, cut at cue boundaries."""
    if not cues:
        return []
    groups, current, current_start = [], [], 0.0
    for cue in cues:
        if current and cue["start"] >= current_start + window:
            groups.append((current_start, current))
            current, current_start = [], cue["start"]
        current.append(cue)
    groups.append((current_start, current))
    sections = []
    for i, (start, group) in enumerate(groups):
        end = groups[i + 1][0] if i + 1 < len(groups) else max(duration, group[-1]["end"])
        text = " ".join(c["text"] for c in group)
        sections.append({"id": f"w{i + 1}", "start": start, "end": min(end, duration) if duration else end,
                         "text": text[:120] + ("..." if len(text) > 120 else "")})
    return [s for s in sections if s["end"] > s["start"]]


def fixed_windows(duration: float, window: float = WINDOW_S) -> list:
    out, start, i = [], 0.0, 1
    while start < duration:
        end = min(duration, start + window)
        out.append({"id": f"w{i}", "start": start, "end": end, "text": ""})
        start, i = end, i + 1
    return out


def section_losses(points: list, duration: float, sections: list) -> list:
    """Retention at each section's start and end, the loss and the loss per minute (percentage points).

    `coarse` marks sections shorter than one curve step: their two values are interpolated from the same pair of
    points, so they are not a measurement on their own. `opening` marks sections that start inside the first 30 s.
    """
    step = duration / len(points) if points else duration
    rows = []
    for s in sections:
        start, end = max(0.0, s["start"]), min(duration, s["end"])
        if end <= start:
            continue
        a, b = value_at(points, start, duration), value_at(points, end, duration)
        if a is None or b is None:
            continue
        loss = (a - b) * 100
        rows.append({**s, "start": start, "end": end, "at_start_pct": a * 100, "at_end_pct": b * 100,
                     "loss_pp": loss, "loss_per_min_pp": loss / (end - start) * 60,
                     "coarse": (end - start) < step, "opening": start < OPENING_S})
    return rows


def rank_sections(rows: list) -> list:
    """Highest loss per minute first; each row gets its rank."""
    ranked = sorted(rows, key=lambda r: -r["loss_per_min_pp"])
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked


def quotes_near(cues: list, start: float, end: float, margin: float = QUOTE_MARGIN_S) -> list:
    """Caption lines that overlap [start - margin, end + margin]."""
    return [c["text"] for c in cues if c["end"] >= start - margin and c["start"] <= end + margin]


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def totals_from_report(report: dict) -> dict:
    names = [h.get("name") for h in (report or {}).get("columnHeaders") or []]
    rows = (report or {}).get("rows") or []
    row = rows[0] if rows else []

    def get(name):
        try:
            return row[names.index(name)]
        except (ValueError, IndexError):
            return None

    return {"views": get("views"), "average_view_duration_s": get("averageViewDuration"),
            "average_view_percentage": get("averageViewPercentage")}


def analyze(video_id: str, video: dict, curve_report: dict, totals_report: dict, sections: list,
            sections_source: str, cues: Optional[list] = None) -> dict:
    """Everything the report says, from the raw API answers. Pure: no network, no files."""
    snippet = video.get("snippet") or {}
    duration = parse_iso_duration((video.get("contentDetails") or {}).get("duration", ""))
    watch = curve_points(curve_report, "audienceWatchRatio")
    relative = curve_points(curve_report, "relativeRetentionPerformance")
    cues = cues or []

    def pct(v):
        return None if v is None else round(v * 100, 1)

    loss = opening_loss(watch, duration)
    drops = steepest_drops(watch, duration)
    for d in drops:
        d["said"] = quotes_near(cues, d["start"], d["end"]) if cues else []
    report = {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "published": (snippet.get("publishedAt") or "")[:10],
        "duration_s": duration,
        "views_data_api": _int((video.get("statistics") or {}).get("viewCount")),
        "analytics": totals_from_report(totals_report),
        "curve_points": len(watch),
        "curve_step_s": round(duration / len(watch), 2) if watch else None,
        "retention_at_pct": {str(t): pct(v) for t, v in retention_marks(watch, duration).items()},
        "relative_retention": ({str(t): (None if value_at(relative, t, duration) is None
                                         else round(value_at(relative, t, duration), 2)) for t in (10, 30)}
                               if relative else None),
        "opening": {"start_pct": pct(watch[0][1]) if watch else None,
                    "at_30s_pct": pct(value_at(watch, OPENING_S, duration)),
                    "loss_pp": None if loss is None else round(loss * 100, 1)},
        "steepest_drops": drops,
        "rewatch_bumps": rewatch_bumps(watch, duration),
        "sections_source": sections_source,
        "sections": rank_sections(section_losses(watch, duration, sections)),
        "note": AUTOPLAY_NOTE,
    }
    return report


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def render_text(r: dict) -> str:
    lines = [f"Retention: \"{r['title']}\" ({r['video_id']})",
             f"published {r['published'] or '?'}, duration {fmt_time(r['duration_s'])}, Data API views "
             f"{r['views_data_api'] if r['views_data_api'] is not None else '?'}"]
    a = r["analytics"]
    if a.get("views") is not None:
        avg = a.get("average_view_duration_s")
        lines.append(f"Analytics: {a['views']} views, average view "
                     f"{fmt_time(avg) if avg is not None else '?'} ({a.get('average_view_percentage') or 0:.1f}%)")
    lines.append(f"curve: {r['curve_points']} points, one every {r['curve_step_s']} s")
    lines += ["", AUTOPLAY_NOTE, "", "OPENING"]
    o = r["opening"]
    if o["loss_pp"] is not None:
        lines.append(f"  start {o['start_pct']:.1f}% -> 30 s {o['at_30s_pct']:.1f}%: {o['loss_pp']:.1f} points lost "
                     "by 30 s")
    else:
        lines.append("  the video is shorter than 30 s")
    rel = r["relative_retention"]
    if rel:
        shown = ", ".join(f"{t} s {v:.2f}" for t, v in rel.items() if v is not None)
        lines.append(f"  relative retention: {shown} (0.50 = median of similar-length videos; lower is worse)")
    else:
        lines.append("  relative retention: not returned for this video")
    lines += ["", "RETENTION AT", "  " + "  ".join(f"{t} s {v:.1f}%" for t, v in r["retention_at_pct"].items()
                                                   if v is not None)]
    lines += ["", "STEEPEST DROPS AFTER 30 s"]
    if not r["steepest_drops"]:
        lines.append("  none")
    for i, d in enumerate(r["steepest_drops"], 1):
        lines.append(f"  {i}. {fmt_time(d['start'])}-{fmt_time(d['end'])}  -{d['loss_pp']:.1f} points "
                     f"({d['per_second_pp']:.2f} points/s)")
        for q in d.get("said") or []:
            lines.append(f"       said: \"{q}\"")
    lines += ["", "REWATCH BUMPS (clip/Short candidates)"]
    if not r["rewatch_bumps"]:
        lines.append("  none")
    for i, b in enumerate(r["rewatch_bumps"][:BUMPS_SHOWN], 1):
        lines.append(f"  {i}. {fmt_time(b['start'])}-{fmt_time(b['end'])}  +{b['rise_pp']:.1f} points")
    if len(r["rewatch_bumps"]) > BUMPS_SHOWN:
        lines.append(f"  ... {len(r['rewatch_bumps']) - BUMPS_SHOWN} smaller rises (all in --json); on a video with "
                     "few views a rise under ~1 point can be one or two people")
    lines += ["", f"SECTIONS (from {r['sections_source']}), ranked by loss per minute",
              "  rank  time          at start  at end   lost  per min  section"]
    for s in r["sections"]:
        flags = (" [opening]" if s["opening"] else "") + (" [coarse: shorter than one curve step]"
                                                          if s["coarse"] else "")
        span = f"{fmt_time(s['start'])}-{fmt_time(s['end'])}"
        label = s.get("text") or s.get("id", "")
        lines.append(f"  {s['rank']:>4}  {span:<12}  {s['at_start_pct']:>6.1f}%  {s['at_end_pct']:>6.1f}%  "
                     f"{s['loss_pp']:>5.1f}  {s['loss_per_min_pp']:>7.1f}  {label[:70]}{flags}")
    return "\n".join(lines) + "\n"


# --- network ------------------------------------------------------------------------------------------------------

def tubecrew_home() -> Path:
    env = os.environ.get("TUBECREW_HOME")
    return Path(env).expanduser() if env else Path.home() / ".tubecrew"


def token_path(profile: str) -> Path:
    return tubecrew_home() / "tokens" / f"{profile}-analytics.json"


def login_command(profile: str) -> str:
    script = sys.argv[0] if sys.argv and sys.argv[0].endswith(".py") else "retention.py"
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
        hint = " (the video must belong to the channel this token was created for)" if r.status_code == 403 else ""
        raise ApiError(r.status_code, f"HTTP {r.status_code} from {url.split('?')[0]}{hint}: {str(r.text)[:300]}")
    return r.json()


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def fetch_video_info(session, token: str, video_id: str) -> Optional[dict]:
    data = _get(session, token, f"{DATA_API}/videos",
                {"part": "snippet,contentDetails,statistics", "id": video_id})
    items = data.get("items") or []
    return items[0] if items else None


def fetch_curve(session, token: str, video_id: str, start: str, end: str) -> dict:
    """Retention curve; falls back to audienceWatchRatio alone if the relative metric is refused (HTTP 400)."""
    params = {"ids": "channel==MINE", "startDate": start, "endDate": end, "filters": f"video=={video_id}",
              "dimensions": "elapsedVideoTimeRatio", "metrics": "audienceWatchRatio,relativeRetentionPerformance"}
    try:
        return _get(session, token, ANALYTICS_API, params)
    except ApiError as e:
        if e.status != 400:
            raise
        return _get(session, token, ANALYTICS_API, {**params, "metrics": "audienceWatchRatio"})


def fetch_totals(session, token: str, video_id: str, start: str, end: str) -> dict:
    return _get(session, token, ANALYTICS_API,
                {"ids": "channel==MINE", "startDate": start, "endDate": end, "filters": f"video=={video_id}",
                 "metrics": "views,averageViewDuration,averageViewPercentage"})


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
        data = _get(new_session(), cred.token, f"{DATA_API}/channels", {"part": "id,snippet", "mine": "true"})
        for item in data.get("items") or []:
            print(f"Channel: {(item.get('snippet') or {}).get('title', '?')} ({item.get('id')})")
    except Exception as e:  # noqa: BLE001 - the token is saved; the channel name is a courtesy
        print(f"Could not read the channel name ({type(e).__name__}).")
    return 0


# --- main ---------------------------------------------------------------------------------------------------------

def _write_out(path: Path, report: dict, as_json: bool) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n" if as_json else render_text(report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    ap = argparse.ArgumentParser(description="Where viewers leave a YouTube video, by section.")
    ap.add_argument("video_id", nargs="?", help="the 11-character video id")
    ap.add_argument("--profile", default="default", help="credential profile, one per channel (default: default)")
    ap.add_argument("--token", type=Path, help="use this authorized-user token JSON instead of the profile token")
    ap.add_argument("--srt", type=Path, help="captions of the video, to quote what was said at each drop")
    ap.add_argument("--timeline", type=Path, help="sections JSON: [{id, start, end, text?}] in seconds")
    ap.add_argument("--json", action="store_true", help="print the machine-readable report")
    ap.add_argument("--out", type=Path, help="also write the report here (.json = JSON, otherwise text)")
    ap.add_argument("--login", action="store_true", help="open the browser once to authorize this profile")
    a = ap.parse_args(argv)

    def usage(message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return 2

    if not _PROFILE.fullmatch(a.profile):
        return usage("--profile may only use letters, digits, '.', '_' and '-'")
    try:
        if a.login:
            return do_login(a.profile)
        if not a.video_id:
            return usage("give the video id (or --login)")
        if not _VIDEO_ID.fullmatch(a.video_id):
            return usage(f"{a.video_id!r} is not an 11-character video id")

        timeline = cues = None
        if a.timeline:
            try:
                timeline = load_timeline(json.loads(a.timeline.read_text(encoding="utf-8-sig")))
            except (OSError, ValueError) as e:
                return usage(f"--timeline {a.timeline.name}: {e}")
        if a.srt:
            try:
                cues = parse_srt(a.srt.read_text(encoding="utf-8-sig", errors="replace"))
            except OSError as e:
                return usage(f"--srt {a.srt.name}: {e}")
            if not cues:
                return usage(f"--srt {a.srt.name}: no caption cues found")

        login_cmd = login_command(a.profile)
        cred = load_credentials(a.token.expanduser() if a.token else token_path(a.profile), login_cmd)
        session = new_session()
        try:
            video = fetch_video_info(session, cred.token, a.video_id)
            if video is None:
                return usage(f"video {a.video_id} not found, or not visible to this account")
            today = dt.date.today().isoformat()
            start = min((video.get("snippet") or {}).get("publishedAt", today)[:10], today)
            curve = fetch_curve(session, cred.token, a.video_id, start, today)
            if not (curve or {}).get("rows"):
                if a.json:
                    print(json.dumps({"video_id": a.video_id, "data": False, "message": NO_DATA}))
                else:
                    print(NO_DATA)
                return 0
            totals = fetch_totals(session, cred.token, a.video_id, start, today)
        except ApiError as e:
            return usage(str(e))
        except OSError as e:
            return usage(f"network error ({type(e).__name__}); check the connection and try again")

        try:
            duration = parse_iso_duration((video.get("contentDetails") or {}).get("duration", ""))
        except ValueError as e:
            return usage(str(e))
        if timeline:
            sections, source = timeline, f"timeline {a.timeline.name}"
        elif chapter_sections((video.get("snippet") or {}).get("description"), duration):
            sections, source = chapter_sections(video["snippet"]["description"], duration), "description chapters"
        elif cues:
            sections, source = srt_windows(cues, duration), f"captions {a.srt.name}, ~30 s windows"
        else:
            sections, source = fixed_windows(duration), "fixed 30 s windows"
        report = analyze(a.video_id, video, curve, totals, sections, source, cues)
        report["data"] = True
    except EnvError as e:
        return usage(str(e))

    print(json.dumps(report, ensure_ascii=False, indent=2) if a.json else render_text(report), end="" if not a.json
          else "\n")
    if a.out:
        try:
            _write_out(a.out, report, a.out.suffix.lower() == ".json")
        except OSError as e:
            return usage(f"could not write --out: {e}")
        print(f"wrote {a.out.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
