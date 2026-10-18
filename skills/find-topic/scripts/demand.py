"""Measure demand for a topic with free sources: what people type, whether a small channel can be seen, and how
much curiosity the subject draws compared with a topic that already worked for you.

Why: a keyword tool's volume is one opinion. Three free signals that measure different things catch most bad
bets: YouTube's own search suggestions (real phrasing and intent), the search results page (is there a step a
small channel can stand on) and Wikipedia pageviews (curiosity, as a multiple of a proven topic). When two demand
sources disagree by more than 3x, the topic is labelled "demand unclear" instead of "demand".

How it works:
  1. Suggestions: YouTube autocomplete for the query and every --variant. --expand adds "<query> a" .. "<query> z"
     and why/how/what/when prefixes. Suggestions that point at a film, a song, exam prep or another language
     market are flagged: that demand belongs to something else.
  2. Reachable step: the top --top results (default 18) of a YouTube search, read in full with
     `yt-dlp -J "ytsearch18:<query>"`. A step is a video from a channel under 50,000 subscribers with 10,000+ views.
     It counts only when it is fresh (uploaded within --fresh-days, default 365) and real: not a Short or anything
     under 3 minutes, not a stream replay, not a podcast episode, not a likely re-upload. The gate is at least one
     fresh, real step on the query or on any variant (one phrasing is never enough).
  3. Wikipedia: daily pageviews (user traffic, all access) of --wiki ARTICLE over the last --days (default 90) from
     the Wikimedia REST API, divided by the same for --baseline ARTICLE (one of your proven topics; repeat it to
     average several). Redirects are followed first, so a redirect page is never measured by mistake.
  4. Triangulation: the Wikipedia multiple and every --source NAME=TOPIC/BASELINE you read by hand (Google Trends
     with the YouTube Search property, a keyword tool's volume for both terms) are compared. A spread over 3x
     between any two is "demand unclear".

Limits: Wikipedia measures curiosity, not need. How-to and personal-money topics score low even when many people
need them, and pageviews say nothing about how crowded YouTube already is. Google Trends has no stable API, so it
stays a manual check (see references/demand.md).

Run:
  python demand.py "tacoma narrows bridge"
  python demand.py "tacoma narrows bridge" --variant "why the tacoma bridge collapsed" --top 18
  python demand.py "tacoma narrows bridge" --no-serp --expand
  python demand.py "tacoma narrows bridge" --wiki "Tacoma Narrows Bridge (1940)" --baseline "Golden Gate Bridge" \
      --source trends-yt=40/55 --json --out research/demand.json

Network (only in real runs, no API key): suggestqueries.google.com, YouTube through yt-dlp, wikimedia.org and
<lang>.wikipedia.org. Only the query and the article names are sent. yt-dlp is taken from PATH, or
`python -m yt_dlp` when that is installed.

Exit codes: 0 report printed and the step gate passed or was not requested, 1 the search results were read and no
query has a fresh, real step (a gap or a desert), 2 usage or environment error (no query, bad option, yt-dlp
missing, the search results or every measurement could not be read).
"""
from __future__ import annotations

import argparse
import datetime as dt
import http.client
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - older or redirected streams
        pass

SMALL_SUBS = 50_000      # under this a channel is "small": its success shows a newcomer can be seen there
STEP_VIEWS = 10_000      # the views that make a small channel's video a step instead of noise
SERP_TOP = 18            # about three screens of results; deeper results are rarely seen
FRESH_DAYS = 365         # an older step describes a results page that may have changed (outliers.py's "old" line)
SHORT_MAX_S = 180        # Shorts run up to 3 minutes; a step for long-form work has to be longer than that
WIKI_DAYS = 90           # a quarter smooths one-day news spikes and still reflects current interest
UNCLEAR_SPREAD = 3.0     # two sources more than 3x apart disagree about the topic, not about noise
WIKI_PAUSE_S = 0.6       # Wikimedia answers bursts with HTTP 429; a short pause between calls avoids it
SUGGEST_PAUSE_S = 0.2    # --expand sends ~30 suggestion calls; spacing them keeps the endpoint friendly
HTTP_TIMEOUT_S = 25
SERP_TIMEOUT_S = 900     # 18 full video reads can take several minutes on a slow connection
# Wikimedia asks every client for a descriptive User-Agent with a way to reach the maintainer.
USER_AGENT = "tubecrew-demand/0.3 (YouTube topic research; +https://github.com/berkayburakk/tubecrew)"

SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
WIKI_REST = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
EXPAND_PREFIXES = ("why", "how", "what", "when")
INTENT_MARKERS = {
    "film, music or games": re.compile(
        r"\b(?:movie|film|scene|trailer|song|lyrics|cast|season|netflix|anime|gameplay|game|meme)s?\b", re.I),
    "exam prep or another language market": re.compile(
        r"\b(?:in (?:hindi|urdu|tamil|telugu|bengali|marathi|malayalam|kannada|gujarati|punjabi|arabic|spanish|"
        r"portuguese|french|german)|gcse|igcse|a ?levels?|class \d{1,2}|grade \d{1,2}|upsc|ncert|cbse|mcqs?|"
        r"for kids)\b", re.I),
}
PODCAST_RE = re.compile(r"\bpodcasts?\b|\bfull episode\b|\bep\.?\s*#?\d+\b|^#\d+\b", re.I)
REUPLOAD_RE = re.compile(r"\bre-?upload(?:ed)?\b|\bfull (?:movie|film|documentary)\b", re.I)
STREAM_STATES = {"is_live", "was_live", "post_live", "is_upcoming"}
SHAPE_TEXT = {
    "reachable": "a small channel was seen here recently",
    "gap": "views exist, but no fresh, real step: big channels own it, or the steps are old or flagged",
    "desert": "no result reached %s views: little demand on search" % f"{STEP_VIEWS:,}",
}
CURIOSITY_LINE = ("Wikipedia measures curiosity, not need: how-to and personal-money topics score low even when "
                  "people need them, and pageviews say nothing about how crowded YouTube already is.")


class UsageError(Exception):
    """Bad input or a broken environment (exit 2)."""


# ---------------------------------------------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------------------------------------------

def _int(x) -> int | None:
    if isinstance(x, bool) or x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _iso_date(value) -> str | None:
    """'20260105', '2026-01-05' or an epoch timestamp -> '2026-01-05'."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    m = re.fullmatch(r"(\d{4})-?(\d{2})-?(\d{2})", str(value).strip())
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def unique_queries(query: str, variants: list[str] | None) -> list[str]:
    """The query first, then each variant once (case-insensitive)."""
    out, seen = [], set()
    for q in [query] + list(variants or []):
        q = " ".join((q or "").split())
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


# --- suggestions -----------------------------------------------------------------------------------------------

def suggest_url(query: str, hl: str | None = "en", gl: str | None = None) -> str:
    params = {"client": "firefox", "ds": "yt", "q": query}
    if hl:
        params["hl"] = hl
    if gl:
        params["gl"] = gl
    return SUGGEST_URL + "?" + urllib.parse.urlencode(params)


def parse_suggestions(text: str) -> list[str]:
    """Suggestions from the autocomplete answer: plain JSON ["q", ["a", "b"]] or the JSONP form name([...])."""
    s = (text or "").strip()
    m = re.match(r"^[\w.$]+\((.*)\)\s*;?\s*$", s, re.S)
    if m:
        s = m.group(1)
    data = json.loads(s)
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        raise ValueError("unexpected autocomplete answer")
    out: list[str] = []
    for item in data[1]:
        if isinstance(item, list) and item:
            item = item[0]
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out


def expand_queries(query: str) -> list[str]:
    """'<query> a' .. '<query> z' plus question prefixes: the long tail that the bare query hides."""
    return [f"{query} {c}" for c in "abcdefghijklmnopqrstuvwxyz"] + [f"{p} {query}" for p in EXPAND_PREFIXES]


def intent_flags(suggestions: list[str]) -> dict[str, list[str]]:
    """Suggestions whose wording points at another kind of demand, by category."""
    out: dict[str, list[str]] = {}
    for label, rx in INTENT_MARKERS.items():
        hits = [s for s in suggestions if rx.search(s)]
        if hits:
            out[label] = hits
    return out


# --- search results --------------------------------------------------------------------------------------------

def parse_serp(data: dict) -> list[dict]:
    """Rows from `yt-dlp -J "ytsearchN:<query>"` output; a failed entry keeps its rank but is left out."""
    entries = data.get("entries") if isinstance(data, dict) else None
    rows = []
    for rank, e in enumerate(entries or [], start=1):
        if not isinstance(e, dict) or not e.get("id"):
            continue
        rows.append({
            "rank": rank, "id": e["id"], "title": e.get("title") or "",
            "channel": e.get("channel") or e.get("uploader") or "",
            "subscribers": _int(e.get("channel_follower_count")), "views": _int(e.get("view_count")),
            "duration": e.get("duration") if isinstance(e.get("duration"), (int, float)) else None,
            "upload_date": _iso_date(e.get("upload_date")) or _iso_date(e.get("timestamp")),
            "live_status": e.get("live_status"), "was_live": bool(e.get("was_live")),
            "media_type": e.get("media_type"),
            "url": e.get("webpage_url") or f"https://www.youtube.com/watch?v={e['id']}",
        })
    return rows


def classify(row: dict, today: dt.date, fresh_days: int = FRESH_DAYS) -> dict:
    """Add size, step, freshness and the reasons a step would not be real (Short, stream, podcast, re-upload)."""
    flags = []
    dur = row.get("duration")
    if row.get("media_type") == "short" or "/shorts/" in (row.get("url") or ""):
        flags.append("Short")
    elif dur is not None and dur <= SHORT_MAX_S:
        flags.append("under 3 min")
    if row.get("live_status") in STREAM_STATES or row.get("was_live"):
        flags.append("stream replay")
    if PODCAST_RE.search(row.get("title") or "") or PODCAST_RE.search(row.get("channel") or ""):
        flags.append("podcast")
    if REUPLOAD_RE.search(row.get("title") or ""):
        flags.append("possible re-upload")
    iso = row.get("upload_date")
    age = (today - dt.date.fromisoformat(iso)).days if iso else None
    subs, views = row.get("subscribers"), row.get("views")
    small = subs is not None and 0 < subs < SMALL_SUBS
    step = small and views is not None and views >= STEP_VIEWS
    fresh = age is not None and age <= fresh_days
    row.update({"age_days": age, "small": small, "step": step, "fresh": fresh, "flags": flags,
                "real_step": bool(step and fresh and not flags)})
    return row


def serp_summary(rows: list[dict]) -> dict:
    """Shape of one results page: reachable (a fresh, real step), gap (views but no such step) or desert."""
    steps = [r for r in rows if r.get("step")]
    real = [r for r in steps if r.get("real_step")]
    hits = [r for r in rows if (r.get("views") or 0) >= STEP_VIEWS]
    shape = "reachable" if real else ("gap" if hits else "desert")
    return {"results": len(rows), "steps": len(steps), "real_steps": len(real),
            "stale_or_flagged_steps": len(steps) - len(real), "shape": shape,
            "unknown_size": sum(1 for r in rows if r.get("subscribers") is None),
            "top_age_days": rows[0].get("age_days") if rows else None}


# --- Wikipedia -------------------------------------------------------------------------------------------------

def parse_wiki_arg(text: str, default_lang: str = "en") -> tuple[str, str]:
    """'Some Article', 'Some_Article' or a full wikipedia.org URL -> (language, title with spaces)."""
    s = (text or "").strip()
    if not s:
        raise UsageError("empty Wikipedia article name")
    m = re.match(r"https?://([a-z][a-z\-]*)\.(?:m\.)?wikipedia\.org/wiki/([^?#]+)", s, re.I)
    if m:
        return m.group(1).lower(), urllib.parse.unquote(m.group(2)).replace("_", " ").strip()
    if re.match(r"https?://", s, re.I):
        raise UsageError(f"not a Wikipedia article URL: {text!r}")
    return default_lang, s.replace("_", " ")


def resolve_url(lang: str, title: str) -> str:
    params = {"action": "query", "format": "json", "formatversion": "2", "redirects": "1", "titles": title}
    return f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)


def parse_resolve(data: dict) -> str | None:
    """Canonical title after normalization and redirects, or None when the article does not exist."""
    pages = (data.get("query") or {}).get("pages") if isinstance(data, dict) else None
    if not pages or not isinstance(pages[0], dict):
        return None
    page = pages[0]
    if page.get("missing") or page.get("invalid"):
        return None
    return page.get("title")


def wiki_window(today: dt.date, days: int = WIKI_DAYS) -> tuple[dt.date, dt.date]:
    """The last `days` complete days: today is still being counted, so the window ends yesterday."""
    end = today - dt.timedelta(days=1)
    return end - dt.timedelta(days=days - 1), end


def pageviews_url(lang: str, title: str, start: dt.date, end: dt.date) -> str:
    article = urllib.parse.quote(title.replace(" ", "_"), safe="")
    return (f"{WIKI_REST}/{lang}.wikipedia.org/all-access/user/{article}/daily/"
            f"{start:%Y%m%d}/{end:%Y%m%d}")


def parse_pageviews(data: dict) -> int:
    """Total views in a per-article answer (days with no views are simply absent)."""
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("unexpected pageviews answer")
    return sum(int(i.get("views") or 0) for i in items if isinstance(i, dict))


# --- triangulation ---------------------------------------------------------------------------------------------

def parse_source(text: str) -> dict:
    """'trends-yt=40/55' -> {'name': 'trends-yt', 'topic': 40.0, 'baseline': 55.0, 'multiple': 0.727...}."""
    m = re.fullmatch(r"\s*([A-Za-z][\w .\-]*?)\s*=\s*([\d.,]+)\s*/\s*([\d.,]+)\s*", text or "")
    if not m:
        raise UsageError(f"--source expects NAME=TOPIC/BASELINE (for example trends-yt=40/55), got {text!r}")
    try:
        topic, base = float(m.group(2).replace(",", "")), float(m.group(3).replace(",", ""))
    except ValueError:
        raise UsageError(f"--source {text!r}: TOPIC and BASELINE must be numbers") from None
    if base <= 0:
        raise UsageError(f"--source {text!r}: the baseline must be above 0")
    return {"name": m.group(1).strip(), "topic": topic, "baseline": base, "multiple": topic / base}


def triangulate(multiples: dict[str, float | None]) -> dict:
    """Compare demand multiples (topic / baseline) from different sources; over 3x apart is 'demand unclear'."""
    vals = {k: v for k, v in multiples.items() if v is not None}
    if not vals:
        return {"label": "not measured", "spread": None, "sources": {}}
    if len(vals) == 1:
        return {"label": "single source", "spread": None, "sources": vals}
    lo, hi = min(vals.values()), max(vals.values())
    if hi == 0:
        spread = 1.0
    elif lo == 0:
        spread = None  # one source sees nothing, another sees something: as far apart as it gets
    else:
        spread = hi / lo
    unclear = spread is None or spread > UNCLEAR_SPREAD
    return {"label": "demand unclear" if unclear else "consistent",
            "spread": round(spread, 2) if spread is not None else None, "sources": vals}


# ---------------------------------------------------------------------------------------------------------------
# Network (each call is one function, so tests can replace it)
# ---------------------------------------------------------------------------------------------------------------

def http_get(url: str, timeout: int = HTTP_TIMEOUT_S) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def ytdlp_command() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise UsageError("yt-dlp not found. Install it (pip install yt-dlp), put it on PATH, or pass --no-serp.")


def _run_json(cmd: list[str], timeout: int = SERP_TIMEOUT_S) -> dict:
    """Run yt-dlp and parse its JSON. --ignore-errors exits 1 when one entry fails, so stdout decides."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp timed out") from None
    out = (proc.stdout or "").strip()
    if out:
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            pass
    err = (proc.stderr or "").strip().splitlines()
    raise RuntimeError(err[-1] if err else f"yt-dlp exited with {proc.returncode} and printed no JSON")


def _net_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"network error: {exc.reason}"
    return str(exc) or exc.__class__.__name__


NET_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, RuntimeError)


# ---------------------------------------------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------------------------------------------

def measure_suggestions(queries: list[str], expand: bool, hl: str | None, gl: str | None) -> dict:
    res = {"hl": hl, "gl": gl, "queries": {}, "errors": [], "expanded": None}
    calls = 0
    for q in queries:
        if calls:
            _pause(SUGGEST_PAUSE_S)
        calls += 1
        try:
            res["queries"][q] = parse_suggestions(http_get(suggest_url(q, hl, gl)))
        except NET_ERRORS as exc:
            res["errors"].append(f"{q}: {_net_error(exc)}")
    if expand:
        seen = {s for lst in res["queries"].values() for s in lst}
        extra: list[str] = []
        for q in expand_queries(queries[0]):
            _pause(SUGGEST_PAUSE_S)
            try:
                for s in parse_suggestions(http_get(suggest_url(q, hl, gl))):
                    if s not in seen:
                        seen.add(s)
                        extra.append(s)
            except NET_ERRORS as exc:
                res["errors"].append(f"{q}: {_net_error(exc)}")
        res["expanded"] = extra
    everything = [s for lst in res["queries"].values() for s in lst] + (res["expanded"] or [])
    res["unique"] = len(set(everything))
    res["intent"] = intent_flags(sorted(set(everything), key=everything.index))
    res["no_query"] = [q for q, lst in res["queries"].items() if not lst]
    res["ok"] = bool(res["queries"])
    return res


def measure_serp(base_cmd: list[str], queries: list[str], top: int, today: dt.date, fresh_days: int) -> dict:
    res = {"top": top, "fresh_days": fresh_days, "small_subs": SMALL_SUBS, "step_views": STEP_VIEWS,
           "queries": {}, "errors": []}
    for q in queries:
        print(f"Reading the top {top} search results for {q!r} ...", file=sys.stderr)
        cmd = base_cmd + ["-J", "--skip-download", "--no-warnings", "--ignore-errors", f"ytsearch{top}:{q}"]
        try:
            rows = parse_serp(_run_json(cmd))
        except (RuntimeError, ValueError) as exc:
            res["errors"].append(f"{q}: {exc}")
            continue
        if not rows:
            res["errors"].append(f"{q}: the search returned no readable results")
            continue
        rows = [classify(r, today, fresh_days) for r in rows]
        res["queries"][q] = {"summary": serp_summary(rows), "rows": rows}
    res["ok"] = bool(res["queries"])
    return res


def _wiki_article(text: str, default_lang: str, start: dt.date, end: dt.date, first: bool) -> dict:
    lang, title = parse_wiki_arg(text, default_lang)
    art = {"input": text, "lang": lang, "title": None, "total": None, "per_day": None, "error": None}
    try:
        if not first:
            _pause(WIKI_PAUSE_S)
        resolved = parse_resolve(json.loads(http_get(resolve_url(lang, title))))
        if not resolved:
            art["error"] = f"no article {title!r} on {lang}.wikipedia.org (check the exact title)"
            return art
        art["title"] = resolved
        _pause(WIKI_PAUSE_S)
        total = parse_pageviews(json.loads(http_get(pageviews_url(lang, resolved, start, end))))
    except urllib.error.HTTPError as exc:
        art["error"] = ("no pageview data in this window" if exc.code == 404 else _net_error(exc))
        return art
    except NET_ERRORS as exc:
        art["error"] = _net_error(exc)
        return art
    days = (end - start).days + 1
    art["total"], art["per_day"] = total, total / days
    return art


def measure_wiki(article: str, baselines: list[str], lang: str, days: int, today: dt.date) -> dict:
    start, end = wiki_window(today, days)
    res = {"lang": lang, "days": days, "start": start.isoformat(), "end": end.isoformat(), "agent": "user"}
    res["topic"] = _wiki_article(article, lang, start, end, first=True)
    res["baselines"] = [_wiki_article(b, lang, start, end, first=False) for b in baselines]
    good = [b["per_day"] for b in res["baselines"] if b["per_day"] is not None]
    res["baseline_per_day"] = sum(good) / len(good) if good else None
    topic = res["topic"]["per_day"]
    res["multiple"] = (round(topic / res["baseline_per_day"], 3)
                       if topic is not None and res["baseline_per_day"] else None)
    res["ok"] = topic is not None
    return res


# ---------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------

def _fmt(x) -> str:
    return "?" if x is None else f"{int(round(x)):,}"


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def print_human(r: dict) -> None:
    print(f"Demand for {r['query']!r} (measured {r['today']})")
    sug = r.get("suggestions")
    if sug:
        print()
        print(f"Search suggestions (YouTube autocomplete, hl={sug['hl'] or '-'}"
              f"{', gl=' + sug['gl'] if sug.get('gl') else ''})")
        for q, lst in sug["queries"].items():
            label = " (label: no query)" if not lst else ""
            print(f"  {q!r}: {len(lst)} suggestion{'s' if len(lst) != 1 else ''}{label}")
            for s in lst:
                print(f"    {s}")
        if sug.get("expanded") is not None:
            ex = sug["expanded"]
            print(f"  Expansion (a-z and {'/'.join(EXPAND_PREFIXES)}): {len(ex)} more")
            for s in ex[:25]:
                print(f"    {s}")
            if len(ex) > 25:
                print(f"    ... {len(ex) - 25} more in --json")
        for label, hits in sug.get("intent", {}).items():
            shown = "; ".join(hits[:3])
            print(f"  Intent flag, {label}: {len(hits)} ({shown})")
        if sug.get("no_query"):
            print("  No query means nobody types that phrasing. It is information, not a gate.")
        print("  Read the intent, not just the count: if most suggestions are about a film or exam prep, the demand "
              "belongs there.")
    serp = r.get("serp")
    if serp:
        print()
        print(f"Reachable step (top {serp['top']} results; small = under {SMALL_SUBS:,} subscribers; step = "
              f"{STEP_VIEWS:,}+ views; fresh = {serp['fresh_days']} days or newer)")
        for q, item in serp["queries"].items():
            s = item["summary"]
            top_age = f"; top result {s['top_age_days']} days old" if s["top_age_days"] is not None else ""
            print(f"  {q!r}: {s['shape'].upper()}, {s['real_steps']} fresh real step"
                  f"{'s' if s['real_steps'] != 1 else ''} ({s['steps']} step{'s' if s['steps'] != 1 else ''} in "
                  f"total){top_age}")
            print(f"    {SHAPE_TEXT[s['shape']]}")
            print(f"    {'':1} {'rank':>4} {'views':>11} {'subscribers':>12} {'age':>6}  {'flags':<22} title [channel]")
            for row in item["rows"]:
                mark = "*" if row["real_step"] else ("s" if row["step"] else " ")
                age = f"{row['age_days']}d" if row["age_days"] is not None else "?"
                flags = ", ".join(row["flags"]) or ("not fresh" if row["step"] and not row["fresh"] else "")
                print(f"    {mark} {row['rank']:>4} {_fmt(row['views']):>11} {_fmt(row['subscribers']):>12} "
                      f"{age:>6}  {_cut(flags, 22):<22} {_cut(row['title'], 60)} [{_cut(row['channel'], 30)}]")
                if row["real_step"]:
                    print(f"    {'':>6} {row['url']}")
        print("    * fresh, real step   s step that does not count (old, short, stream, podcast or re-upload)")
    wiki = r.get("wikipedia")
    if wiki:
        print()
        print(f"Wikipedia pageviews ({wiki['lang']}, user traffic, {wiki['start']} to {wiki['end']}, "
              f"{wiki['days']} days)")
        for label, art in [("topic", wiki["topic"])] + [("baseline", b) for b in wiki["baselines"]]:
            if art["error"]:
                print(f"  {label} {art['input']!r}: not measured ({art['error']})")
            else:
                print(f"  {label} {art['title']}: {_fmt(art['total'])} ({_fmt(art['per_day'])}/day)")
        if wiki["multiple"] is not None:
            print(f"  Multiple: {wiki['multiple']:.2f}x the baseline")
        elif wiki["topic"]["per_day"] is not None and not wiki["baselines"]:
            print("  No baseline: raw pageviews mean little. Add --baseline with one of your proven topics.")
        print(f"  {CURIOSITY_LINE}")
    tri = r["triangulation"]
    if tri["label"] != "not measured":
        print()
        print("Triangulation (topic / baseline, per source)")
        parts = ", ".join(f"{k} {v:.2f}x" for k, v in tri["sources"].items())
        if tri["label"] == "single source":
            print(f"  {parts}: single source. Add a second one by hand, for example Google Trends with the YouTube "
                  "Search property: --source trends-yt=TOPIC/BASELINE.")
        else:
            spread = f"spread {tri['spread']:.1f}x" if tri["spread"] is not None else "one source sees nothing"
            print(f"  {parts} -> {spread}: {tri['label'].upper()}")
    print()
    print(f"STEP GATE: {r['gate']}" + (f" ({r['gate_reason']})" if r.get("gate_reason") else ""))
    for note in r["notes"]:
        print(f"Note: {note}")


# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=" ".join(__doc__.split("\n\n")[0].split()))
    ap.add_argument("query", nargs="?", default="", help="the topic in the words people would search")
    ap.add_argument("--variant", action="append", default=[], metavar="TEXT",
                    help="another phrasing to measure the same way (repeatable)")
    ap.add_argument("--expand", action="store_true",
                    help="also collect suggestions for '<query> a'..'z' and why/how/what/when prefixes")
    ap.add_argument("--hl", default="en", help="suggestion language (default en)")
    ap.add_argument("--gl", default=None, help="suggestion country, for example US or GB")
    ap.add_argument("--no-suggest", action="store_true", help="skip the autocomplete suggestions")
    ap.add_argument("--no-serp", action="store_true", help="skip the search results (no yt-dlp needed)")
    ap.add_argument("--top", type=int, default=SERP_TOP, help=f"search results to read per query (default {SERP_TOP})")
    ap.add_argument("--fresh-days", type=int, default=FRESH_DAYS,
                    help=f"a step counts only when uploaded within this many days (default {FRESH_DAYS})")
    ap.add_argument("--wiki", metavar="ARTICLE", help="the topic's Wikipedia article (title or URL)")
    ap.add_argument("--baseline", action="append", default=[], metavar="ARTICLE",
                    help="article for one of your proven topics; repeat to average several")
    ap.add_argument("--wiki-lang", default="en", help="Wikipedia language for plain titles (default en)")
    ap.add_argument("--days", type=int, default=WIKI_DAYS, help=f"pageview window in days (default {WIKI_DAYS})")
    ap.add_argument("--source", action="append", default=[], metavar="NAME=TOPIC/BASELINE",
                    help="a demand reading you took by hand for the topic and the same baseline (repeatable)")
    ap.add_argument("--json", action="store_true", help="print a machine-readable result")
    ap.add_argument("--out", metavar="FILE", help="also write the JSON result to this file")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2

    try:
        query = " ".join(args.query.split())
        if not query:
            raise UsageError("give the topic as a search query, for example: demand.py \"tacoma narrows bridge\"")
        if not 1 <= args.top <= 50:
            raise UsageError("--top must be between 1 and 50")
        if args.fresh_days < 1:
            raise UsageError("--fresh-days must be at least 1")
        if not 7 <= args.days <= 365:
            raise UsageError("--days must be between 7 and 365")
        if args.no_suggest and args.no_serp and not args.wiki and not args.source:
            raise UsageError("nothing to measure: drop --no-suggest or --no-serp, or add --wiki or --source")
        if args.baseline and not args.wiki:
            raise UsageError("--baseline needs --wiki: the baseline is compared with the topic's article")
        if args.wiki:
            parse_wiki_arg(args.wiki, args.wiki_lang)
            for b in args.baseline:
                parse_wiki_arg(b, args.wiki_lang)
        sources = [parse_source(s) for s in args.source]
        names = [s["name"].lower() for s in sources]
        if len(set(names)) != len(names) or (args.wiki and "wikipedia" in names):
            raise UsageError("each --source needs its own name (and 'wikipedia' is taken by --wiki)")
        base_cmd = None if args.no_serp else ytdlp_command()
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    today = dt.date.today()
    queries = unique_queries(query, args.variant)
    result: dict = {"query": query, "variants": queries[1:], "today": today.isoformat(), "notes": []}
    requested, failed = 0, 0

    if not args.no_suggest:
        requested += 1
        sug = measure_suggestions(queries, args.expand, args.hl, args.gl)
        result["suggestions"] = sug
        failed += 0 if sug["ok"] else 1
        result["notes"] += [f"suggestions not read for {e}" for e in sug["errors"]]
    if base_cmd is not None:
        requested += 1
        serp = measure_serp(base_cmd, queries, args.top, today, args.fresh_days)
        result["serp"] = serp
        failed += 0 if serp["ok"] else 1
        result["notes"] += [f"search results not read for {e}" for e in serp["errors"]]
    if args.wiki:
        requested += 1
        wiki = measure_wiki(args.wiki, args.baseline, args.wiki_lang, args.days, today)
        result["wikipedia"] = wiki
        failed += 0 if wiki["ok"] else 1
        for art in [wiki["topic"]] + wiki["baselines"]:
            if art["error"]:
                result["notes"].append(f"Wikipedia {art['input']!r}: {art['error']}")

    multiples: dict = {}
    if args.wiki and result["wikipedia"]["multiple"] is not None:
        multiples["wikipedia"] = result["wikipedia"]["multiple"]
    for s in sources:
        multiples[s["name"]] = s["multiple"]
    result["sources"] = sources
    result["triangulation"] = triangulate(multiples)

    serp = result.get("serp")
    if serp and serp["ok"]:
        reach = [q for q, item in serp["queries"].items() if item["summary"]["shape"] == "reachable"]
        result["gate"] = "PASS" if reach else "FAIL"
        result["gate_reason"] = (f"a fresh, real step on {len(reach)} of {len(serp['queries'])} quer"
                                 f"{'ies' if len(serp['queries']) != 1 else 'y'}" if reach else
                                 "no fresh, real step on any query: drop a desert; keep a gap only with strong "
                                 "catalog evidence")
    else:
        result["gate"] = "not measured"
        result["gate_reason"] = "search results not read" if serp else "--no-serp"

    if args.out:
        try:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_human(result)

    if requested and failed == requested:
        print("error: nothing could be measured; check the network connection", file=sys.stderr)
        return 2
    if serp and not serp["ok"]:
        print("error: the search results could not be read, so the step gate was not measured", file=sys.stderr)
        return 2
    return 1 if result["gate"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
