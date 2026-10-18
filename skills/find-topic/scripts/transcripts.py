"""Fetch competitor video transcripts for free with yt-dlp: one clean text file per video, a manifest, and the
opening on demand.

Why: the opening mechanics of an evidence video (its first sentence, the second the first number lands) and the
lessons in a niche's outliers live in what is said, and reading is faster than watching. Captions are free, so no
paid transcript tool is needed. A manifest makes a re-run skip what is already on disk, so a sector scan every
couple of weeks only fetches what is new.

How it works:
  1. Targets: video URLs or ids; channels (@handle, channel URL or id), which give their newest --last long videos
     (the videos tab, which has no Shorts); or --from-json with an outliers.py catalog (--save) or its --json
     report. --min-multiple keeps only videos at or above that multiple of their own channel's median (a catalog
     channel needs 4+ videos, as in outliers.py). Videos flagged "possibly ad-pushed" are skipped.
  2. One read per video (`yt-dlp -J`) lists its caption tracks. The track is chosen in this order: the --lang
     languages (default en), then the video's original language, then the first available. Within a language a
     manual track beats an automatic one. An automatic track in a language other than the original is YouTube's
     machine translation, so it is never taken as a "requested" language.
  3. That one track is downloaded as WebVTT (`yt-dlp --skip-download --write-subs|--write-auto-subs --sub-langs
     <track> --sub-format vtt`). Automatic captions roll: each line is shown again while the next one builds up.
     Repeats are dropped, tags and entities removed, and the text is written as paragraphs with an [mm:ss] stamp
     about every 30 seconds.
  4. The VTT is kept in <out>/vtt/ so --first can print exact line times later without the network.

Output (--out, default transcripts/):
  <id>_<title-slug>.<lang>.txt   three '#' header lines (title, URL, channel/language/kind/date), then the text
  vtt/<id>.<track>.vtt           the caption file as downloaded
  manifest.json                  video id -> {id, title, channel, url, language, kind (manual|auto), choice
                                 (requested|original|first available), fetched (YYYY-MM-DD), file, vtt}
A video already in the manifest with its file on disk is skipped; --refresh fetches it again.

--first SECONDS prints only the opening of each video, one caption line per row with its time, plus the first line
that holds a number (digits or an English number word). A transcript cannot show the first cut: watch for it.

Run:
  python transcripts.py https://www.youtube.com/watch?v=VIDEO_ID_01 --first 60
  python transcripts.py @channelA @channelB --last 5
  python transcripts.py --from-json catalog.json --min-multiple 2 --out research/transcripts
  python transcripts.py VIDEO_ID_01 --lang es --lang en --refresh --json

Network: YouTube through yt-dlp only (taken from PATH, or `python -m yt_dlp` when installed); nothing else is sent
anywhere. Each video costs two small requests; a pause between videos and --max (default 20 new videos per run)
keep YouTube from answering with HTTP 429. Transcripts are for research: never republish the words.

Exit codes: 0 at least one transcript was written or was already present, 1 none could be fetched (no captions,
unavailable videos), 2 usage or environment error (no targets, bad option or catalog, unreadable manifest, yt-dlp
missing, no channel could be listed).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - older or redirected streams
        pass

DEFAULT_LANGS = ["en"]
STAMP_EVERY_S = 30       # often enough to find a passage again, rare enough not to break the reading
ROLLING_WINDOW = 2       # rolling captions repeat the previous line above the new one; comparing two lines covers it
PAUSE_S = 1.5            # YouTube answers bursts of caption requests with HTTP 429; a short pause between videos avoids it
MAX_NEW = 20             # new videos per run: enough for a round, few enough to stay under YouTube's rate limit
CHANNEL_LAST = 5         # newest long videos per channel: a fortnight's output for most channels
MIN_N = 4                # as in outliers.py: a median of fewer videos is not a median
AD_PUSH_PCT = 0.35       # as in outliers.py: likes under 0.35% of views mark bought views, which are not evidence
TIMEOUT_S = 180
SLUG_MAX = 60
# 'one' is left out on purpose: in speech it is mostly a pronoun ("no one", "one of the").
NUMBER_RE = re.compile(r"\d|\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|"
                       r"fifty|hundred|thousand|million|billion|trillion|percent|dozen|half)\b", re.I)
VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
VIDEO_URL_RE = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
LANG_RE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*")
TIME_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s+-->")
TAG_RE = re.compile(r"<[^>]*>")
STAMP_LINE_RE = re.compile(r"^\[(?:(\d+):)?(\d{1,2}):(\d{2})\]\s*(.*)$")
OTHER_TABS = {"featured", "shorts", "streams", "live", "playlists", "community", "about", "podcasts", "releases",
              "courses", "store", "posts"}
CUT_LINE = ("A transcript cannot show the first cut: watch the opening for it. Caption times are approximate; check "
            "the second in the video before you quote it.")


class UsageError(Exception):
    """Bad input or a broken environment (exit 2)."""


# ---------------------------------------------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------------------------------------------

def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def normalize_channel_url(text: str) -> str:
    """Turn '@handle', a channel id or any channel URL into its long-video tab URL."""
    s = text.strip()
    if s.startswith("@"):
        s = "https://www.youtube.com/" + s
    if re.fullmatch(r"UC[\w-]{22}", s):
        return f"https://www.youtube.com/channel/{s}/videos"
    if not re.match(r"https?://", s, re.I):
        if s.lower().startswith(("www.youtube.com", "youtube.com", "m.youtube.com")):
            s = "https://" + s
        else:
            raise UsageError(f"not a video id, video URL, channel URL or @handle: {text!r}")
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    last = s.rsplit("/", 1)[-1].lower()
    if last == "videos":
        return s
    if last in OTHER_TABS:
        s = s.rsplit("/", 1)[0]
    return s + "/videos"


def classify_target(text: str) -> tuple[str, str]:
    """('video', id) for an 11-character id or any video URL, ('channel', videos-tab URL) for a channel."""
    s = text.strip()
    if not s:
        raise UsageError("empty target")
    if VIDEO_ID_RE.fullmatch(s):
        return "video", s
    m = VIDEO_URL_RE.search(s)
    if m:
        return "video", m.group(1)
    return "channel", normalize_channel_url(s)


def parse_langs(values: list[str] | None) -> list[str]:
    langs: list[str] = []
    for value in values or DEFAULT_LANGS:
        for part in value.split(","):
            part = part.strip()
            if not part:
                continue
            if not LANG_RE.fullmatch(part):
                raise UsageError(f"not a language code: {part!r} (use codes like en, es, pt-BR)")
            if part.lower() not in (x.lower() for x in langs):
                langs.append(part)
    if not langs:
        raise UsageError("--lang needs at least one language code")
    return langs


def _like_pct(video: dict) -> float | None:
    likes, views = video.get("likes"), video.get("views")
    if isinstance(likes, bool) or not isinstance(likes, int) or not isinstance(views, (int, float)) or not views:
        return None
    return likes / views * 100


def targets_from_json(path: Path, min_multiple: float | None) -> tuple[list[dict], list[str]]:
    """Videos from an outliers.py catalog (channel -> videos) or report (--json), strongest multiple first."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # -sig: Windows editors may add a BOM
    except FileNotFoundError:
        raise UsageError(f"catalog not found: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read catalog {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{path} must be an outliers.py catalog or report (a JSON object)")
    rows: list[dict] = []
    notes: list[str] = []
    pushed = 0
    if isinstance(data.get("outliers"), list) and "min_multiple" in data:  # outliers.py --json report
        for o in data["outliers"]:
            if not isinstance(o, dict) or not o.get("id"):
                continue
            multiple = o.get("multiple")
            if min_multiple is not None and not (isinstance(multiple, (int, float)) and multiple >= min_multiple):
                continue
            if o.get("ad_pushed"):
                pushed += 1
                continue
            rows.append({"id": str(o["id"]), "title": o.get("title") or "", "channel": o.get("channel") or "",
                         "multiple": multiple})
    else:
        for channel, videos in data.items():
            if str(channel).startswith("_"):
                continue
            if not isinstance(videos, list):
                raise UsageError(f"catalog {path}: {channel!r} must map to a list of videos")
            videos = [v for v in videos if isinstance(v, dict) and v.get("id")]
            med = None
            if min_multiple is not None:
                views = [v["views"] for v in videos
                         if isinstance(v.get("views"), (int, float)) and not isinstance(v.get("views"), bool)]
                if len(views) < MIN_N:
                    notes.append(f"{channel}: skipped, {len(views)} video{'s' if len(views) != 1 else ''} with views "
                                 f"is not a median (need {MIN_N})")
                    continue
                med = statistics.median(views)
                if not med:
                    notes.append(f"{channel}: skipped, median views is 0")
                    continue
            for v in videos:
                multiple = None
                if med:
                    if not isinstance(v.get("views"), (int, float)) or isinstance(v.get("views"), bool):
                        continue
                    multiple = round(v["views"] / med, 2)
                    if multiple < min_multiple:
                        continue
                pct = _like_pct(v)
                if pct is not None and pct < AD_PUSH_PCT:
                    pushed += 1
                    continue
                rows.append({"id": str(v["id"]), "title": v.get("title") or "", "channel": str(channel),
                             "multiple": multiple})
    if pushed:
        notes.append(f"skipped {pushed} possibly ad-pushed video{'s' if pushed != 1 else ''} (not evidence)")
    rows.sort(key=lambda r: -(r["multiple"] or 0))  # stable: equal multiples keep catalog order
    return rows, notes


# ---------------------------------------------------------------------------------------------------------------
# Captions: choosing a track, cleaning WebVTT, rendering text
# ---------------------------------------------------------------------------------------------------------------

def _same_lang(a: str, b: str) -> bool:
    """'en' matches 'en', 'en-GB' and 'en-US' (either way round); 'en' never matches 'es'."""
    a, b = a.lower(), b.lower()
    return a == b or a.startswith(b + "-") or b.startswith(a + "-")


def _base_lang(key: str) -> str:
    return key[:-5] if key.endswith("-orig") else key


def caption_tracks(info: dict) -> tuple[list[str], list[str], str | None]:
    """(manual track keys, automatic keys in the original language, original language) from `yt-dlp -J` output.

    yt-dlp lists YouTube's machine translations of the automatic track next to the real one and marks the real one
    with '-orig'. Without that mark the video's 'language' field decides; without both, every track is a candidate.
    """
    manual = [k for k, v in (info.get("subtitles") or {}).items() if v and k != "live_chat"]
    auto_all = [k for k, v in (info.get("automatic_captions") or {}).items() if v]
    orig_keys = [k for k in auto_all if k.endswith("-orig")]
    original = _base_lang(orig_keys[0]) if orig_keys else (info.get("language") or None)
    if orig_keys:
        auto = orig_keys
    elif original:
        base = original.split("-")[0].lower()
        auto = [k for k in auto_all if k.split("-")[0].lower() == base]
    else:
        auto = auto_all
    return manual, auto, original


def pick_track(info: dict, langs: list[str]) -> dict | None:
    """The caption track to fetch: requested languages, then the original language, then the first available."""
    manual, auto, original = caption_tracks(info)

    def for_lang(lang: str, choice: str) -> list[tuple[str, str, str]]:
        hand = sorted((k for k in manual if _same_lang(k, lang)), key=lambda k: k.lower() != lang.lower())
        return ([(k, "manual", choice) for k in hand]
                + [(k, "auto", choice) for k in auto if _same_lang(_base_lang(k), lang)])

    order: list[tuple[str, str, str]] = []
    for lang in langs:
        order += for_lang(lang, "requested")
    if original:
        order += for_lang(original, "original")
    order += [(k, "manual", "first available") for k in manual] + [(k, "auto", "first available") for k in auto]
    if not order:
        return None
    key, kind, choice = order[0]
    return {"track": key, "language": _base_lang(key), "kind": kind, "choice": choice, "original": original}


def _cue_seconds(m: re.Match) -> float:
    h, mm, ss, frac = m.groups()
    return int(h or 0) * 3600 + int(mm) * 60 + int(ss) + int(frac.ljust(3, "0")) / 1000


def read_cues(text: str) -> list[list]:
    """[[start seconds, [raw text lines]], ...] from WebVTT. Header, NOTE/STYLE blocks and cue ids are left out."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[list] = []
    cur: list | None = None
    prev_blank, skip_block = True, False
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:  # YouTube puts a lone space line inside rolling cues, so a blank never ends a cue here
            prev_blank, skip_block = True, False
            continue
        m = TIME_RE.match(raw)
        if m:
            cur = [_cue_seconds(m), []]
            cues.append(cur)
            prev_blank, skip_block = False, False
            continue
        if prev_blank and (re.match(r"NOTE(\s|$)", stripped) or stripped in ("STYLE", "REGION")):
            skip_block = True
        is_cue_id = prev_blank and i + 1 < len(lines) and TIME_RE.match(lines[i + 1])
        prev_blank = False
        if skip_block or cur is None or is_cue_id:
            continue
        cur[1].append(raw)
    return cues


def clean_line(raw: str) -> str:
    """Drop timing and style tags, decode entities, collapse whitespace."""
    s = html.unescape(TAG_RE.sub("", raw)).replace("\u200b", "")
    return " ".join(s.split())


def parse_vtt(text: str) -> list[tuple[float, str]]:
    """[(start seconds, line)] with the repeats of rolling automatic captions removed."""
    segments: list[list] = []
    for start, raw_lines in read_cues(text):
        for raw in raw_lines:
            line = clean_line(raw)
            if not line or line in [s[1] for s in segments[-ROLLING_WINDOW:]]:
                continue
            if segments and line.startswith(segments[-1][1] + " "):  # a caption that grows word by word
                segments[-1][1] = line
                continue
            segments.append([start, line])
    return [(round(s, 3), t) for s, t in segments]


def fmt_stamp(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def render_text(segments: list[tuple[float, str]], every: float = STAMP_EVERY_S) -> str:
    """Paragraphs that each start with an [mm:ss] stamp, a new one about every `every` seconds."""
    paragraphs: list[list[str]] = []
    last = None
    for start, line in segments:
        if last is None or start - last >= every:
            paragraphs.append([f"[{fmt_stamp(start)}]", line])
            last = start
        else:
            paragraphs[-1].append(line)
    return "\n\n".join(" ".join(p) for p in paragraphs) + "\n" if paragraphs else ""


def segments_from_text(text: str) -> list[tuple[float, str]]:
    """Read the stamped paragraphs of a transcript file back (used when its VTT is gone)."""
    out = []
    for line in text.splitlines():
        m = STAMP_LINE_RE.match(line.strip())
        if m:
            out.append((int(m.group(1) or 0) * 3600 + int(m.group(2)) * 60 + int(m.group(3)), m.group(4)))
    return out


def opening(segments: list[tuple[float, str]], seconds: float) -> list[tuple[float, str]]:
    return [s for s in segments if s[0] < seconds]


def first_number(segments: list[tuple[float, str]]) -> tuple[float, str] | None:
    return next((s for s in segments if NUMBER_RE.search(s[1])), None)


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:SLUG_MAX].strip("-") or "untitled"


# ---------------------------------------------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    """Write UTF-8 with LF line ends through a temporary file, so an interrupted run never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageError(f"cannot read {path}: {exc}; fix it or move it away") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{path} must map video ids to entries; fix it or move it away")
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("file")}


def save_manifest(path: Path, manifest: dict) -> None:
    _write(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def is_present(out_dir: Path, entry: dict | None) -> bool:
    return bool(entry) and (out_dir / entry["file"]).is_file()


def stored_segments(out_dir: Path, entry: dict) -> list[tuple[float, str]]:
    """Exact lines from the kept VTT; the 30-second paragraphs of the text file when the VTT is gone."""
    vtt = out_dir / entry["vtt"] if entry.get("vtt") else None
    try:
        if vtt and vtt.is_file():
            return parse_vtt(vtt.read_text(encoding="utf-8", errors="replace"))
        return segments_from_text((out_dir / entry["file"]).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


# ---------------------------------------------------------------------------------------------------------------
# yt-dlp (network; each call goes through subprocess.run so tests can replace it)
# ---------------------------------------------------------------------------------------------------------------

def ytdlp_command() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    raise UsageError("yt-dlp not found. Install it (pip install yt-dlp) or put it on PATH.")


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _run(cmd: list[str], timeout: int = TIMEOUT_S) -> subprocess.CompletedProcess:
    # A Python yt-dlp writes its messages in the console code page on Windows; ask it for UTF-8 so they decode.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp timed out") from None
    except OSError as exc:
        raise RuntimeError(f"cannot run yt-dlp: {exc}") from exc


def _last_error(proc: subprocess.CompletedProcess, fallback: str) -> str:
    err = [ln for ln in (proc.stderr or "").strip().splitlines() if ln.strip()]
    return err[-1].strip() if err else fallback


def _run_json(cmd: list[str], timeout: int = TIMEOUT_S) -> dict:
    proc = _run(cmd, timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out:
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data
    raise RuntimeError(_last_error(proc, f"yt-dlp exited with {proc.returncode} and printed no JSON"))


def fetch_channel(base_cmd: list[str], url: str, last: int) -> list[dict]:
    """The newest `last` long videos of a channel (live and upcoming streams left out)."""
    data = _run_json(base_cmd + ["--flat-playlist", "-J", "--playlist-end", str(last), "--no-warnings", url],
                     timeout=600)
    name = data.get("channel") or data.get("uploader") or url
    videos: list[dict] = []
    stack = list(data.get("entries") or [])
    while stack:
        e = stack.pop(0)
        if not isinstance(e, dict):
            continue
        if e.get("_type") == "playlist" and e.get("entries"):
            stack = list(e["entries"]) + stack
            continue
        if not e.get("id") or e.get("live_status") in ("is_upcoming", "is_live"):
            continue
        videos.append({"id": e["id"], "title": e.get("title") or "", "channel": name, "multiple": None})
    return videos[:last]


def fetch_info(base_cmd: list[str], video_id: str) -> dict:
    return _run_json(base_cmd + ["-J", "--no-playlist", "--skip-download", "--no-warnings", video_url(video_id)])


def download_track(base_cmd: list[str], video_id: str, track: str, kind: str) -> str:
    """Download one caption track as WebVTT and return its text."""
    with tempfile.TemporaryDirectory(prefix="tubecrew-subs-") as tmp:
        cmd = base_cmd + ["--skip-download", "--no-playlist", "--no-warnings", "--quiet",
                          "--write-subs" if kind == "manual" else "--write-auto-subs",
                          "--sub-langs", re.escape(track), "--sub-format", "vtt",
                          "-o", str(Path(tmp) / "%(id)s.%(ext)s"), video_url(video_id)]
        proc = _run(cmd)
        files = sorted(Path(tmp).glob("*.vtt"))
        if not files:
            raise RuntimeError(_last_error(proc, f"yt-dlp wrote no caption file for track {track}"))
        return files[0].read_text(encoding="utf-8", errors="replace")


def fetch_one(base_cmd: list[str], video_id: str, langs: list[str], out_dir: Path, today: str,
              old: dict | None = None) -> tuple[dict, list[tuple[float, str]]]:
    """Fetch, clean and store one video's transcript; return its manifest entry and its lines."""
    info = fetch_info(base_cmd, video_id)
    title = info.get("title") or video_id
    channel = info.get("channel") or info.get("uploader") or ""
    track = pick_track(info, langs)
    if track is None:
        raise RuntimeError("no captions (manual or automatic) on this video")
    vtt_text = download_track(base_cmd, video_id, track["track"], track["kind"])
    segments = parse_vtt(vtt_text)
    if not segments:
        raise RuntimeError(f"caption track {track['track']} has no text")
    lang, kind = track["language"], track["kind"]
    entry = {"id": video_id, "title": title, "channel": channel, "url": video_url(video_id), "language": lang,
             "kind": kind, "choice": track["choice"], "fetched": today,
             "file": f"{video_id}_{slugify(title)}.{lang}.txt", "vtt": f"vtt/{video_id}.{track['track']}.vtt"}
    header = (f"# {title}\n# {entry['url']}\n"
              f"# channel: {channel or 'unknown'} | language: {lang} ({kind}) | fetched {today}\n\n")
    _write(out_dir / entry["vtt"], vtt_text)
    _write(out_dir / entry["file"], header + render_text(segments))
    for key in ("file", "vtt"):  # a refresh in another language must not leave the old files behind
        if old and old.get(key) and old[key] != entry[key]:
            try:
                (out_dir / old[key]).unlink()
            except OSError:
                pass
    return entry, segments


# ---------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------

def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 3] + "..."


def _lang_label(r: dict) -> str:
    extra = {"original": " (original language)", "first available": " (first available)"}.get(r.get("choice"), "")
    return f"{r.get('language')} {r.get('kind')}{extra}"


def print_human(results: list[dict], counts: dict, args, out_dir: Path, langs: list[str], notes: list[str]) -> None:
    ok = [r for r in results if r["status"] in ("fetched", "present")]
    if args.first is not None:
        for r in ok:
            print(f"{r['title']} ({r['id']}), {_lang_label(r)}")
            print(f"  {r['url']}")
            for t, line in r["opening"]:
                print(f"  [{fmt_stamp(t)}] {line}")
            if not r["opening"]:
                print(f"  (no caption lines in the first {args.first:g} s)")
            fn = r.get("first_number")
            print(f"  First number: [{fmt_stamp(fn['t'])}] {fn['text']}" if fn
                  else f"  First number: none in the first {args.first:g} s")
            print()
        if ok:
            print(CUT_LINE)
    else:
        print(f"Transcripts in {out_dir} (languages: {', '.join(langs)}, then the original, then the first available)")
        for r in ok:
            print(f"  {r['status']:<8}  {r['id']}  {_lang_label(r):<28}  {_cut(r.get('title') or '', 60)}")
            print(f"  {'':<8}  {'':<11}  -> {r['file']}")
    for r in results:
        if r["status"] == "failed":
            title = _cut(r.get("title") or "", 50)
            print(f"  failed    {r['id']}  {title + ': ' if title else ''}{r['error']}")
    if counts["deferred"]:
        print(f"  deferred  {counts['deferred']} video{'s' if counts['deferred'] != 1 else ''} over --max "
              f"{args.max}; run again to continue (fetched ones are skipped)")
    print(f"{counts['fetched']} fetched, {counts['present']} already present, {counts['failed']} failed. "
          f"Manifest: {out_dir / 'manifest.json'}")
    for note in notes:
        print(f"Note: {note}")


# ---------------------------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=" ".join(__doc__.split("\n\n")[0].split()))
    ap.add_argument("targets", nargs="*", help="video URLs or ids, channel URLs, @handles or channel ids")
    ap.add_argument("--from-json", metavar="CATALOG", help="an outliers.py catalog (--save) or report (--json)")
    ap.add_argument("--min-multiple", type=float, default=None,
                    help="with --from-json: only videos at or above this multiple of their channel's median")
    ap.add_argument("--last", type=int, default=CHANNEL_LAST,
                    help=f"newest long videos per channel (default {CHANNEL_LAST})")
    ap.add_argument("--lang", action="append", metavar="CODE",
                    help="preferred caption language, repeatable or comma-separated (default en)")
    ap.add_argument("--out", default="transcripts", metavar="DIR", help="output folder (default transcripts)")
    ap.add_argument("--first", type=float, default=None, metavar="SECONDS",
                    help="print only the opening of each video, line by line with times")
    ap.add_argument("--refresh", action="store_true", help="fetch again even when the manifest has the video")
    ap.add_argument("--max", type=int, default=MAX_NEW, help=f"new videos to fetch per run (default {MAX_NEW})")
    ap.add_argument("--json", action="store_true", help="print a machine-readable result")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2

    notes: list[str] = []
    try:
        langs = parse_langs(args.lang)
        if args.last < 1:
            raise UsageError("--last must be at least 1")
        if args.max < 1:
            raise UsageError("--max must be at least 1")
        if args.first is not None and args.first <= 0:
            raise UsageError("--first must be a positive number of seconds")
        if args.min_multiple is not None and args.min_multiple <= 0:
            raise UsageError("--min-multiple must be positive")
        if args.min_multiple is not None and not args.from_json:
            raise UsageError("--min-multiple works on a catalog: add --from-json CATALOG")
        parsed = [classify_target(t) for t in args.targets]
        json_rows: list[dict] = []
        if args.from_json:
            json_rows, json_notes = targets_from_json(Path(args.from_json), args.min_multiple)
            notes += json_notes
        if not parsed and not args.from_json:
            raise UsageError("give at least one video, channel or --from-json CATALOG")
        out_dir = Path(args.out)
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UsageError(f"cannot create {out_dir}: {exc}") from exc
        manifest_path = out_dir / "manifest.json"
        manifest = load_manifest(manifest_path)

        def needs_fetch(vid: str) -> bool:
            return args.refresh or not is_present(out_dir, manifest.get(vid))

        needs_net = (any(kind == "channel" for kind, _ in parsed)
                     or any(needs_fetch(v) for kind, v in parsed if kind == "video")
                     or any(needs_fetch(r["id"]) for r in json_rows))
        base_cmd = ytdlp_command() if needs_net else None

        items: list[dict] = []
        channel_errors: list[str] = []
        for kind, value in parsed:
            if kind == "video":
                items.append({"id": value, "title": "", "channel": "", "multiple": None})
                continue
            print(f"Listing {value} ...", file=sys.stderr)
            try:
                listed = fetch_channel(base_cmd, value, args.last)
            except (RuntimeError, ValueError) as exc:
                channel_errors.append(f"{value}: {exc}")
                continue
            if not listed:
                channel_errors.append(f"{value}: no long videos listed")
            items.extend(listed)
        items.extend(json_rows)
        notes += [f"channel not listed: {e}" for e in channel_errors]
        if not items:
            if channel_errors:
                raise UsageError("no channel could be listed: " + "; ".join(channel_errors))
            raise UsageError("the catalog gave no videos" + (" at that --min-multiple" if args.min_multiple else ""))
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    seen: set[str] = set()
    unique = []
    for it in items:
        if it["id"] not in seen:
            seen.add(it["id"])
            unique.append(it)

    today = dt.date.today().isoformat()
    results: list[dict] = []
    segments_by_id: dict[str, list] = {}
    attempts = 0
    for it in unique:
        vid = it["id"]
        old = manifest.get(vid)
        if not args.refresh and is_present(out_dir, old):
            results.append({**old, "status": "present"})
            if args.first is not None:
                segments_by_id[vid] = stored_segments(out_dir, old)
            continue
        if attempts >= args.max:
            results.append({"id": vid, "title": it.get("title") or "", "status": "deferred"})
            continue
        if attempts:
            _pause(PAUSE_S)
        attempts += 1
        print(f"Fetching {vid} ...", file=sys.stderr)
        try:
            entry, segments = fetch_one(base_cmd, vid, langs, out_dir, today, old)
            manifest[vid] = entry
            save_manifest(manifest_path, manifest)
        except (RuntimeError, ValueError, OSError) as exc:
            results.append({"id": vid, "title": it.get("title") or "", "channel": it.get("channel") or "",
                            "url": video_url(vid), "status": "failed", "error": str(exc)})
            continue
        results.append({**entry, "status": "fetched"})
        segments_by_id[vid] = segments

    if args.first is not None:
        for r in results:
            if r["status"] in ("fetched", "present"):
                lines = opening(segments_by_id.get(r["id"], []), args.first)
                fn = first_number(lines)
                r["opening"] = lines
                r["first_number"] = {"t": fn[0], "text": fn[1]} if fn else None

    counts = {s: sum(1 for r in results if r["status"] == s) for s in ("fetched", "present", "failed", "deferred")}
    if args.json:
        for r in results:
            if "opening" in r:
                r["opening"] = [{"t": t, "text": line} for t, line in r["opening"]]
        print(json.dumps({"out": str(out_dir), "manifest": str(manifest_path), "languages": langs,
                          "first": args.first, "videos": results, "counts": counts, "notes": notes,
                          "note": CUT_LINE if args.first is not None else None}, ensure_ascii=False, indent=2))
    else:
        print_human(results, counts, args, out_dir, langs, notes)
    return 0 if counts["fetched"] or counts["present"] else 1


if __name__ == "__main__":
    sys.exit(main())
