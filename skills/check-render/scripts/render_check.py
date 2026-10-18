#!/usr/bin/env python3
"""render_check.py - the code gate for a rendered video.

What it does
    Measures the defects of a finished render that code can measure, in one ffmpeg decode pass:
    missing streams, audio that stops before the picture, silent tails and gaps, integrated loudness and true
    peak, black stretches, a frozen hook, and (with --srt) subtitle cues that run past the end, overlap or are
    too long to read. It writes a JSON report bound to the video's SHA-256 so an upload step can refuse a
    verdict that belongs to a different file.

Why
    These defects repeat from video to video and are cheap to measure but easy to miss by eye: an end screen
    without sound, a 2-second black gap from a missing clip, a still opening frame that loses the viewer in the
    first seconds. A reviewer's time is better spent on frames and meaning; this script handles the rest.

How to run
    python render_check.py render/final.mp4 [--srt voice/captions.srt] [--hook-seconds 45] [--fast]
                           [--out PATH] [--json]

    The project folder is the video's parent, or the parent of `render/` when the video sits in a folder
    named `render`. The report goes to <project>/qa/render-check.json unless --out is given. Paths inside the
    report are relative to the project folder.

    --fast samples fewer frames at a smaller size (for iterations); run the full pass once on the final file.

Exit codes
    0  verdict PASS (warnings may be listed)
    1  verdict FAIL: at least one check failed, fix the render
    2  usage or environment error (file missing, ffmpeg/ffprobe not on PATH, file not readable as media)

Requires ffmpeg and ffprobe on PATH. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL = "render_check.py"
VERSION = "0.1.0"

# ----------------------------------------------------------------------------------------------- thresholds
AUDIO_SHORT_MAX_S = 0.5        # audio may end this much before the picture (AAC frame padding); more = mute end screen
TAIL_EPS_S = 0.25              # a silence ending this close to the end of the audio is the tail of the video
TRAILING_SILENCE_FAIL_S = 3.0  # a silent last 3 s sounds like a broken upload and kills the end screen
SILENCE_GAP_WARN_S = 2.0       # dead air of 2 s in the body feels like a mistake to the viewer
SILENCE_NOISE_DB = -50         # below -50 dB is silence for speech and music mixes (natural fades stay above it)
SILENCE_MIN_S = 1.5            # silencedetect minimum; shorter pauses are normal speech rhythm

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
BODY_FREEZE_WARN_S = 8.0       # long holds in the body are sometimes intended (a chart to read), so only warn
END_SCREEN_S = 20.0            # end screens (last 5-20 s) are often static by design
DEFAULT_HOOK_S = 45.0          # the opening that decides retention

SUB_END_TOLERANCE_S = 0.5      # a cue may outlast the picture by this much
SUB_MAX_LINE_CHARS = 42        # common broadcast/streaming limit for one subtitle line
SUB_MAX_LINES = 2              # more than two lines covers the picture

FULL_SAMPLE = (5, 320)         # (fps, width) for the decode pass
FAST_SAMPLE = (2, 160)
FFMPEG_TIMEOUT_S = 3600
FFPROBE_TIMEOUT_S = 120

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


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
                          timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


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
            "audio_duration": None, "width": None, "height": None, "fps": None}
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info["has_video"]:
            if (stream.get("disposition") or {}).get("attached_pic"):
                continue  # cover art is not a picture track
            info["has_video"] = True
            info["width"], info["height"] = stream.get("width"), stream.get("height")
            fps = parse_rate(stream.get("avg_frame_rate")) or parse_rate(stream.get("r_frame_rate"))
            info["fps"] = round(fps, 3) if fps else None
            info["video_duration"] = _stream_duration(stream) or duration
        elif kind == "audio" and not info["has_audio"]:
            info["has_audio"] = True
            info["audio_duration"] = _stream_duration(stream) or duration
    return info


_BLACK_RE = re.compile(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*(-?[\d.]+)")
_FREEZE_RE = re.compile(r"freezedetect\.freeze_(start|end):\s*(-?[\d.]+)")
_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?[\d.]+)")


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


def parse_silencedetect(stderr: str, duration: float | None = None) -> list[list[float]]:
    """silencedetect lines -> [[start, end], ...]; a silence still open at the end runs to `duration`."""
    return _pair_events([(k, float(v)) for k, v in _SILENCE_RE.findall(stderr)], duration)


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


def read_subtitles(path: Path) -> str:
    """Subtitle file -> text: UTF-8 (with or without BOM) or UTF-16 with a BOM."""
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


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

    trailing, gaps = [], []
    for start, end in m.get("silences") or []:
        length = end - start
        if end >= audio_end - TAIL_EPS_S:
            if length >= TRAILING_SILENCE_FAIL_S:
                trailing.append(check("trailing_silence", FAIL,
                                      f"the last {length:.1f} s are silent (limit {TRAILING_SILENCE_FAIL_S:g} s)",
                                      start, end))
            elif length >= SILENCE_GAP_WARN_S:
                trailing.append(check("trailing_silence", WARN, f"silent tail of {length:.1f} s", start, end))
        elif length >= SILENCE_GAP_WARN_S:
            gaps.append(check("silence_gap", WARN, f"{length:.1f} s of silence in the body", start, end))
    out += trailing or [check("trailing_silence", PASS, "sound continues to the end")]
    out += gaps or [check("silence_gap", PASS, f"no silent gap of {SILENCE_GAP_WARN_S:g} s or more")]

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


def _check_freeze(m: dict, hook_seconds: float) -> list[dict]:
    if not m.get("has_video"):
        return []
    duration = m.get("video_duration") or m.get("duration") or 0.0
    hook_end = min(max(hook_seconds, 0.0), duration)
    end_screen = max(hook_end, duration - END_SCREEN_S)
    out = []
    for start, end in m.get("freezes") or []:
        in_hook = max(0.0, min(end, hook_end) - max(start, 0.0))
        in_body = max(0.0, min(end, end_screen) - max(start, hook_end))
        in_end = max(0.0, min(end, duration) - max(start, end_screen))
        length = end - start
        if in_hook >= HOOK_FREEZE_FAIL_S:
            out.append(check("freeze", FAIL, f"picture frozen for {in_hook:.1f} s inside the first {hook_end:g} s: "
                                             "the hook must move", start, min(end, hook_end)))
        elif in_body >= BODY_FREEZE_WARN_S:
            out.append(check("freeze", WARN, f"picture frozen for {in_body:.1f} s in the body", start, end))
        elif in_end > 0:
            out.append(check("freeze", INFO, f"static picture for {length:.1f} s in the last {END_SCREEN_S:g} s "
                                             "(end screens are often static)", start, end))
    if not any(c["status"] in (FAIL, WARN) for c in out):
        out.insert(0, check("freeze", PASS, f"no frozen stretch of {HOOK_FREEZE_FAIL_S:g} s in the hook or "
                                            f"{BODY_FREEZE_WARN_S:g} s in the body"))
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


def evaluate(measured: dict, cues: list[dict] | None = None, hook_seconds: float = DEFAULT_HOOK_S) -> list[dict]:
    """Measurements (and optional subtitle cues) -> list of checks. Pure: no files, no ffmpeg.

    `measured` keys: duration, video_duration, audio_duration, has_video, has_audio, width, height, fps,
    lufs, true_peak, black, freezes, silences (intervals as [start, end] seconds).
    """
    checks = _check_streams(measured)
    checks += _check_audio(measured)
    checks += _check_black(measured)
    checks += _check_freeze(measured, hook_seconds)
    if cues is not None:
        checks += _check_subtitles(measured, cues)
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


def build_decode_cmd(video: Path, has_video: bool, has_audio: bool, fast: bool = False) -> list[str]:
    fps, width = FAST_SAMPLE if fast else FULL_SAMPLE
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video)]
    if has_video:
        cmd += ["-vf", f"fps={fps},scale={width}:-2,"
                       f"blackdetect=d={BLACK_MIN_S}:pix_th={BLACK_PIX_TH:.2f},"
                       f"freezedetect=n={FREEZE_NOISE}:d={FREEZE_MIN_S:g}"]
    else:
        cmd += ["-vn"]
    if has_audio:
        cmd += ["-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_S},"
                       "ebur128=peak=true:framelog=quiet"]
    else:
        cmd += ["-an"]
    return cmd + ["-f", "null", "-"]


def measure(video: Path, fast: bool = False) -> tuple[dict, str | None]:
    """Run ffprobe and one ffmpeg decode pass. Returns (measured, decode_error or None)."""
    info = probe(video)
    measured = dict(info)
    measured.update({"black": [], "freezes": [], "silences": [], "lufs": None, "true_peak": None, "lra": None,
                     "fast": fast})
    if not info["has_video"] and not info["has_audio"]:
        return measured, None
    code, _, err = _run(build_decode_cmd(video, info["has_video"], info["has_audio"], fast), FFMPEG_TIMEOUT_S)
    video_end = info["video_duration"] or info["duration"]
    audio_end = info["audio_duration"] or info["duration"]
    measured["black"] = parse_blackdetect(err)
    measured["freezes"] = parse_freezedetect(err, video_end)
    measured["silences"] = parse_silencedetect(err, audio_end)
    loud = parse_loudness(err) if info["has_audio"] else None
    if loud:
        measured.update(loud)
    decode_error = None
    if code != 0:
        decode_error = err.strip().splitlines()[-1] if err.strip() else f"ffmpeg exited with {code}"
    return measured, decode_error


# ----------------------------------------------------------------------------------------------- output

def human_lines(report: dict, report_rel: str) -> list[str]:
    m = report["measured"]
    head = f"render_check: {report['video']}  {fmt_time(m.get('duration'))}"
    if m.get("has_video"):
        head += f"  {m.get('width')}x{m.get('height')}"
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


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Code gate for a rendered video: streams, sound to the end, loudness, black and "
                               "frozen picture, subtitle timing. Exit 0 pass, 1 fail, 2 usage error.")
    parser.add_argument("video", help="the rendered video file (e.g. render/final.mp4)")
    parser.add_argument("--srt", help="subtitle file to check against the video")
    parser.add_argument("--hook-seconds", type=float, default=DEFAULT_HOOK_S,
                        help=f"length of the opening that must keep moving (default {DEFAULT_HOOK_S:g})")
    parser.add_argument("--fast", action="store_true", help="sample fewer, smaller frames (for iterations)")
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
        if args.hook_seconds < 0:
            raise UsageError("--hook-seconds must be zero or more")
        require_tools()
        project = find_project(video)
        cues = None
        if srt is not None:
            try:
                cues = parse_srt(read_subtitles(srt))
            except OSError as exc:
                raise UsageError(f"cannot read subtitle file: {exc}") from exc
        measured, decode_error = measure(video, fast=args.fast)
    except UsageError as exc:
        print(f"render_check: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("render_check: error: ffmpeg timed out", file=sys.stderr)
        return 2

    checks = evaluate(measured, cues, hook_seconds=args.hook_seconds)
    if decode_error:
        checks.insert(0, check("decode", FAIL, f"ffmpeg could not decode the whole file: {decode_error[-200:]}"))
    verdict = verdict_of(checks)
    measured["hook_seconds"] = args.hook_seconds
    if srt is not None:
        measured["srt"] = rel_path(srt, project)
        measured["srt_cues"] = len(cues or [])

    report = {
        "tool": TOOL,
        "version": VERSION,
        "video": rel_path(video, project),
        "sha256": sha256_file(video),
        "size": video.stat().st_size,
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict,
        "measured": measured,
        "checks": checks,
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
