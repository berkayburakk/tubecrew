#!/usr/bin/env python3
"""normalize_audio.py - the final mix step: bring a video's (or an audio file's) loudness to -14 LUFS.

What it does
    Two-pass ffmpeg loudnorm. Pass one measures the integrated loudness, true peak and loudness range; pass two
    applies one linear gain from those numbers (target -14 LUFS integrated, true peak 0.5 dB under the -1 dBTP
    ceiling, loudness range 11 LU). The video stream is copied, not re-encoded. Then the new file is measured
    again and gated: it fails when the integrated loudness is more than 0.5 LU from the target or the true peak
    is above -1 dBTP. It writes a new file and never touches the input.

Why
    YouTube plays everything at about -14 LUFS: a quiet mix sounds weak next to other videos, a hot one is turned
    down and its peaks may clip after lossy encoding. One trap is silent: loudnorm asked for linear=true falls back
    to dynamic mode (compression that changes the mix) when a linear gain would push the true peak over the
    target or the loudness range is too wide. The second pass reports which mode it used; this script reads that
    report and says so, because the fix is in the mix (tame the peaks, then normalize again).

How to run
    python normalize_audio.py render/raw.mp4 [--out render/final.mp4] [--target-i -14] [--max-tp -1]
                              [--report PATH] [--json]

    The default output is <stem>-normalized<ext> next to the input. Video containers (.mp4 .mov .m4v .mkv
    .webm) keep the first video stream (copied) and get AAC audio at 320 kb/s (Opus in .webm); audio files keep
    their kind (.wav 24-bit PCM, .flac, .mp3, .m4a, .ogg/.opus). Subtitle and extra audio tracks are not carried
    over. The report goes to <project>/qa/normalize-audio.json (the project is the output's folder, or the
    parent of `render/`) unless --report is given.

Exit codes
    0  the output meets the targets (a dynamic-mode fallback is reported as a warning)
    1  the output misses a target, or the input audio is silent
    2  usage or environment error (file missing, no audio stream, output would overwrite the input, ffmpeg fails)

Requires ffmpeg and ffprobe on PATH. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

TOOL = "normalize_audio.py"
VERSION = "0.3.0"

TARGET_I = -14.0          # YouTube's playback reference loudness (LUFS integrated)
MAX_TP = -1.0             # true-peak ceiling: above -1 dBTP lossy encoding can clip
TP_MARGIN = 0.5           # the filter aims this far under the ceiling, since AAC/Opus encoding adds a little overshoot
TARGET_LRA = 11.0         # loudness range target; narrator plus music mixes sit well under it
I_TOLERANCE = 0.5         # the output may miss the target by this much (LU) before the gate fails
SILENT_LUFS = -70.0       # quieter than this is silence: there is nothing to normalize
FFMPEG_TIMEOUT_S = 3600
FFPROBE_TIMEOUT_S = 120

VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
AUDIO_CODECS = {          # output extension -> audio encoder arguments
    ".mp4": ["-c:a", "aac", "-b:a", "320k"], ".m4v": ["-c:a", "aac", "-b:a", "320k"],
    ".mov": ["-c:a", "aac", "-b:a", "320k"], ".mkv": ["-c:a", "aac", "-b:a", "320k"],
    ".m4a": ["-c:a", "aac", "-b:a", "320k"], ".webm": ["-c:a", "libopus", "-b:a", "192k"],
    ".ogg": ["-c:a", "libopus", "-b:a", "192k"], ".opus": ["-c:a", "libopus", "-b:a", "192k"],
    ".mp3": ["-c:a", "libmp3lame", "-b:a", "320k"], ".wav": ["-c:a", "pcm_s24le"], ".flac": ["-c:a", "flac"],
}
FASTSTART_EXTS = {".mp4", ".m4v", ".mov", ".m4a"}   # index at the front: uploads and players start sooner


class UsageError(Exception):
    """Raised for exit-code-2 problems."""


# ----------------------------------------------------------------------------------------------- helpers

def find_project(path: Path) -> Path:
    """The project folder: parent of `render/` if the file sits in a folder named render, else its folder."""
    parent = path.resolve().parent
    return parent.parent if parent.name.lower() == "render" else parent


def rel_path(path: Path, base: Path) -> str:
    """Path relative to base in POSIX form; outside the base only the file name is kept."""
    try:
        rel = os.path.relpath(str(Path(path).resolve()), str(Path(base).resolve()))
    except ValueError:
        return Path(path).name
    if os.path.isabs(rel) or rel == ".." or rel.startswith(".." + os.sep) or rel.startswith("../"):
        return Path(path).name
    return Path(rel).as_posix()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(cmd: list[str], timeout: float = FFMPEG_TIMEOUT_S) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("-inf")


def _finite(value):
    """Replace non-finite floats (silence measures -inf) with None so the report stays valid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


# ----------------------------------------------------------------------------------------------- pure parts

def parse_loudnorm_json(stderr: str) -> dict | None:
    """loudnorm's print_format=json block (the one holding "input_i") from an ffmpeg log, or None."""
    pos = stderr.rfind('"input_i"')
    if pos < 0:
        return None
    start, end = stderr.rfind("{", 0, pos), stderr.find("}", pos)
    if start < 0 or end < start:
        return None
    try:
        return json.loads(stderr[start:end + 1])
    except ValueError:
        return None


def loudnorm_filter(target_i: float, target_tp: float, lra: float, measured: dict | None = None) -> str:
    """The loudnorm filter string: measuring (no `measured`) or applying a linear gain from pass one."""
    base = f"loudnorm=I={target_i:g}:TP={target_tp:g}:LRA={lra:g}"
    if measured:
        base += (f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
                 f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
                 f":offset={measured['target_offset']}:linear=true")
    return base + ":print_format=json"


def default_output(path: Path) -> Path:
    return path.with_name(f"{path.stem}-normalized{path.suffix}")


def build_apply_cmd(src: Path, dst: Path, audio_index: int | None, video_index: int | None, filt: str) -> list[str]:
    """Pass two: copy the video stream (when the output can hold one), re-encode the audio through loudnorm."""
    ext = dst.suffix.lower()
    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostats", "-i", str(src)]
    if video_index is not None and ext in VIDEO_EXTS:
        cmd += ["-map", f"0:{video_index}", "-c:v", "copy"]
    else:
        cmd += ["-vn"]
    cmd += ["-map", f"0:{audio_index}" if audio_index is not None else "0:a:0", "-af", filt]
    cmd += AUDIO_CODECS[ext] + ["-ar", "48000", "-map_metadata", "0"]
    if ext in FASTSTART_EXTS:
        cmd += ["-movflags", "+faststart"]
    return cmd + [str(dst)]


def gate(after: dict, target_i: float, max_tp: float) -> list[str]:
    """Problems of the normalized file (empty list = pass)."""
    problems = []
    i, tp = after["i"], after["tp"]
    if not math.isfinite(i) or abs(i - target_i) > I_TOLERANCE:
        problems.append(f"integrated {i:.1f} LUFS, target {target_i:g} +/- {I_TOLERANCE:g}")
    if math.isfinite(tp) and tp > max_tp:
        problems.append(f"true peak {tp:.1f} dBTP, above the {max_tp:g} dBTP ceiling")
    return problems


def mode_warning(mode: str | None) -> str | None:
    """A warning when loudnorm did not apply a single linear gain."""
    if mode == "linear":
        return None
    if mode == "dynamic":
        return ("loudnorm fell back to dynamic mode although linear gain was asked for, so the mix was "
                "compressed. It does that when a linear gain would push the true peak over the target, when the "
                "loudness range is over the target, or when the measured range is zero (a steady test tone). "
                "Lower the loudest peaks in the mix (a limiter on the music or effects) and normalize again.")
    return "loudnorm did not report which mode it used; listen to the result"


def _levels(block: dict) -> dict:
    return {"i": _number(block.get("input_i")), "tp": _number(block.get("input_tp")),
            "lra": _number(block.get("input_lra"))}


# ----------------------------------------------------------------------------------------------- ffmpeg

def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise UsageError(f"{' and '.join(missing)} not found on PATH; install ffmpeg (https://ffmpeg.org) first")


def probe_streams(path: Path) -> tuple[int | None, int | None]:
    """(first audio stream index, first real video stream index); cover art is not a video stream."""
    code, out, err = _run(["ffprobe", "-v", "error", "-of", "json", "-show_streams", str(path)], FFPROBE_TIMEOUT_S)
    if code != 0:
        raise UsageError(f"ffprobe cannot read {path.name}: {err.strip()[-300:]}")
    try:
        streams = json.loads(out).get("streams", [])
    except ValueError as exc:
        raise UsageError(f"ffprobe output for {path.name} is not readable: {exc}") from exc
    audio = next((s.get("index") for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s.get("index") for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    return audio, video


def measure(path: Path, target_i: float, target_tp: float, lra: float, audio_index: int | None = None) -> dict:
    """One loudnorm measuring pass -> its JSON block (values are strings, as loudnorm prints them)."""
    amap = f"0:{audio_index}" if audio_index is not None else "0:a:0"
    code, _, err = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", amap,
                         "-af", loudnorm_filter(target_i, target_tp, lra), "-f", "null", "-"])
    block = parse_loudnorm_json(err)
    if code != 0 or block is None:
        raise UsageError(f"ffmpeg could not measure {path.name}: {err.strip()[-300:]}")
    return block


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _fmt_levels(levels: dict) -> str:
    return f"{levels['i']:.1f} LUFS, true peak {levels['tp']:.1f} dBTP, LRA {levels['lra']:.1f} LU"


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Two-pass loudness normalization to -14 LUFS with a -1 dBTP ceiling, video copied. "
                               "Exit 0 pass, 1 target missed, 2 usage error.")
    parser.add_argument("input", help="video or audio file with the final mix")
    parser.add_argument("--out", help="output file (default <stem>-normalized<ext>; never the input)")
    parser.add_argument("--target-i", type=float, default=TARGET_I,
                        help=f"integrated loudness target in LUFS (default {TARGET_I:g})")
    parser.add_argument("--max-tp", type=float, default=MAX_TP,
                        help=f"true-peak ceiling in dBTP (default {MAX_TP:g}; the filter aims {TP_MARGIN:g} dB lower)")
    parser.add_argument("--report", help="report path (default <project>/qa/normalize-audio.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    src = Path(args.input)
    dst = Path(args.out) if args.out else default_output(src)
    tmp = dst.with_name(f".{dst.stem}.partial{dst.suffix}")
    try:
        if not src.is_file():
            raise UsageError(f"input not found: {args.input}")
        if dst.suffix.lower() not in AUDIO_CODECS:
            raise UsageError(f"cannot write {dst.suffix or 'a file without extension'}; use one of "
                             f"{', '.join(sorted(AUDIO_CODECS))}")
        if dst.resolve() == src.resolve() or (dst.exists() and os.path.samefile(dst, src)):
            raise UsageError("the output would overwrite the input; give another --out")
        if not -70.0 < args.target_i < 0.0 or args.max_tp > 0.0:
            raise UsageError("--target-i must be between -70 and 0 LUFS and --max-tp at most 0 dBTP")
        require_tools()
        audio_index, video_index = probe_streams(src)
        if audio_index is None:
            raise UsageError(f"{src.name} has no audio stream to normalize")
        target_tp = args.max_tp - TP_MARGIN
        first = measure(src, args.target_i, target_tp, TARGET_LRA, audio_index)
        before = _levels(first)
        if before["i"] < SILENT_LUFS:
            print(f"normalize_audio: FAIL: the audio of {src.name} is silent ({before['i']:.1f} LUFS); nothing "
                  "to normalize", file=sys.stderr)
            return 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        filt = loudnorm_filter(args.target_i, target_tp, TARGET_LRA, first)
        code, _, err = _run(build_apply_cmd(src, tmp, audio_index, video_index, filt))
        if code != 0:
            raise UsageError(f"ffmpeg could not write the normalized file: {err.strip()[-300:]}")
        applied = parse_loudnorm_json(err) or {}
        os.replace(tmp, dst)
        after = _levels(measure(dst, args.target_i, target_tp, TARGET_LRA))
    except UsageError as exc:
        print(f"normalize_audio: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("normalize_audio: error: ffmpeg timed out", file=sys.stderr)
        return 2
    finally:
        if tmp.exists():
            tmp.unlink()

    mode = applied.get("normalization_type")
    problems = gate(after, args.target_i, args.max_tp)
    warning = mode_warning(mode)
    verdict = "FAIL" if problems else "PASS"
    project = find_project(dst)
    video_state = "none"
    if video_index is not None:
        video_state = "copied" if dst.suffix.lower() in VIDEO_EXTS else "dropped"
    report = {
        "tool": TOOL, "version": VERSION,
        "input": rel_path(src, project), "input_sha256": sha256_file(src),
        "output": rel_path(dst, project), "output_sha256": sha256_file(dst),
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "target": {"i": args.target_i, "tp_ceiling": args.max_tp, "tp_filter": target_tp, "lra": TARGET_LRA,
                   "i_tolerance": I_TOLERANCE},
        "before": before, "after": after, "normalization": mode, "video": video_state,
        "verdict": verdict, "problems": problems, "warnings": [warning] if warning else [],
    }
    report_path = Path(args.report) if args.report else project / "qa" / "normalize-audio.json"
    clean = _finite(report)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"normalize_audio: error: cannot write report: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(clean, indent=2, ensure_ascii=False))
    else:
        lines = [f"normalize_audio: {report['input']} -> {report['output']}",
                 f"Before: {_fmt_levels(before)}",
                 f"After:  {_fmt_levels(after)}  ({mode or 'unknown'} mode)",
                 f"Video:  {'copied without re-encoding' if video_state == 'copied' else video_state}"]
        lines += [f"FAIL  {p}" for p in problems]
        if warning:
            lines.append(f"WARN  {warning}")
        lines += [f"VERDICT: {verdict}", f"Report: {rel_path(report_path, project)}"]
        print("\n".join(lines))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
