#!/usr/bin/env python3
"""render_check.py - the code gate for a rendered video.

What it does
    Measures the defects of a finished render that code can measure, in one ffmpeg decode pass:
    missing streams, audio that stops before the picture, total silence (a dropout below -60 dB) anywhere, silent
    tails and gaps, dead air in a music-only soundtrack, integrated loudness and true peak, black stretches, frozen
    pictures, a hook that does not move (a motion band that also catches a slow zoom on a still image), and with
    --srt subtitle cues that run past the end, overlap, are too long to read, split a number from its unit, or
    (with --words) close before their last word is spoken. It writes a JSON report bound to the video's SHA-256 so
    an upload step can refuse a verdict that belongs to a different file.

Why
    These defects repeat from video to video and are cheap to measure but easy to miss by eye: an end screen
    without sound, a second of digital silence where a clip lost its audio, a 2-second black gap from a missing
    clip, an opening that sits on one picture, a subtitle that vanishes before the sentence ends. A reviewer's time
    is better spent on frames and meaning; this script handles the rest.

How to run
    python render_check.py render/final.mp4 [--profile animated|live] [--music] [--hook-seconds 45]
                           [--srt voice/captions.srt [--words qa/voice-check-transcript.json] [--words-offset 0]]
                           [--motion-band 38,2.2,4.9] [--fast] [--out PATH] [--json]

    --profile animated (default): motion graphics, slideshows, edited explainers. Nothing outside the end screen
        may stand fully still for 3 s, and a hook that stands still (or only zooms slowly) fails.
    --profile live: camera or screen recordings, where a face or a cursor carries the picture. The hook motion
        band only warns and body freezes warn at 8 s.
    --music: the soundtrack is music only (no narration), so any dead air is a hole in the mix: a dip below
        -50 dB of 0.5 s warns and of 1 s fails (the last 3 s may fade out). Without it, pauses of up to 2 s are
        normal speech rhythm.
    --words: word-timed JSON of the narration (Whisper style: segments with "words", or a "words" list, e.g. the
        transcript check-voiceover saves). --words-offset is where the narration starts inside the video.
    --motion-band EVENTS,FROZEN,SLOW: the reference band (minimum events per minute, maximum frozen %, maximum
        slow %). The hook fails above FROZEN or SLOW; low events only warn. Change it for a calmer genre.
    --fast samples black and frozen frames more coarsely (for previews). The motion band and the subtitle checks
        still run: they are cheap and matter most on a preview.

    The project folder is the video's parent, or the parent of `render/` when the video sits in a folder named
    `render`. The report goes to <project>/qa/render-check.json unless --out is given. Paths inside the report are
    relative to the project folder.

Exemptions
    When a check is wrong for one video (for example a screen recording whose face cam moves while the screen
    stays still), the creator writes qa/RENDER-EXEMPTION-<YYYY-MM-DD>.md:

        Video: render/final.mp4 sha256:<first 12 hex of the video>
        Checks: freeze, hook_motion
        Reason: <why this check is wrong for this video, and who decided>

    A FAIL of a named check becomes WARN, recorded as exempted with the reason, only while the hash matches. A new
    render needs a new Video line. Several Video lines may share one file.

Exit codes
    0  verdict PASS (warnings may be listed)
    1  verdict FAIL: at least one check failed, fix the render
    2  usage or environment error (file missing, ffmpeg/ffprobe not on PATH, file not readable as media)

Requires ffmpeg and ffprobe on PATH. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import math
import operator
import os
import re
import shutil
import statistics
import subprocess
import sys
import unicodedata
from pathlib import Path

TOOL = "render_check.py"
VERSION = "0.3.1"

# ----------------------------------------------------------------------------------------------- thresholds
AUDIO_SHORT_MAX_S = 0.5        # audio may end this much before the picture (AAC frame padding); more = mute end screen
TAIL_EPS_S = 0.25              # a silence ending this close to the end of the audio is the tail of the video
TRAILING_SILENCE_FAIL_S = 3.0  # a silent last 3 s sounds like a broken upload and kills the end screen
SILENCE_GAP_WARN_S = 2.0       # dead air of 2 s in the body feels like a mistake to the viewer
SILENCE_NOISE_DB = -50         # below -50 dB is near silence: dead air in a speech or music mix
SILENCE_MIN_S = 1.5            # silencedetect minimum; shorter pauses are normal speech rhythm
# Total silence. A natural fade-out still reads as silence at -50 dB (over a second of it on a clean ending), so
# -60 dB is the line that separates a fade from a real dropout: a clip without audio, a cut mix, digital zero.
TOTAL_SILENCE_DB = -60
TOTAL_SILENCE_MAX_S = 1.0      # total silence longer than 1 s anywhere (body or tail) is a dropout, not a pause
TAIL_WINDOW_S = 25.0           # the last 25 s are the tail in the report (end screens run 12-20 s, plus a margin)
# --music: a music-only soundtrack has no speech pauses, so a short dip below -50 dB is already a hole in the mix.
MUSIC_HOLE_WARN_S = 0.5        # half a second of dead air in music is audible
MUSIC_HOLE_FAIL_S = 1.0        # a full second sounds like a broken mix
MUSIC_FADE_S = 3.0             # the music may fade out over the last 3 s
SILENCE_TAG_TOTAL = "tc_total"  # silencedetect instance names: two detectors share one log
SILENCE_TAG_DEAD = "tc_dead"

LUFS_WARN_LOW, LUFS_WARN_HIGH = -16.0, -12.0   # YouTube normalizes to about -14 LUFS; stay near it
LUFS_FAIL_LOW, LUFS_FAIL_HIGH = -20.0, -9.0    # far outside: quiet videos sound weak, hot ones get turned down
TRUE_PEAK_WARN_DBTP = -1.0     # above -1 dBTP lossy encoding can clip

BLACK_MIN_S = 0.3              # blackdetect minimum; shorter black frames are cuts or flashes
BLACK_PIX_TH = 0.10            # a pixel darker than 10% luminance counts as black
BLACK_FAIL_S = 1.0             # a black stretch of 1 s or more reads as a missing clip
END_GRACE_S = 1.0              # a fade to black in the final second is allowed

FREEZE_NOISE = 0.003           # freezedetect noise tolerance (compression noise on a still frame stays below it)
FREEZE_MIN_S = 2.0             # freezedetect minimum duration
HOOK_FREEZE_FAIL_S = 3.0       # the hook must move: a still picture for 3 s early on loses viewers
STILL_FAIL_S = 3.0             # animated: nothing outside the end screen stands fully still for 3 s
STILL_WARN_S = 2.0             # animated: 2-3 s holds are borderline (a reviewed explainer had several 2.1-2.4 s holds)
BODY_FREEZE_WARN_S = 8.0       # live: long holds on a screen recording are normal, so only warn at 8 s
END_SCREEN_S = 20.0            # end screens (last 5-20 s) are often static by design
DEFAULT_HOOK_S = 45.0          # the opening that decides retention

# Motion band. The picture is sampled at 4 fps as 96x54 gray frames; d is the mean absolute difference between
# consecutive frames (0-255). The thresholds only hold at this sampling: do not compare numbers measured otherwise.
MOTION_FPS = 4
MOTION_W, MOTION_H = 96, 54
EVENT_DIFF = 18.0              # d above this is a visual event: a cut, a punch-in, a text pop
FROZEN_DIFF = 0.15             # d below this is the same frame (a still, a chart that stopped)
SLOW_DIFF = 1.2                # d below this looks still to the eye; a slow zoom on a still image measures ~0.3-0.9
STILL_RUN_S = 3.0              # frozen and slow only count in runs of 3 s or more
# Reference band, calibrated on reference faceless explainer videos that hold attention well: 38-66 events/min,
# frozen 0-2.2 %, slow 1.6-4.9 %. Low events/min depends on the genre, so it only warns; override with --motion-band.
DEFAULT_BAND = {"min_events": 38.0, "max_frozen": 2.2, "max_slow": 4.9}
SLOW_WINDOWS_KEPT = 5          # longest slow windows listed in the report (where to look)

SUB_END_TOLERANCE_S = 0.5      # a cue may outlast the picture by this much
SUB_MAX_LINE_CHARS = 42        # common broadcast/streaming limit for one subtitle line
SUB_MAX_LINES = 2              # more than two lines covers the picture

CUE_EARLY_TOL_S = 0.15         # a cue may close this much before its last word ends (aligner jitter)
DRIFT_FAIL_S = 0.25            # median cue-vs-speech offset above this is a systematic shift, not jitter
DRIFT_VERSION_S = 0.5          # above this the words file may belong to another audio version
WORDS_MIN_MATCH = 0.5          # under half the subtitle words found in the words file = different text or audio
MATCH_MAX_DISTANCE_S = 2.0     # a matched word this far from its cue is a bad match; the cue is skipped
WORDS_END_TOLERANCE_S = 0.5    # words running past the video end by more than this = another audio version
EARLY_CUES_LISTED = 10         # early cues listed one by one; the rest are counted
FRESHNESS_TOLERANCE_S = 1.0    # an input saved within a second of the render is not "newer"

FULL_SAMPLE = (5, 320)         # (fps, width) for black and freeze detection
FAST_SAMPLE = (2, 160)
FFMPEG_TIMEOUT_S = 3600
FFPROBE_TIMEOUT_S = 120

EXEMPTION_GLOB = "RENDER-EXEMPTION-*.md"
EXEMPTION_REASON_MIN = 12      # a reason shorter than this is a stamp, not a reason

ANIMATED, LIVE = "animated", "live"
PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"

CHECK_NAMES = ("decode", "video_stream", "audio_stream", "audio_length", "total_silence", "trailing_silence",
               "silence_gap", "loudness", "true_peak", "black", "freeze", "hook_motion", "motion", "subtitle_end",
               "subtitle_overlap", "subtitle_lines", "subtitle_text", "subtitle_words", "subtitle_drift",
               "freshness")
NOT_EXEMPTABLE = {"decode"}    # a file ffmpeg cannot decode is broken, whatever the reason


class UsageError(Exception):
    """Raised for exit-code-2 problems (missing file, missing tool, unreadable media)."""


# ----------------------------------------------------------------------------------------------- helpers

def fmt_time(seconds: float | None) -> str:
    """Seconds -> 'mm:ss.s' (minutes can exceed 59)."""
    if seconds is None:
        return "--:--.-"
    seconds = max(0.0, float(seconds))
    minutes, sec = divmod(seconds, 60.0)
    if round(sec, 1) >= 60.0:
        minutes, sec = minutes + 1, 0.0
    return f"{int(minutes):02d}:{sec:04.1f}"


def fmt_age(seconds: float) -> str:
    """A time difference in words: '40 s', '2 min', '1.5 h' (never '0.0 h')."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 360:   # under 0.1 h: hours would round to 0.0 or 0.1
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def find_project(video: Path) -> Path:
    """The project folder: parent of `render/` if the video sits in a folder named render, else its folder."""
    parent = video.resolve().parent
    if parent.name.lower() == "render":
        return parent.parent
    return parent


def rel_path(path: Path, base: Path) -> str:
    """Path relative to base in POSIX form; never absolute (falls back to the file name)."""
    try:
        rel = os.path.relpath(str(Path(path).resolve()), str(Path(base).resolve()))
    except ValueError:  # different drive on Windows
        return Path(path).name
    if os.path.isabs(rel) or rel == ".." or rel.startswith(".." + os.sep) or rel.startswith("../"):
        return Path(path).name  # outside the project: keep only the name, never parent folder names
    return Path(rel).as_posix()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Full hex SHA-256, streamed so large videos do not load into memory."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _run(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _run_bytes(cmd: list[str], timeout: float) -> tuple[int, bytes, str]:
    """Like _run, but stdout stays binary (the motion frames come through it)."""
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout or b"", (proc.stderr or b"").decode("utf-8", errors="replace")


def _finite(value):
    """Replace non-finite floats with None so the report stays valid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def check(name: str, status: str, detail: str, start: float | None = None, end: float | None = None) -> dict:
    item = {"name": name, "status": status, "detail": detail}
    if start is not None:
        item["start"] = round(float(start), 2)
    if end is not None:
        item["end"] = round(float(end), 2)
    return item


def read_text_any(path: Path) -> str:
    """Text file -> str: UTF-8 (with or without BOM) or UTF-16 with a BOM."""
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


read_subtitles = read_text_any  # subtitle files use the same decoding rules


# ----------------------------------------------------------------------------------------------- pure parsers

def parse_rate(text: str | None) -> float | None:
    """'30000/1001' or '25' -> frames per second (None when unknown)."""
    if not text:
        return None
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            num_f, den_f = float(num), float(den)
            return num_f / den_f if den_f else None
        return float(text)
    except ValueError:
        return None


_TAG_DURATION_RE = re.compile(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)")


def _stream_duration(stream: dict) -> float | None:
    """A stream's length: its `duration`, else the Matroska/WebM `DURATION` tag ('HH:MM:SS.fffffffff')."""
    try:
        if stream.get("duration"):
            return float(stream["duration"])
    except ValueError:
        pass
    for key, value in (stream.get("tags") or {}).items():
        if key.upper().startswith("DURATION"):
            m = _TAG_DURATION_RE.fullmatch(str(value).strip())
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


def parse_ffprobe(text: str) -> dict:
    """`ffprobe -of json -show_format -show_streams` output -> stream facts."""
    data = json.loads(text)
    duration = float(data.get("format", {}).get("duration") or 0.0)
    info = {"duration": duration, "has_video": False, "has_audio": False, "video_duration": None,
            "audio_duration": None, "width": None, "height": None, "fps": None,
            "video_index": None, "audio_index": None}
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info["has_video"]:
            if (stream.get("disposition") or {}).get("attached_pic"):
                continue  # cover art is not a picture track
            info["has_video"] = True
            info["video_index"] = stream.get("index")
            info["width"], info["height"] = stream.get("width"), stream.get("height")
            fps = parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate"))
            info["fps"] = round(fps, 3) if fps else None
            info["video_duration"] = _stream_duration(stream) or duration
        elif kind == "audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["audio_index"] = stream.get("index")
            info["audio_duration"] = _stream_duration(stream) or duration
    return info


_BLACK_RE = re.compile(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)")
_FREEZE_RE = re.compile(r"freezedetect\.freeze_(start|end):\s*(-?[\d.]+)")
_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")
# "[silencedetect@tc_total @ 0x...] silence_start: 1.5"; older ffmpeg names the instance "tc_total" only.
_SILENCE_LINE_RE = re.compile(r"\[([^\[\]]+?) @ [^\[\]]*\]\s*silence_(start|end):\s*(-?[\d.]+)")


def _pair_events(events: list[tuple[str, float]], until: float | None) -> list[list[float]]:
    """start/end events -> [start, end] intervals; an unclosed start runs to `until`."""
    out: list[list[float]] = []
    start = None
    for kind, value in events:
        if kind == "start":
            start = value
        elif start is not None:
            out.append([round(max(0.0, start), 3), round(value, 3)])
            start = None
    if start is not None:
        end = until if until is not None else start
        out.append([round(max(0.0, start), 3), round(max(start, end), 3)])
    return out


def parse_blackdetect(stderr: str) -> list[list[float]]:
    """blackdetect lines -> [[start, end], ...]."""
    return [[round(float(a), 3), round(float(b), 3)] for a, b in _BLACK_RE.findall(stderr)]


def parse_freezedetect(stderr: str, duration: float | None = None) -> list[list[float]]:
    """freezedetect metadata lines -> [[start, end], ...]; a freeze still open at the end runs to `duration`."""
    return _pair_events([(k, float(v)) for k, v in _FREEZE_RE.findall(stderr)], duration)


def parse_silencedetect(stderr: str, duration: float | None = None, tag: str | None = None) -> list[list[float]]:
    """silencedetect lines -> [[start, end], ...]; a silence still open at the end runs to `duration`.

    With `tag`, only lines from the silencedetect instance of that name count (the decode pass runs two detectors,
    one for total silence and one for dead air, and both write to the same log).
    """
    if tag is None:
        return _pair_events([(k, float(v)) for k, v in _SILENCE_RE.findall(stderr)], duration)
    events = [(k, float(v)) for ctx, k, v in _SILENCE_LINE_RE.findall(stderr)
              if ctx == tag or ctx.endswith("@" + tag)]
    return _pair_events(events, duration)


def _num(text: str) -> float:
    text = text.strip().lower()
    if text in ("-inf", "inf", "+inf", "nan"):
        return float("-inf")
    return float(text)


def parse_loudness(stderr: str) -> dict | None:
    """Integrated loudness (LUFS), true peak (dBTP) and loudness range from ebur128 or loudnorm output.

    Accepts the ebur128 `Summary:` block (with peak=true) or loudnorm's print_format=json block.
    Returns {"lufs", "true_peak", "lra"} or None when neither is present.
    """
    pos = stderr.rfind("Summary:")
    if pos >= 0:
        summary = stderr[pos:]
        m_i = re.search(r"\bI:\s+(-?inf|[-+]?[\d.]+)\s+LUFS", summary)
        if m_i:
            m_lra = re.search(r"\bLRA:\s+(-?inf|[-+]?[\d.]+)\s+LU\b", summary)
            m_tp = re.search(r"True peak:.*?Peak:\s+(-?inf|[-+]?[\d.]+)\s+dB", summary, re.S)
            return {"lufs": _num(m_i.group(1)),
                    "true_peak": _num(m_tp.group(1)) if m_tp else None,
                    "lra": _num(m_lra.group(1)) if m_lra else None}
    pos = stderr.rfind('"input_i"')
    if pos >= 0:
        start = stderr.rfind("{", 0, pos)
        end = stderr.find("}", pos)
        if start >= 0 and end > start:
            try:
                block = json.loads(stderr[start:end + 1])
                return {"lufs": _num(str(block["input_i"])),
                        "true_peak": _num(str(block["input_tp"])) if "input_tp" in block else None,
                        "lra": _num(str(block["input_lra"])) if "input_lra" in block else None}
            except (ValueError, KeyError):
                return None
    return None


_SRT_STAMP = r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})"   # hours are optional in WebVTT (00:01.000)
_SRT_TIME_RE = re.compile(_SRT_STAMP + r"\s*-->\s*" + _SRT_STAMP)
_TAG_RE = re.compile(r"<[^>]*>|\{\\[^}]*\}")


def _srt_seconds(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_srt(text: str) -> list[dict]:
    """SRT text -> [{"index", "start", "end", "lines"}]. Tolerates BOM, CRLF and missing cue numbers."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[dict] = []
    for block in re.split(r"\n\s*\n", text):
        rows = [r for r in block.split("\n") if r.strip() != ""]
        for i, row in enumerate(rows):
            m = _SRT_TIME_RE.search(row)
            if not m:
                continue
            g = m.groups()
            lines = [_TAG_RE.sub("", r).strip() for r in rows[i + 1:]]
            index = len(cues) + 1
            if i > 0 and rows[i - 1].strip().isdigit():
                index = int(rows[i - 1].strip())
            cues.append({"index": index, "start": _srt_seconds(*g[:4]), "end": _srt_seconds(*g[4:]),
                         "lines": [ln for ln in lines if ln]})
            break
    return cues


_WORD_KEYS = ("w", "word", "text")
_START_KEYS = ("s", "start")
_END_KEYS = ("e", "end")


def _first(item: dict, keys: tuple):
    for key in keys:
        if item.get(key) is not None:
            return item[key]
    return None


def parse_words(data) -> list[dict]:
    """Word-timed JSON -> [{"w", "s", "e"}] sorted by start.

    Accepts {"segments": [{"words": [...]}]} (Whisper), {"words": [...]}, a list of segments, or a list of words
    keyed word/text/w, start/s and end/e. Segments without word timings are skipped (their text has spaces).
    """
    if isinstance(data, dict):
        for key in ("words", "segments"):
            if isinstance(data.get(key), list):
                return parse_words(data[key])
        raise ValueError("no 'words' or 'segments' list")
    if not isinstance(data, list):
        raise ValueError("expected an object or a list")
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("words"), list):
            out += parse_words(item["words"])
            continue
        word, start, end = _first(item, _WORD_KEYS), _first(item, _START_KEYS), _first(item, _END_KEYS)
        if word is None or start is None or end is None:
            continue
        text = str(word).strip()
        if not text or len(text.split()) > 1:
            continue
        try:
            out.append({"w": text, "s": float(start), "e": float(end)})
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda w: w["s"])
    return out


def parse_exemption(text: str) -> dict:
    """RENDER-EXEMPTION file text -> {"videos": [(path, hex)], "checks": [names], "reason": str | None}."""
    videos = [(m.group(1).strip(), m.group(2).lower())
              for m in re.finditer(r"^\s*Video:\s*(.+?)\s+sha256:([0-9a-fA-F]{12,64})\b", text, re.M)]
    checks: list[str] = []
    for m in re.finditer(r"^\s*Checks?:\s*(.+?)\s*$", text, re.M | re.I):
        checks += [c.strip().strip("`").lower() for c in re.split(r"[,\s]+", m.group(1)) if c.strip().strip("`")]
    reason = None
    m = re.search(r"^\s*Reason:\s*(.+?)\s*$", text, re.M | re.I)
    if m:
        reason = m.group(1).strip()
    return {"videos": videos, "checks": list(dict.fromkeys(checks)), "reason": reason}


# ----------------------------------------------------------------------------------------------- motion (pure)

def frame_diffs(raw: bytes, frame_size: int = MOTION_W * MOTION_H) -> list[float]:
    """Raw gray frames, back to back -> mean absolute difference of each consecutive pair (0-255)."""
    count = len(raw) // frame_size
    out: list[float] = []
    prev = raw[:frame_size]
    for i in range(1, count):
        cur = raw[i * frame_size:(i + 1) * frame_size]
        out.append(sum(map(abs, map(operator.sub, prev, cur))) / frame_size)
        prev = cur
    return out


def still_windows(diffs, threshold: float, fps: float = MOTION_FPS,
                  min_s: float = STILL_RUN_S) -> list[tuple[float, float]]:
    """(start_s, end_s) runs where d < threshold lasts at least min_s."""
    out: list[tuple[float, float]] = []
    start = None
    for i, value in enumerate(list(diffs) + [float("inf")]):
        if value < threshold:
            if start is None:
                start = i
        elif start is not None:
            if (i - start) / fps >= min_s:
                out.append((start / fps, i / fps))
            start = None
    return out


def motion_summary(diffs, fps: float = MOTION_FPS) -> dict | None:
    """Difference series -> events/min, frozen %, slow % and the longest slow windows (None when empty)."""
    if not diffs:
        return None
    duration = (len(diffs) + 1) / fps
    ordered = sorted(diffs)
    frozen = still_windows(diffs, FROZEN_DIFF, fps)
    slow = still_windows(diffs, SLOW_DIFF, fps)
    longest = sorted(slow, key=lambda w: w[0] - w[1])[:SLOW_WINDOWS_KEPT]
    return {
        "duration_s": round(duration, 1),
        "events_per_min": round(60 * sum(1 for v in diffs if v > EVENT_DIFF) / duration, 1),
        "median_diff": round(ordered[len(ordered) // 2], 2),
        "frozen_pct": round(100 * sum(b - a for a, b in frozen) / duration, 1),
        "slow_pct": round(100 * sum(b - a for a, b in slow) / duration, 1),
        "slow_windows": [[round(a, 1), round(b, 1)] for a, b in longest],
    }


def motion_report(diffs: list[float], hook_seconds: float, fps: float = MOTION_FPS) -> dict:
    """Summaries for the hook window and for the whole video."""
    frames = len(diffs) + 1 if diffs else 0
    hook_frames = min(frames, int(round(max(hook_seconds, 0.0) * fps)))
    return {"fps": fps, "size": f"{MOTION_W}x{MOTION_H}", "frames": frames,
            "hook": motion_summary(diffs[:max(hook_frames - 1, 0)], fps),
            "video": motion_summary(diffs, fps)}


# ----------------------------------------------------------------------------------------------- subtitle text (pure)

_UNIT_RE = re.compile(r"^(thousand|million|billion|trillion|percent|per\s?cent|points?|times|dollars?|euros?|pounds?|"
                      r"gigawatts?|megawatts?|kilowatts?|kilomet(?:er|re)s?|miles?|tonnes?|tons?)\b", re.I)
_NUMBER_END_RE = re.compile(r"[$€£]?\d[\d.,]*%?$")
# ASR splits hyphenated words ("non -cash", "12 -month"). A letter must follow the hyphen: "from -32.7%" is a number.
_HYPHEN_GAP_RE = re.compile(r"\w -[^\W\d_]")


def normalize_word(text: str) -> str:
    """Comparable form of a word: accents and punctuation dropped, case folded ('Percent.' -> 'percent')."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", "", text.casefold())


def match_cues_to_words(cues: list[dict], words: list[dict]) -> tuple[list[dict | None], float]:
    """For each cue, the index of the first and the last spoken word it shows (or None), and the share of
    subtitle words that match the words file exactly.

    The match is by text (difflib), not by time: subtitles often write "$54.2" where the words file says
    "fifty-four point two". Equal runs map one to one, replaced runs proportionally. Spoken words with no subtitle
    counterpart ("dollars") are given to the cue on screen when they start.
    """
    tokens: list[tuple[str, int]] = []
    for ci, cue in enumerate(cues):
        for raw in " ".join(cue["lines"]).split():
            norm = normalize_word(raw)
            if norm:
                tokens.append((norm, ci))
    spoken = [normalize_word(w["w"]) for w in words]
    shown = [t[0] for t in tokens]
    if not shown or not spoken:
        return [None] * len(cues), 0.0
    link: list[int | None] = [None] * len(shown)
    exact = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, shown, spoken, autojunk=False).get_opcodes():
        if op == "equal":
            exact += i2 - i1
            for k in range(i2 - i1):
                link[i1 + k] = j1 + k
        elif op == "replace":
            n, m = i2 - i1, j2 - j1
            for k in range(n):
                link[i1 + k] = j1 + max(0, -(-(k + 1) * m // n) - 1)   # ceil((k+1)*m/n) - 1
    linked = {j for j in link if j is not None}
    by_cue: dict[int, list[int]] = {}
    for ai, (_, ci) in enumerate(tokens):
        by_cue.setdefault(ci, []).append(ai)
    out: list[dict | None] = []
    for ci, cue in enumerate(cues):
        idx = [link[a] for a in by_cue.get(ci, []) if link[a] is not None]
        if not idx:
            out.append(None)
            continue
        first, last = min(idx), max(idx)
        next_start = cues[ci + 1]["start"] if ci + 1 < len(cues) else float("inf")
        while last + 1 < len(spoken) and (last + 1) not in linked and words[last + 1]["s"] < next_start:
            last += 1
        out.append({"first": first, "last": last})
    return out, exact / len(shown)


# ----------------------------------------------------------------------------------------------- evaluation

def _check_streams(m: dict) -> list[dict]:
    out = []
    if m.get("has_video"):
        fps = m.get("fps")
        fps_txt = f" @ {fps:g} fps" if fps else ""
        out.append(check("video_stream", PASS, f"{m.get('width')}x{m.get('height')}{fps_txt}"))
    else:
        out.append(check("video_stream", FAIL, "no video stream"))
    if m.get("has_audio"):
        out.append(check("audio_stream", PASS, "audio stream present"))
    else:
        out.append(check("audio_stream", FAIL, "no audio stream: a video needs sound"))
    return out


def _check_audio(m: dict) -> list[dict]:
    out: list[dict] = []
    if not m.get("has_audio"):
        return out
    video_end = m.get("video_duration") or m.get("duration") or 0.0
    audio_end = m.get("audio_duration") or m.get("duration") or 0.0
    if m.get("has_video"):
        gap = video_end - audio_end
        if gap > AUDIO_SHORT_MAX_S:
            out.append(check("audio_length", FAIL,
                             f"audio ends {gap:.1f} s before the picture (audio {audio_end:.1f} s, video "
                             f"{video_end:.1f} s): the end screen needs sound too", audio_end, video_end))
        else:
            out.append(check("audio_length", PASS, f"audio runs to the end (gap {max(gap, 0.0):.2f} s)"))

    out += _check_silence(m, audio_end)

    lufs = m.get("lufs")
    if lufs is None:
        out.append(check("loudness", WARN, "integrated loudness could not be measured"))
    elif not math.isfinite(lufs):
        out.append(check("loudness", FAIL, "the audio track is digital silence"))
    elif lufs < LUFS_FAIL_LOW or lufs > LUFS_FAIL_HIGH:
        out.append(check("loudness", FAIL, f"integrated {lufs:.1f} LUFS, outside {LUFS_FAIL_LOW:g}..{LUFS_FAIL_HIGH:g}"
                                           " (YouTube plays at about -14)"))
    elif lufs < LUFS_WARN_LOW or lufs > LUFS_WARN_HIGH:
        out.append(check("loudness", WARN, f"integrated {lufs:.1f} LUFS, outside {LUFS_WARN_LOW:g}..{LUFS_WARN_HIGH:g}"
                                           " (YouTube plays at about -14)"))
    else:
        out.append(check("loudness", PASS, f"integrated {lufs:.1f} LUFS"))

    peak = m.get("true_peak")
    if peak is not None and math.isfinite(peak):
        if peak > TRUE_PEAK_WARN_DBTP:
            out.append(check("true_peak", WARN, f"true peak {peak:.1f} dBTP, above {TRUE_PEAK_WARN_DBTP:g} dBTP"))
        else:
            out.append(check("true_peak", PASS, f"true peak {peak:.1f} dBTP"))
    return out


def _check_silence(m: dict, audio_end: float) -> list[dict]:
    """Total silence (-60 dB) anywhere, then dead air (-50 dB) at the end and in the body.

    A -50 dB run that overlaps a failed total silence is the same gap and is not reported twice. With
    m["music"] the soundtrack is music only, so body dead air uses the tighter music limits. The numbers behind the
    checks are written back into m["silence_summary"].
    """
    music = bool(m.get("music"))
    at_end = audio_end - TAIL_EPS_S
    tail_start = max(audio_end - TAIL_WINDOW_S, audio_end / 2)   # a short video keeps a body: at most its 2nd half
    tail_s = round(audio_end - tail_start, 1)
    totals = m.get("total_silences") or []

    def in_tail(run) -> bool:
        return run[0] >= tail_start or run[1] >= at_end

    out: list[dict] = []
    failed: list[tuple[float, float]] = []
    for start, end in totals:
        length = end - start
        if length <= TOTAL_SILENCE_MAX_S:
            continue
        if end >= at_end:
            where = "at the end"
        elif in_tail((start, end)):
            where = f"in the tail (last {tail_s:g} s)"
        else:
            where = "in the body"
        out.append(check("total_silence", FAIL, f"{length:.1f} s of total silence (below {TOTAL_SILENCE_DB:g} dB) "
                                                f"{where}: a dropout, not a pause (limit {TOTAL_SILENCE_MAX_S:g} s)",
                         start, end))
        failed.append((start, end))
    if not failed:
        out.append(check("total_silence", PASS, f"no total silence (below {TOTAL_SILENCE_DB:g} dB) longer than "
                                                f"{TOTAL_SILENCE_MAX_S:g} s"))

    trailing, gaps, holes = [], [], []
    fade_start = audio_end - MUSIC_FADE_S
    for start, end in m.get("silences") or []:
        length = end - start
        if any(start < f_end and end > f_start for f_start, f_end in failed):
            continue   # the same gap, already a total_silence FAIL
        if end >= at_end:
            if length >= TRAILING_SILENCE_FAIL_S:
                trailing.append(check("trailing_silence", FAIL,
                                      f"the last {length:.1f} s are silent (limit {TRAILING_SILENCE_FAIL_S:g} s)",
                                      start, end))
            elif length >= SILENCE_GAP_WARN_S:
                trailing.append(check("trailing_silence", WARN, f"silent tail of {length:.1f} s", start, end))
        elif music:
            if start >= fade_start or length < MUSIC_HOLE_WARN_S:
                continue   # the final fade-out, or a dip too short to hear
            holes.append([round(start, 3), round(end, 3)])
            status = FAIL if length >= MUSIC_HOLE_FAIL_S else WARN
            gaps.append(check("silence_gap", status, f"{length:.1f} s of dead air (below {SILENCE_NOISE_DB:g} dB) in "
                                                     f"a music-only soundtrack (WARN at {MUSIC_HOLE_WARN_S:g} s, FAIL "
                                                     f"at {MUSIC_HOLE_FAIL_S:g} s)", start, end))
        elif length >= SILENCE_GAP_WARN_S:
            gaps.append(check("silence_gap", WARN, f"{length:.1f} s of silence in the body", start, end))

    end_failed = any(f_end >= at_end for _, f_end in failed)
    body_failed = any(f_end < at_end for _, f_end in failed)
    if not trailing:
        trailing = [check("trailing_silence", INFO, "the silent end is reported under total_silence") if end_failed
                    else check("trailing_silence", PASS, "sound continues to the end")]
    if not gaps:
        if body_failed:
            gaps = [check("silence_gap", INFO, "the silent gap is reported under total_silence")]
        elif music:
            gaps = [check("silence_gap", PASS, f"no dead air of {MUSIC_HOLE_WARN_S:g} s or more in the music")]
        else:
            gaps = [check("silence_gap", PASS, f"no silent gap of {SILENCE_GAP_WARN_S:g} s or more")]
    out += trailing + gaps

    def longest(runs) -> float:
        return round(max((e - s for s, e in runs), default=0.0), 3)

    summary = {"total_silence_db": TOTAL_SILENCE_DB, "total_silence_limit_s": TOTAL_SILENCE_MAX_S,
               "dead_air_db": SILENCE_NOISE_DB, "music_only": music,
               "longest_total_silence_body_s": longest([r for r in totals if not in_tail(r)]),
               "longest_total_silence_tail_s": longest([r for r in totals if in_tail(r)]),
               "tail_window_s": tail_s}
    if music:
        summary["music_holes"] = holes
    m["silence_summary"] = summary
    return out


def _check_black(m: dict) -> list[dict]:
    if not m.get("has_video"):
        return []
    video_end = m.get("video_duration") or m.get("duration") or 0.0
    limit = video_end - END_GRACE_S
    out = []
    for start, end in m.get("black") or []:
        effective = min(end, limit) - start
        length = end - start
        if effective >= BLACK_FAIL_S:
            out.append(check("black", FAIL, f"{length:.1f} s of black picture", start, end))
        elif effective >= BLACK_MIN_S:
            out.append(check("black", WARN, f"{length:.1f} s of black picture (a flash or a gap?)", start, end))
        else:
            out.append(check("black", INFO, f"fade to black at the end ({length:.1f} s)", start, end))
    return out or [check("black", PASS, f"no black stretch of {BLACK_MIN_S:g} s or more")]


def _check_freeze(m: dict, hook_seconds: float, profile: str = ANIMATED) -> list[dict]:
    if not m.get("has_video"):
        return []
    duration = m.get("video_duration") or m.get("duration") or 0.0
    hook_end = min(max(hook_seconds, 0.0), duration)
    end_screen = max(hook_end, duration - END_SCREEN_S)
    out = []
    for start, end in m.get("freezes") or []:
        in_hook = max(0.0, min(end, hook_end) - max(start, 0.0))
        in_end = max(0.0, min(end, duration) - max(start, end_screen))
        length = end - start
        if in_hook >= HOOK_FREEZE_FAIL_S:
            out.append(check("freeze", FAIL, f"picture frozen for {in_hook:.1f} s inside the first {hook_end:g} s: "
                                             "the hook must move", start, min(end, hook_end)))
            continue
        if profile == LIVE:
            in_body = max(0.0, min(end, end_screen) - max(start, hook_end))
            if in_body >= BODY_FREEZE_WARN_S:
                out.append(check("freeze", WARN, f"picture frozen for {in_body:.1f} s in the body", start, end))
                continue
        else:
            held = max(0.0, min(end, end_screen) - max(start, 0.0))   # the still part before the end screen
            where = "in the hook" if start < hook_end else "in the body"
            if held >= STILL_FAIL_S:
                out.append(check("freeze", FAIL, f"picture frozen for {held:.1f} s {where}: nothing outside the end "
                                                 f"screen should stand fully still for {STILL_FAIL_S:g} s",
                                 start, min(end, end_screen)))
                continue
            if held >= STILL_WARN_S:
                out.append(check("freeze", WARN, f"picture frozen for {held:.1f} s {where}",
                                 start, min(end, end_screen)))
                continue
        if in_end > 0:
            out.append(check("freeze", INFO, f"static picture for {length:.1f} s in the last {END_SCREEN_S:g} s "
                                             "(end screens are often static)", start, end))
    if not any(c["status"] in (FAIL, WARN) for c in out):
        if profile == LIVE:
            text = (f"no frozen stretch of {HOOK_FREEZE_FAIL_S:g} s in the hook or {BODY_FREEZE_WARN_S:g} s in "
                    "the body")
        else:
            text = f"no frozen stretch of {STILL_WARN_S:g} s or more outside the end screen"
        out.insert(0, check("freeze", PASS, text))
    return out


def _check_motion(m: dict, hook_seconds: float, profile: str = ANIMATED, band: dict | None = None) -> list[dict]:
    motion = m.get("motion")
    if not motion or not m.get("has_video"):
        return []
    band = band or DEFAULT_BAND
    out: list[dict] = []
    hook = motion.get("hook")
    if hook is None:
        if hook_seconds > 0:
            out.append(check("hook_motion", WARN, "hook motion could not be measured (too few frames)"))
    else:
        problems = []
        if hook["frozen_pct"] > band["max_frozen"]:
            problems.append(f"frozen {hook['frozen_pct']:g}% (limit {band['max_frozen']:g}%)")
        if hook["slow_pct"] > band["max_slow"]:
            problems.append(f"slow {hook['slow_pct']:g}% (limit {band['max_slow']:g}%)")
        low_events = hook["events_per_min"] < band["min_events"]
        events = f"{hook['events_per_min']:g} events/min"
        if problems:
            if low_events:
                problems.append(f"{events} (reference {band['min_events']:g}+)")
            detail = (f"the hook (first {hook['duration_s']:g} s) stands still: {', '.join(problems)}; a still "
                      "picture or a slow zoom on a still image loses viewers, change the shot every few seconds")
            status = FAIL
            if profile == LIVE:
                status = WARN
                detail += " (profile live: check by eye that a face or cursor carries the opening)"
            window = hook["slow_windows"][0] if hook["slow_windows"] else [None, None]
            out.append(check("hook_motion", status, detail, window[0], window[1]))
        elif low_events:
            out.append(check("hook_motion", WARN, f"the hook has {events} (reference {band['min_events']:g}+): few "
                                                  "cuts, punch-ins or pops"))
        else:
            out.append(check("hook_motion", PASS, f"the hook moves: {events}, frozen {hook['frozen_pct']:g}%, slow "
                                                  f"{hook['slow_pct']:g}%"))
    video = motion.get("video")
    if video is not None:
        over = []
        if video["events_per_min"] < band["min_events"]:
            over.append(f"events/min under {band['min_events']:g}")
        if video["frozen_pct"] > band["max_frozen"]:
            over.append(f"frozen over {band['max_frozen']:g}%")
        if video["slow_pct"] > band["max_slow"]:
            over.append(f"slow over {band['max_slow']:g}%")
        detail = (f"whole video: {video['events_per_min']:g} events/min, frozen {video['frozen_pct']:g}%, slow "
                  f"{video['slow_pct']:g}%")
        windows = ", ".join(f"{fmt_time(a)}-{fmt_time(b)}" for a, b in video["slow_windows"][:3])
        if over:
            detail += f" (outside the reference band: {', '.join(over)})"
        if windows:
            detail += f"; longest slow windows {windows}"
        out.append(check("motion", WARN if over and profile == ANIMATED else INFO, detail))
    return out


def _check_subtitles(m: dict, cues: list[dict]) -> list[dict]:
    if not cues:
        return [check("subtitle_end", FAIL, "no readable cues in the subtitle file (check its format and encoding)")]
    duration = m.get("duration") or 0.0
    out = []
    late = [c for c in cues if c["end"] > duration + SUB_END_TOLERANCE_S]
    if late:
        first = late[0]
        out.append(check("subtitle_end", FAIL,
                         f"{len(late)} cue(s) end after the video ({duration:.1f} s); first is #{first['index']} "
                         f"ending at {fmt_time(first['end'])}", first["start"], first["end"]))
    else:
        out.append(check("subtitle_end", PASS, f"all {len(cues)} cues end inside the video"))

    ordered = sorted(cues, key=lambda c: (c["start"], c["end"]))
    overlaps = [(a, b) for a, b in zip(ordered, ordered[1:]) if b["start"] < a["end"] - 0.001]
    if overlaps:
        a, b = overlaps[0]
        out.append(check("subtitle_overlap", WARN,
                         f"{len(overlaps)} overlapping cue pair(s); first is #{a['index']} and #{b['index']}",
                         b["start"], a["end"]))
    else:
        out.append(check("subtitle_overlap", PASS, "no overlapping cues"))

    long_lines = [c for c in cues if any(len(ln) > SUB_MAX_LINE_CHARS for ln in c["lines"])]
    many_lines = [c for c in cues if len(c["lines"]) > SUB_MAX_LINES]
    if long_lines:
        c = long_lines[0]
        longest = max(len(ln) for ln in c["lines"])
        out.append(check("subtitle_lines", WARN,
                         f"{len(long_lines)} cue(s) have a line over {SUB_MAX_LINE_CHARS} characters; first is "
                         f"#{c['index']} ({longest} characters)", c["start"], c["end"]))
    if many_lines:
        c = many_lines[0]
        out.append(check("subtitle_lines", WARN,
                         f"{len(many_lines)} cue(s) have more than {SUB_MAX_LINES} lines; first is #{c['index']} "
                         f"({len(c['lines'])} lines)", c["start"], c["end"]))
    if not long_lines and not many_lines:
        out.append(check("subtitle_lines", PASS,
                         f"lines within {SUB_MAX_LINE_CHARS} characters, at most {SUB_MAX_LINES} per cue"))
    return out


def _splits_number(left: str, right: str) -> bool:
    """True when `left` ends with a number and `right` starts with its unit ('54.2' | 'billion')."""
    left_tokens, right_tokens = left.split(), right.split()
    return bool(left_tokens and right_tokens and _NUMBER_END_RE.search(left_tokens[-1])
                and _UNIT_RE.match(right_tokens[0]))


def _check_subtitle_text(cues: list[dict]) -> list[dict]:
    """Rules that need only the subtitle text: number and unit together, no ASR hyphen gaps."""
    out = []
    ordered = sorted(cues, key=lambda c: (c["start"], c["end"]))
    for ci, cue in enumerate(ordered):
        lines = cue["lines"]
        for line in lines:
            if _HYPHEN_GAP_RE.search(line):
                out.append(check("subtitle_text", WARN, f"#{cue['index']} has a speech-recognition hyphen gap: "
                                                        f"\"{line}\" (write \"non-cash\", not \"non -cash\")",
                                 cue["start"], cue["end"]))
        for upper, lower in zip(lines, lines[1:]):
            if _splits_number(upper, lower):
                out.append(check("subtitle_text", WARN, f"#{cue['index']} splits a number from its unit across "
                                                        f"lines: \"{upper} / {lower}\"", cue["start"], cue["end"]))
        if ci + 1 < len(ordered) and lines and ordered[ci + 1]["lines"]:
            nxt = ordered[ci + 1]
            if _splits_number(lines[-1], nxt["lines"][0]):
                out.append(check("subtitle_text", WARN, f"#{cue['index']} and #{nxt['index']} split a number from "
                                                        f"its unit: \"{lines[-1].split()[-1]} | "
                                                        f"{nxt['lines'][0].split()[0]}\"", cue["start"], nxt["end"]))
    return out or [check("subtitle_text", PASS, "numbers stay with their units; no hyphen gaps")]


def _check_subtitle_words(m: dict, cues: list[dict], words: list[dict] | None) -> list[dict]:
    """Each cue must stay on screen until its last word is spoken; a shared offset is reported once as drift."""
    if not cues:
        return []
    if not words:
        return [check("subtitle_words", INFO, "cue ends against the spoken words not measured: pass --words "
                                              "(for example the transcript check-voiceover saved)")]
    out: list[dict] = []
    stats: dict = {"words": len(words)}
    m["subtitle_words"] = stats
    duration = m.get("duration") or 0.0
    if duration and words[-1]["e"] > duration + WORDS_END_TOLERANCE_S:
        out.append(check("subtitle_words", WARN, f"the words file ends at {fmt_time(words[-1]['e'])} but the video "
                                                 f"at {fmt_time(duration)}: another audio version, or a wrong "
                                                 "--words-offset?"))
    ordered = sorted(cues, key=lambda c: (c["start"], c["end"]))
    matches, ratio = match_cues_to_words(ordered, words)
    stats["match_ratio"] = round(ratio, 3)
    if ratio < WORDS_MIN_MATCH:
        out.append(check("subtitle_words", FAIL, f"only {ratio:.0%} of the subtitle words are in the words file: a "
                                                 "different script or audio version, so cue ends cannot be "
                                                 "measured; pass the words file of this narration"))
        return out
    measured, skipped, drifts = [], 0, []
    for cue, match in zip(ordered, matches):
        if match is None:
            skipped += 1
            continue
        last, first = words[match["last"]], words[match["first"]]
        if last["s"] > cue["end"] + MATCH_MAX_DISTANCE_S or last["e"] < cue["start"] - MATCH_MAX_DISTANCE_S:
            skipped += 1
            continue
        drifts.append(cue["start"] - first["s"])
        measured.append((cue, last, last["e"] - cue["end"]))
    drift = statistics.median(drifts) if drifts else 0.0
    # A cue's own lateness: when subtitles run ahead (drift < 0) every cue closes early by that much, which the
    # drift check reports once, so it is taken out. When they lag (drift > 0) the raw gap is kept, otherwise a cue
    # that closes after its word would look early. Hence min(gap, gap + drift).
    early = []
    for cue, last, gap in measured:
        own = min(gap, gap + drift)
        if own > CUE_EARLY_TOL_S:
            early.append({"cue": cue["index"], "cue_end": round(cue["end"], 3), "word": last["w"],
                          "word_end": round(last["e"], 3), "gap_s": round(gap, 3), "own_gap_s": round(own, 3)})
    stats.update({"cues_measured": len(measured), "cues_skipped": skipped, "median_drift_s": round(drift, 3),
                  "early_cues": len(early), "worst_early_s": max((e["own_gap_s"] for e in early), default=0.0),
                  "early": early})
    for e in early[:EARLY_CUES_LISTED]:
        out.append(check("subtitle_words", FAIL,
                         f"#{e['cue']} closes at {fmt_time(e['cue_end'])} but its last word \"{e['word']}\" ends at "
                         f"{fmt_time(e['word_end'])} ({e['own_gap_s']:.2f} s early after removing the drift; limit "
                         f"{CUE_EARLY_TOL_S:g} s)", e["cue_end"], e["word_end"]))
    if len(early) > EARLY_CUES_LISTED:
        out.append(check("subtitle_words", FAIL, f"{len(early) - EARLY_CUES_LISTED} more cue(s) close early (all "
                                                 "listed in measured.subtitle_words.early)"))
    if not early:
        note = f", {skipped} cue(s) not matched" if skipped else ""
        out.append(check("subtitle_words", PASS, f"all {len(measured)} matched cues stay up until their last word "
                                                 f"ends (match {ratio:.0%}{note})"))
    direction = "ahead of" if drift < 0 else "behind"
    if abs(drift) > DRIFT_FAIL_S:
        hint = " (over 0.5 s: the words may belong to another audio version)" if abs(drift) > DRIFT_VERSION_S else ""
        out.append(check("subtitle_drift", FAIL, f"subtitles run {abs(drift):.2f} s {direction} the speech (median "
                                                 f"of cue start minus first word start; limit {DRIFT_FAIL_S:g} s): "
                                                 f"time the cues from the word timings{hint}"))
    elif abs(drift) > CUE_EARLY_TOL_S:
        out.append(check("subtitle_drift", WARN, f"subtitles run {abs(drift):.2f} s {direction} the speech (FAIL "
                                                 f"above {DRIFT_FAIL_S:g} s): two aligners may disagree slightly"))
    else:
        out.append(check("subtitle_drift", PASS, f"no systematic drift ({drift:+.2f} s)"))
    return out


def _check_freshness(m: dict) -> list[dict]:
    inputs = m.get("inputs_checked")
    if not inputs:
        return []
    stale = m.get("stale_inputs") or []
    if not stale:
        return [check("freshness", PASS, f"{', '.join(inputs)} older than the video")]
    return [check("freshness", WARN, f"{s['file']} is {fmt_age(s['newer_by_s'])} newer than the video: the render "
                                     "may predate its inputs") for s in stale]


def evaluate(measured: dict, cues: list[dict] | None = None, hook_seconds: float = DEFAULT_HOOK_S,
             profile: str = ANIMATED, words: list[dict] | None = None, band: dict | None = None) -> list[dict]:
    """Measurements (and optional subtitle cues and spoken words) -> list of checks. No files, no ffmpeg.

    `measured` keys: duration, video_duration, audio_duration, has_video, has_audio, width, height, fps,
    lufs, true_peak, black, freezes, silences (-50 dB runs), total_silences (-60 dB runs; intervals as
    [start, end] seconds), and optionally music (True for a music-only soundtrack), motion (see motion_report),
    inputs_checked and stale_inputs. Word timings in `words` are in video time. The silence and subtitle-word
    statistics are written back into measured["silence_summary"] and measured["subtitle_words"].
    """
    checks = _check_streams(measured)
    checks += _check_audio(measured)
    checks += _check_black(measured)
    checks += _check_freeze(measured, hook_seconds, profile)
    checks += _check_motion(measured, hook_seconds, profile, band)
    if cues is not None:
        checks += _check_subtitles(measured, cues)
        if cues:
            checks += _check_subtitle_text(cues)
            checks += _check_subtitle_words(measured, cues, words)
    checks += _check_freshness(measured)
    return checks


def apply_exemptions(checks: list[dict], exemptions: list[dict]) -> list[dict]:
    """Turn the FAIL of every check named in a matching exemption into WARN, recording file and reason.
    Each exemption gets an `applied_to` list. Returns the same list for convenience."""
    for ex in exemptions:
        names = set(ex["checks"]) - NOT_EXEMPTABLE
        applied = []
        for c in checks:
            if c["status"] == FAIL and c["name"] in names:
                c["status"] = WARN
                c["exempted"] = True
                c["exemption"] = {"file": ex["file"], "reason": ex["reason"]}
                c["detail"] = f"exempted ({ex['file']}): {c['detail']}"
                applied.append(c["name"])
        ex["applied_to"] = sorted(set(applied))
    return checks


def verdict_of(checks: list[dict]) -> str:
    return FAIL if any(c["status"] == FAIL for c in checks) else PASS


# ----------------------------------------------------------------------------------------------- measuring

def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise UsageError(f"{' and '.join(missing)} not found on PATH; install ffmpeg (https://ffmpeg.org) first")


def probe(video: Path) -> dict:
    code, out, err = _run(["ffprobe", "-v", "error", "-of", "json", "-show_format", "-show_streams", str(video)],
                          FFPROBE_TIMEOUT_S)
    if code != 0:
        raise UsageError(f"ffprobe cannot read {video.name}: {err.strip()[-300:]}")
    try:
        return parse_ffprobe(out)
    except (ValueError, KeyError) as exc:
        raise UsageError(f"ffprobe output for {video.name} is not readable: {exc}") from exc


def build_decode_cmd(video: Path, has_video: bool, has_audio: bool, fast: bool = False, motion: bool = False,
                     video_index: int | None = None, audio_index: int | None = None,
                     music: bool = False) -> list[str]:
    """One decode pass. Black/freeze detection and silence/loudness go to a null output; with `motion` a second
    branch of the picture goes to stdout as raw 96x54 gray frames at 4 fps. Two named silencedetect instances run
    in the audio chain: total silence (-60 dB) and dead air (-50 dB; shorter minimum with `music`)."""
    fps, width = FAST_SAMPLE if fast else FULL_SAMPLE
    dead_min = MUSIC_HOLE_WARN_S if music else SILENCE_MIN_S
    vin = f"[0:{video_index}]" if video_index is not None else "[0:v:0]"
    ain = f"[0:{audio_index}]" if audio_index is not None else "[0:a:0]"
    detect = (f"fps={fps},scale={width}:-2,blackdetect=d={BLACK_MIN_S}:pix_th={BLACK_PIX_TH:.2f},"
              f"freezedetect=n={FREEZE_NOISE}:d={FREEZE_MIN_S:g}")
    graph = []
    if has_video:
        if motion:
            graph.append(f"{vin}split=2[vd][vm];[vd]{detect}[vq];"
                         f"[vm]fps={MOTION_FPS},scale={MOTION_W}:{MOTION_H},format=gray[mo]")
        else:
            graph.append(f"{vin}{detect}[vq]")
    if has_audio:
        graph.append(f"{ain}silencedetect@{SILENCE_TAG_TOTAL}=noise={TOTAL_SILENCE_DB}dB:d={TOTAL_SILENCE_MAX_S:g},"
                     f"silencedetect@{SILENCE_TAG_DEAD}=noise={SILENCE_NOISE_DB}dB:d={dead_min:g},"
                     "ebur128=peak=true:framelog=quiet[aq]")
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-filter_complex", ";".join(graph)]
    cmd += ["-map", "[vq]"] if has_video else ["-vn"]
    cmd += ["-map", "[aq]"] if has_audio else ["-an"]
    cmd += ["-f", "null", "-"]
    if has_video and motion:
        cmd += ["-map", "[mo]", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    return cmd


def measure(video: Path, fast: bool = False, hook_seconds: float = DEFAULT_HOOK_S,
            music: bool = False) -> tuple[dict, str | None]:
    """Run ffprobe and one ffmpeg decode pass. Returns (measured, decode_error or None)."""
    info = probe(video)
    measured = dict(info)
    measured.update({"black": [], "freezes": [], "silences": [], "total_silences": [], "lufs": None,
                     "true_peak": None, "lra": None, "fast": fast, "music": music})
    if not info["has_video"] and not info["has_audio"]:
        return measured, None
    cmd = build_decode_cmd(video, info["has_video"], info["has_audio"], fast, motion=info["has_video"],
                           video_index=info.get("video_index"), audio_index=info.get("audio_index"), music=music)
    code, raw, err = _run_bytes(cmd, FFMPEG_TIMEOUT_S)
    video_end = info["video_duration"] or info["duration"]
    audio_end = info["audio_duration"] or info["duration"]
    measured["black"] = parse_blackdetect(err)
    measured["freezes"] = parse_freezedetect(err, video_end)
    measured["silences"] = parse_silencedetect(err, audio_end, tag=SILENCE_TAG_DEAD)
    measured["total_silences"] = parse_silencedetect(err, audio_end, tag=SILENCE_TAG_TOTAL)
    loud = parse_loudness(err) if info["has_audio"] else None
    if loud:
        measured.update(loud)
    if info["has_video"]:
        measured["motion"] = motion_report(frame_diffs(raw), hook_seconds)
    decode_error = None
    if code != 0:
        decode_error = err.strip().splitlines()[-1] if err.strip() else f"ffmpeg exited with {code}"
    return measured, decode_error


def load_words(path: Path, offset: float = 0.0) -> list[dict]:
    """Word-timed JSON file -> words in video time (offset added)."""
    try:
        data = json.loads(read_text_any(path))
        words = parse_words(data)
    except OSError as exc:
        raise UsageError(f"cannot read words file: {exc}") from exc
    except ValueError as exc:
        raise UsageError(f"words file {path.name} is not word-timed JSON: {exc}") from exc
    if not words:
        raise UsageError(f"no word timings in {path.name}: it needs words with start and end times (transcribe "
                         "with word timestamps)")
    for w in words:
        w["s"] = round(w["s"] + offset, 3)
        w["e"] = round(w["e"] + offset, 3)
    return words


def stale_inputs(video: Path, inputs: list[Path], project: Path) -> list[dict]:
    """Inputs saved after the video: the render may not contain them."""
    video_mtime = video.stat().st_mtime
    out = []
    for path in inputs:
        newer = path.stat().st_mtime - video_mtime
        if newer > FRESHNESS_TOLERANCE_S:
            out.append({"file": rel_path(path, project), "newer_by_s": round(newer, 1)})
    return out


def load_exemptions(project: Path, sha256: str) -> tuple[list[dict], list[dict]]:
    """Exemption files in <project>/qa -> (exemptions that name this video's hash, notes as checks)."""
    qa = project / "qa"
    matching: list[dict] = []
    notes: list[dict] = []
    if not qa.is_dir():
        return matching, notes
    for path in sorted(qa.glob(EXEMPTION_GLOB)):
        shown = rel_path(path, project)
        try:
            parsed = parse_exemption(read_text_any(path))
        except OSError as exc:
            notes.append(check("exemption", WARN, f"cannot read {shown}: {exc}"))
            continue
        hashes = [h for _, h in parsed["videos"]]
        if not any(sha256.startswith(h) for h in hashes):
            if hashes:
                notes.append(check("exemption", INFO, f"{shown} names other renders "
                                                      f"(sha256 {', '.join(h[:12] for h in hashes)}); not applied"))
            else:
                notes.append(check("exemption", WARN, f"{shown} has no 'Video: <path> sha256:<12 hex>' line; "
                                                      "not applied"))
            continue
        reason = parsed["reason"] or ""
        if len(reason) < EXEMPTION_REASON_MIN:
            notes.append(check("exemption", WARN, f"{shown} has no 'Reason:' line (at least "
                                                  f"{EXEMPTION_REASON_MIN} characters); not applied"))
            continue
        unknown = [c for c in parsed["checks"] if c not in CHECK_NAMES or c in NOT_EXEMPTABLE]
        if unknown:
            notes.append(check("exemption", WARN, f"{shown} names check(s) that cannot be exempted: "
                                                  f"{', '.join(unknown)}"))
        known = [c for c in parsed["checks"] if c in CHECK_NAMES and c not in NOT_EXEMPTABLE]
        if not known:
            notes.append(check("exemption", WARN, f"{shown} names no check (add 'Checks: <name>, ...'); not applied"))
            continue
        matching.append({"file": shown, "checks": known, "reason": reason})
    return matching, notes


# ----------------------------------------------------------------------------------------------- output

def human_lines(report: dict, report_rel: str) -> list[str]:
    m = report["measured"]
    head = f"render_check: {report['video']}  {fmt_time(m.get('duration'))}"
    if m.get("has_video"):
        head += f"  {m.get('width')}x{m.get('height')}"
    head += f"  profile {m.get('profile', ANIMATED)}"
    if m.get("music"):
        head += "  music-only soundtrack"
    if m.get("fast"):
        head += "  (fast pass)"
    lines = [head]
    width = max([len(c["name"]) for c in report["checks"]] + [4])
    for c in report["checks"]:
        span = ""
        if "start" in c:
            span = fmt_time(c["start"]) + ("-" + fmt_time(c["end"]) if "end" in c else "")
        lines.append(f"{c['status']:<4}  {c['name']:<{width}}  {span:<15}  {c['detail']}")
    lines.append(f"VERDICT: {report['verdict']}")
    lines.append(f"Report: {report_rel}")
    return lines


def _display_path(path: Path, project: Path) -> str:
    """A relative path for messages: relative to the project, else to the working directory, else the name."""
    shown = rel_path(path, project)
    if not shown.startswith(".."):
        return shown
    try:
        cwd_rel = os.path.relpath(str(path.resolve()), os.getcwd())
        return Path(cwd_rel).as_posix() if not os.path.isabs(cwd_rel) else path.name
    except ValueError:
        return path.name


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def parse_band(text: str) -> dict:
    """'38,2.2,4.9' -> {"min_events", "max_frozen", "max_slow"}."""
    parts = [p.strip() for p in text.split(",")]
    try:
        values = [float(p) for p in parts]
    except ValueError:
        values = []
    if len(values) != 3 or any(v < 0 for v in values):
        raise UsageError("--motion-band takes three numbers: EVENTS_PER_MIN,FROZEN_PCT,SLOW_PCT (e.g. 38,2.2,4.9)")
    return {"min_events": values[0], "max_frozen": values[1], "max_slow": values[2]}


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Code gate for a rendered video: streams, sound to the end, total silence and "
                               "dead air, loudness, black and frozen picture, hook motion, subtitle timing "
                               "against the spoken words. "
                               "Exit 0 pass, 1 fail, 2 usage error.")
    parser.add_argument("video", help="the rendered video file (e.g. render/final.mp4)")
    parser.add_argument("--profile", choices=(ANIMATED, LIVE), default=ANIMATED,
                        help="animated (default): motion graphics and edited explainers; live: camera or screen "
                             "recordings, where hook motion only warns and body freezes warn at 8 s")
    parser.add_argument("--music", action="store_true",
                        help="the soundtrack is music only (no narration): dead air below -50 dB warns at "
                             f"{MUSIC_HOLE_WARN_S:g} s and fails at {MUSIC_HOLE_FAIL_S:g} s")
    parser.add_argument("--srt", help="subtitle file to check against the video")
    parser.add_argument("--words", help="word-timed JSON of the narration (Whisper style); needs --srt")
    parser.add_argument("--words-offset", type=float, default=0.0,
                        help="where the narration starts inside the video, in seconds (default 0)")
    parser.add_argument("--hook-seconds", type=float, default=DEFAULT_HOOK_S,
                        help=f"length of the opening that must keep moving (default {DEFAULT_HOOK_S:g})")
    parser.add_argument("--motion-band", default=None,
                        help="EVENTS_PER_MIN,FROZEN_PCT,SLOW_PCT reference band (default "
                             f"{DEFAULT_BAND['min_events']:g},{DEFAULT_BAND['max_frozen']:g},"
                             f"{DEFAULT_BAND['max_slow']:g})")
    parser.add_argument("--fast", action="store_true",
                        help="sample black and frozen frames more coarsely (for previews)")
    parser.add_argument("--out", help="report path (default <project>/qa/render-check.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        video = Path(args.video)
        if not video.is_file():
            raise UsageError(f"video not found: {args.video}")
        srt = Path(args.srt) if args.srt else None
        if srt is not None and not srt.is_file():
            raise UsageError(f"subtitle file not found: {args.srt}")
        words_path = Path(args.words) if args.words else None
        if words_path is not None and srt is None:
            raise UsageError("--words needs --srt: the words are compared with the subtitle cues")
        if words_path is not None and not words_path.is_file():
            raise UsageError(f"words file not found: {args.words}")
        if args.hook_seconds < 0:
            raise UsageError("--hook-seconds must be zero or more")
        band = parse_band(args.motion_band) if args.motion_band else dict(DEFAULT_BAND)
        require_tools()
        project = find_project(video)
        cues = None
        if srt is not None:
            try:
                cues = parse_srt(read_subtitles(srt))
            except OSError as exc:
                raise UsageError(f"cannot read subtitle file: {exc}") from exc
        words = load_words(words_path, args.words_offset) if words_path is not None else None
        measured, decode_error = measure(video, fast=args.fast, hook_seconds=args.hook_seconds, music=args.music)
    except UsageError as exc:
        print(f"render_check: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("render_check: error: ffmpeg timed out", file=sys.stderr)
        return 2

    measured["profile"] = args.profile
    measured["hook_seconds"] = args.hook_seconds
    measured["motion_band"] = band
    inputs = [p for p in (srt, words_path) if p is not None]
    if inputs:
        measured["inputs_checked"] = [rel_path(p, project) for p in inputs]
        measured["stale_inputs"] = stale_inputs(video, inputs, project)
    if srt is not None:
        measured["srt"] = rel_path(srt, project)
        measured["srt_cues"] = len(cues or [])
    if words_path is not None:
        measured["words"] = rel_path(words_path, project)
        measured["words_offset"] = args.words_offset

    checks = evaluate(measured, cues, hook_seconds=args.hook_seconds, profile=args.profile, words=words, band=band)
    if decode_error:
        checks.insert(0, check("decode", FAIL, f"ffmpeg could not decode the whole file: {decode_error[-200:]}"))
    digest = sha256_file(video)
    exemptions, notes = load_exemptions(project, digest)
    apply_exemptions(checks, exemptions)
    checks += notes
    verdict = verdict_of(checks)

    report = {
        "tool": TOOL,
        "version": VERSION,
        "video": rel_path(video, project),
        "sha256": digest,
        "size": video.stat().st_size,
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "measured": measured,
        "checks": checks,
        "exemptions": exemptions,
    }
    report = _finite(report)

    out_path = Path(args.out) if args.out else project / "qa" / "render-check.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"render_check: error: cannot write report: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(report, _display_path(out_path, project))))
    return 1 if verdict == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
