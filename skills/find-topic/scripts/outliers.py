"""Find the videos that beat their own channel: views divided by that channel's own median.

Why: raw views only rank channel size. A video is interesting when it did far better than the same channel
usually does, so every video is measured against its own channel's median (and the median is only reported when
there are enough videos to make it one).

How it works:
  1. Pull each channel's long-video tab (Shorts live on a separate tab) with
     `yt-dlp --flat-playlist -J --playlist-end N https://www.youtube.com/@handle/videos`. The flat pull asks for
     approximate dates ("3 weeks ago"); ages built from them are marked with "~".
  2. Per channel: n and median views. Channels with fewer than 4 videos are skipped.
  3. multiple = views / channel median; keep videos at or above --min-multiple.
  4. --dates K fetches full metadata (`yt-dlp -J <video url>`) for the top K outliers only, for exact dates.
     Under 7 days old is "too new" (views not settled); over 365 days is "old" (weak evidence). An approximate
     age is a lower bound ("1 year ago" means 12-23 months), so an approximate 365 days already counts as old.
  View counts come from the channel page, rounded the way YouTube shows them ("63M views" -> 63,000,000).

Run:
  python outliers.py @channelA https://www.youtube.com/@channelB --last 60 --min-multiple 2 --dates 10
  python outliers.py @channelA @channelB --save catalog.json      # keep the raw catalog
  python outliers.py --from-json catalog.json --json              # no network at all

The catalog file maps channel name -> list of {id, title, views, duration, upload_date?, date_approx?}.
Keys starting with "_" are ignored. yt-dlp is taken from PATH, or `python -m yt_dlp` when that is installed.

Exit codes: 0 report printed (this is a research tool, not a gate), 2 usage or environment error
(no channels, yt-dlp missing, unreadable catalog, every channel failed to load).
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - older or redirected streams
        pass

MIN_N = 4
TOO_NEW_DAYS = 7
OLD_DAYS = 365
LEADS_LINE = ("Outliers are leads, not topics. A topic is double-sourced only when at least two independent channels "
              "beat their own median on it.")
THIN_REASON = "a median of {n} video{s} is not a median; need at least %d" % MIN_N
OTHER_TABS = {"featured", "shorts", "streams", "live", "playlists", "community", "about", "podcasts", "releases",
              "courses", "store", "posts"}


class UsageError(Exception):
    """Bad input or a broken environment (exit 2)."""


# ---------------------------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------------------------

def median(values):
    """Median of a list of numbers; None for an empty list."""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return statistics.median(vals)


def normalize_channel_url(text: str) -> str:
    """Turn '@handle', a channel id or any channel URL into its long-video tab URL."""
    s = text.strip()
    if not s:
        raise UsageError("empty channel argument")
    if s.startswith("@"):
        s = "https://www.youtube.com/" + s  # "@handle/shorts" must still land on the videos tab
    if re.fullmatch(r"UC[\w-]{22}", s):
        return f"https://www.youtube.com/channel/{s}/videos"
    if not re.match(r"https?://", s, re.I):
        if s.lower().startswith(("www.youtube.com", "youtube.com", "m.youtube.com")):
            s = "https://" + s
        else:
            raise UsageError(f"not a channel URL or @handle: {text!r}")
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    last = s.rsplit("/", 1)[-1].lower()
    if last == "videos":
        return s
    if last in OTHER_TABS:
        s = s.rsplit("/", 1)[0]
    return s + "/videos"


def _iso_date(value) -> str | None:
    """'20260105', '2026-01-05' or an epoch timestamp -> '2026-01-05'."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", s)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def parse_flat_playlist(data: dict, fallback_name: str = "") -> tuple[str, list[dict]]:
    """Parse `yt-dlp --flat-playlist -J` output into (channel name, videos).

    Entries without a view count (upcoming premieres, members-only) are dropped; so are live and upcoming streams.
    A nested tab (the channel root returns tabs as sub-playlists) is flattened.
    """
    if not isinstance(data, dict):
        raise ValueError("yt-dlp output is not a JSON object")
    name = data.get("channel") or data.get("uploader") or fallback_name or data.get("title") or "unknown channel"
    videos: list[dict] = []
    stack = list(data.get("entries") or [])
    while stack:
        e = stack.pop(0)
        if not isinstance(e, dict):
            continue
        if e.get("_type") == "playlist" and e.get("entries"):
            stack = list(e["entries"]) + stack
            continue
        vid = e.get("id")
        views = e.get("view_count")
        if not vid or views is None or e.get("live_status") in ("is_upcoming", "is_live"):
            continue
        item = {"id": vid, "title": e.get("title") or "", "views": int(views), "duration": e.get("duration")}
        exact = _iso_date(e.get("upload_date"))
        approx = None if exact else _iso_date(e.get("timestamp") or e.get("release_timestamp"))
        if exact:
            item["upload_date"] = exact
        elif approx:
            item["upload_date"] = approx
            item["date_approx"] = True
        videos.append(item)
    return name, videos


def parse_video_metadata(data: dict) -> str | None:
    """Exact upload date ('YYYY-MM-DD') from `yt-dlp -J <video url>` output."""
    if not isinstance(data, dict):
        return None
    return _iso_date(data.get("upload_date")) or _iso_date(data.get("timestamp")) or _iso_date(
        data.get("release_timestamp"))


def channel_stats(videos: list[dict]) -> dict:
    """n and median views of one channel's videos."""
    views = [v["views"] for v in videos if v.get("views") is not None]
    return {"n": len(views), "median": median(views)}


def age_days(upload_date: str | None, today: dt.date) -> int | None:
    iso = _iso_date(upload_date)
    if not iso:
        return None
    return (today - dt.date.fromisoformat(iso)).days


def age_label(days: int | None, approx: bool = False) -> str:
    """'too new' under 7 days (views not settled), 'old' over 365 days (weak evidence), 'no date', or ''.

    An approximate age is a lower bound ("1 year ago" is 365-729 days), so approximate 365 already counts as old.
    """
    if days is None:
        return "no date"
    if days < TOO_NEW_DAYS:
        return "too new"
    if days > OLD_DAYS or (approx and days >= OLD_DAYS):
        return "old"
    return ""


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def find_outliers(catalog: dict, min_multiple: float = 2.0, today: dt.date | None = None,
                  min_n: int = MIN_N) -> tuple[list[dict], list[dict]]:
    """Return (outliers sorted by multiple, per-channel summaries).

    catalog: channel name -> list of video dicts. Channels with n < min_n or a zero median are skipped.
    """
    today = today or dt.date.today()
    outliers: list[dict] = []
    summaries: list[dict] = []
    for channel, videos in catalog.items():
        if str(channel).startswith("_"):
            continue
        videos = [v for v in (videos or []) if isinstance(v, dict) and v.get("views") is not None]
        st = channel_stats(videos)
        summary = {"channel": channel, "n": st["n"], "median": st["median"], "outliers": 0, "top_multiple": None,
                   "skipped": None}
        if st["n"] < min_n:
            summary["skipped"] = THIN_REASON.format(n=st["n"], s="" if st["n"] == 1 else "s")
            summaries.append(summary)
            continue
        if not st["median"]:
            summary["skipped"] = "median views is 0; multiples are undefined"
            summaries.append(summary)
            continue
        for v in videos:
            multiple = v["views"] / st["median"]
            if multiple < min_multiple:
                continue
            days = age_days(v.get("upload_date"), today)
            approx = bool(v.get("date_approx"))
            outliers.append({
                "channel": channel, "id": v["id"], "title": v.get("title", ""), "url": video_url(v["id"]),
                "views": v["views"], "duration": v.get("duration"), "multiple": round(multiple, 2),
                "channel_median": st["median"], "channel_n": st["n"],
                "upload_date": _iso_date(v.get("upload_date")), "date_approx": approx,
                "age_days": days, "age_label": age_label(days, approx),
            })
            summary["outliers"] += 1
            if summary["top_multiple"] is None or multiple > summary["top_multiple"]:
                summary["top_multiple"] = round(multiple, 2)
        summaries.append(summary)
    outliers.sort(key=lambda o: (-o["multiple"], -o["views"]))
    return outliers, summaries


def refresh_ages(outliers: list[dict], today: dt.date) -> None:
    for o in outliers:
        o["age_days"] = age_days(o.get("upload_date"), today)
        o["age_label"] = age_label(o["age_days"], bool(o.get("date_approx")))


def load_catalog(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # -sig: Windows editors may add a BOM
    except FileNotFoundError:
        raise UsageError(f"catalog not found: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read catalog {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"catalog {path} must be an object: channel -> list of videos")
    out = {}
    for channel, videos in data.items():
        if str(channel).startswith("_"):
            continue
        if not isinstance(videos, list):
            raise UsageError(f"catalog {path}: {channel!r} must map to a list of videos")
        for v in videos:
            views = v.get("views") if isinstance(v, dict) else None
            if isinstance(v, dict) and (not v.get("id") or isinstance(views, bool)
                                        or not (views is None or isinstance(views, (int, float)))):
                raise UsageError(f"catalog {path}: {channel!r} has a video without an id or with non-numeric "
                                 f"views: {json.dumps(v, ensure_ascii=False)[:120]}")
        out[channel] = videos
    return out


# ---------------------------------------------------------------------------------------------------------------
# yt-dlp (network)
# ---------------------------------------------------------------------------------------------------------------

def ytdlp_command() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise UsageError("yt-dlp not found. Install it (pip install yt-dlp) or put it on PATH.")


def _run_json(cmd: list[str], timeout: int = 600) -> dict:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp timed out") from None
    if proc.returncode != 0 or not proc.stdout.strip():
        err = (proc.stderr or "").strip().splitlines()
        raise RuntimeError(err[-1] if err else f"yt-dlp exited with {proc.returncode}")
    return json.loads(proc.stdout)


def fetch_channel(base_cmd: list[str], url: str, last: int) -> tuple[str, list[dict]]:
    data = _run_json(base_cmd + ["--flat-playlist", "-J", "--playlist-end", str(last), "--no-warnings",
                                 "--extractor-args", "youtubetab:approximate_date", url])
    return parse_flat_playlist(data, fallback_name=url)


def fetch_upload_date(base_cmd: list[str], video_id: str) -> str | None:
    data = _run_json(base_cmd + ["-J", "--no-playlist", "--skip-download", "--no-warnings", video_url(video_id)],
                     timeout=180)
    return parse_video_metadata(data)


# ---------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------

def _fmt_int(x) -> str:
    if x is None:
        return "?"
    return f"{int(round(x)):,}"


def _fmt_age(o: dict) -> str:
    if o["age_days"] is None:
        return "no date"
    txt = ("~" if o.get("date_approx") else "") + f"{o['age_days']}d"
    return f"{txt} {o['age_label']}".strip()


def print_human(outliers: list[dict], summaries: list[dict], min_multiple: float, notes: list[str]) -> None:
    usable = [s for s in summaries if not s["skipped"] and not s.get("error")]
    print(f"Outliers at or above {min_multiple:g}x their own channel median: {len(outliers)} "
          f"from {len(usable)} channel{'s' if len(usable) != 1 else ''}")
    if outliers:
        print(f"  {'multiple':>8}  {'views':>12}  {'channel median (n)':>20}  {'age':<14}  title")
        for o in outliers:
            med = f"{_fmt_int(o['channel_median'])} ({o['channel_n']})"
            print(f"  {o['multiple']:>7.1f}x  {_fmt_int(o['views']):>12}  {med:>20}  {_fmt_age(o):<14}  "
                  f"[{o['channel']}] {o['title']}")
            print(f"  {'':>8}  {o['url']}")
    print()
    print("Per channel")
    for s in summaries:
        if s.get("error"):
            print(f"  {s['channel']}: not loaded ({s['error']})")
        elif s["skipped"]:
            print(f"  {s['channel']}: skipped, n={s['n']} ({s['skipped']})")
        else:
            top = f", top {s['top_multiple']:.1f}x" if s["top_multiple"] is not None else ""
            print(f"  {s['channel']}: n={s['n']}, median {_fmt_int(s['median'])}, {s['outliers']} outlier"
                  f"{'s' if s['outliers'] != 1 else ''}{top}")
    for note in notes:
        print(f"Note: {note}")
    print()
    print(LEADS_LINE)


# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("channels", nargs="*", help="channel URLs, @handles or channel ids")
    ap.add_argument("--from-json", metavar="CATALOG", help="read a saved catalog instead of the network")
    ap.add_argument("--last", type=int, default=60, help="newest long videos per channel to pull (default 60)")
    ap.add_argument("--min-multiple", type=float, default=2.0, help="keep videos at or above this multiple")
    ap.add_argument("--dates", type=int, default=10, metavar="K",
                    help="fetch exact upload dates for the top K outliers (default 10; 0 = off; never with "
                         "--from-json alone)")
    ap.add_argument("--save", metavar="CATALOG", help="write the catalog (channel -> videos) to this file")
    ap.add_argument("--json", action="store_true", help="print a machine-readable result")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2

    try:
        if not args.channels and not args.from_json:
            raise UsageError("give at least one channel (@handle or URL) or --from-json CATALOG")
        if args.last < 1:
            raise UsageError("--last must be at least 1")
        if args.min_multiple <= 0:
            raise UsageError("--min-multiple must be positive")
        if args.dates < 0:
            raise UsageError("--dates must be 0 or more")

        catalog: dict = {}
        if args.from_json:
            catalog.update(load_catalog(Path(args.from_json)))
        from_file = set(catalog)  # a fresh pull of a saved channel replaces it instead of adding "Name (2)"
        errors: list[dict] = []
        base_cmd = None
        if args.channels:
            urls = [normalize_channel_url(c) for c in args.channels]
            base_cmd = ytdlp_command()
            for url in urls:
                print(f"Pulling {url} ...", file=sys.stderr)
                try:
                    name, videos = fetch_channel(base_cmd, url, args.last)
                except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                    errors.append({"channel": url, "error": str(exc)})
                    continue
                key, i = name, 2
                while key in catalog and key not in from_file:
                    key, i = f"{name} ({i})", i + 1
                from_file.discard(key)
                catalog[key] = videos
            if not catalog:
                raise UsageError("no channel could be loaded: " + "; ".join(e["error"] for e in errors))
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    today = dt.date.today()
    outliers, summaries = find_outliers(catalog, args.min_multiple, today)
    notes: list[str] = []

    if base_cmd and args.dates:
        by_id = {(ch, v["id"]): v for ch, vids in catalog.items() for v in vids if isinstance(v, dict)}
        fetched = 0
        for o in outliers[:args.dates]:
            try:
                exact = fetch_upload_date(base_cmd, o["id"])
            except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
                notes.append(f"no exact date for {o['id']}: {exc}")
                continue
            if exact:
                fetched += 1
                o["upload_date"], o["date_approx"] = exact, False
                v = by_id.get((o["channel"], o["id"]))
                if v is not None:
                    v["upload_date"] = exact
                    v.pop("date_approx", None)
        refresh_ages(outliers, today)
        notes.append(f"exact dates fetched for {fetched} of the top {min(args.dates, len(outliers))} outliers; "
                     "ages marked ~ are approximate")
    elif any(o.get("date_approx") for o in outliers):
        notes.append("ages marked ~ are approximate")

    summaries.extend({"channel": e["channel"], "n": 0, "median": None, "outliers": 0, "top_multiple": None,
                      "skipped": None, "error": e["error"]} for e in errors)

    if args.save:
        try:
            out = Path(args.save)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            notes.append(f"catalog saved to {args.save}")
        except OSError as exc:
            print(f"error: cannot write {args.save}: {exc}", file=sys.stderr)
            return 2

    if args.json:
        print(json.dumps({"min_multiple": args.min_multiple, "min_n": MIN_N, "today": today.isoformat(),
                          "outliers": outliers, "channels": summaries, "notes": notes, "note": LEADS_LINE},
                         ensure_ascii=False, indent=2))
    else:
        print_human(outliers, summaries, args.min_multiple, notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
