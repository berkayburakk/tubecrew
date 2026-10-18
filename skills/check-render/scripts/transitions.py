#!/usr/bin/env python3
"""transitions.py - find overlays that pop in or out without a transition, and hard screen jumps.

What it does
    Samples the picture at 10 fps as 320x180 gray frames (180x320 for a vertical video), takes the absolute
    difference of every pair of consecutive frames and averages it over a coarse 32x18 grid of 10x10-pixel cells.
    Two kinds of moment are marked:

      overlay pop   at least 4 cells jump by 25 or more (0-255) in a single frame step, the same cells were calm
                    (under 3) for 0.5 s before and stay calm for 0.5 s after, and the frame as a whole stays calm
                    (under 8): a label, box or blur appeared or vanished in one frame, with no fade or move.
      screen jump   the whole frame changes by 14 or more in one step: a hard cut. With --cuts, only jumps within
                    0.15 s of a listed cut are marked (the edit points you made); without it, every jump is.

    It prints the timestamps, writes qa/transitions.json and, with --frames DIR, saves a before | after frame pair
    (PNG) for every mark.

Why
    A label that vanishes at a cut, a privacy box that snaps on and off, a blur that appears in one frame: at full
    speed the eye reads them as a glitch without knowing where, and frame-by-frame review only finds them when
    someone knows where to look. A moving cursor and scrolling text stay under 4 cells, so the marks stay few.

    This is a review aid, not a gate. A hard cut is often a choice; every mark is a place for the render-auditor
    to look, with the frame pair in hand.

How to run
    python transitions.py render/final.mp4 [--cuts 12.5,40,1:02.3 | --cuts cuts.json] [--mask X,Y,W,H]
                          [--frames qa/transition-frames] [--jump 14] [--step 25] [--out PATH] [--json]

    --cuts: the edit points, inline (seconds or m:ss, comma separated) or a file (a JSON list, {"cuts": [...]},
        or numbers separated by commas, spaces or new lines).
    --mask X,Y,W,H: a region in the video's own pixels to ignore, such as a face cam (repeatable).
    --jump / --step: the screen-jump threshold and the overlay cell threshold (mean difference, 0-255).

    The project folder is the video's parent, or the parent of `render/` when the video sits in a folder named
    `render`. The report goes to <project>/qa/transitions.json unless --out is given.

Exit codes
    0  report written (marks are places to look, not failures)
    2  usage or environment error (file missing, ffmpeg/ffprobe not on PATH, file not readable as video)

Requires ffmpeg and ffprobe on PATH. Standard library only.
"""
from __future__ import annotations

import argparse
import array
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL = "transitions.py"
VERSION = "0.3.1"

# ----------------------------------------------------------------------------------------------- definitions
# Calibrated on an edited screen-recording video where a label vanished at a cut and privacy boxes snapped on and
# off: these thresholds caught them, while cursor movement and scrolling text stayed under the cell count.
FPS = 10                       # one step = 0.1 s: a snap shows as one step, a 0.3 s fade spreads over three
ANALYSIS_W, ANALYSIS_H = 320, 180   # swapped for a vertical video, so a cell stays 10x10 pixels
GRID_COLS, GRID_ROWS = 32, 18  # coarse enough to ignore noise, fine enough that a label covers several cells
CELL_JUMP = 25.0               # a cell whose mean difference reaches 25 changed in one step (--step)
CELL_CALM = 3.0                # under 3 a cell is calm: compression noise and a blinking cursor stay below it
CALM_STEPS = 5                 # 0.5 s before and after must be calm, so an animation in progress is not a pop
POP_MIN_CELLS = 4              # fewer cells is a cursor or a few typed letters
FRAME_CALM = 8.0               # above this the whole picture moves (a cut or a camera move), not just an overlay
SCREEN_JUMP = 14.0             # the whole frame changing by 14 or more in one step is a hard jump (--jump)
CUT_TOLERANCE_S = 0.15         # a jump within 0.15 s of a listed cut is that cut
MIN_GAP_S = 0.3                # marks closer than this are one moment
PAIR_BEFORE_S = 0.2            # the before frame of a pair (the change lands between the two)
PAIR_AFTER_S = 0.1             # the after frame of a pair
PAIR_HEIGHT = 360              # frame pairs are scaled to this height, side by side
FFMPEG_TIMEOUT_S = 3600
FFPROBE_TIMEOUT_S = 120

OVERLAY, JUMP = "overlay_pop", "screen_jump"
LABELS = {OVERLAY: "overlay pop", JUMP: "screen jump"}


class UsageError(Exception):
    """Raised for exit-code-2 problems (missing file, missing tool, unreadable media, bad option)."""


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
        return Path(path).name
    return Path(rel).as_posix()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ----------------------------------------------------------------------------------------------- options (pure)

_TIME_RE = re.compile(r"^(?:(\d+):)?(\d+(?:\.\d+)?)$")


def parse_time(text: str) -> float:
    """'62.3', '1:02.3' or '1:02:03' -> seconds."""
    text = text.strip()
    parts = text.split(":")
    if len(parts) == 3:
        hours, rest = parts[0], ":".join(parts[1:])
    else:
        hours, rest = "0", text
    m = _TIME_RE.match(rest)
    if not hours.isdigit() or not m:
        raise UsageError(f"not a time: {text!r} (--cuts takes seconds or m:ss, or the path of an existing file)")
    return int(hours) * 3600 + int(m.group(1) or 0) * 60 + float(m.group(2))


def parse_cuts(value: str) -> list[float]:
    """--cuts value -> sorted cut times. A path to a file (JSON list, {"cuts": [...]} or plain numbers) or an
    inline comma-separated list."""
    path = Path(value)
    text = value
    try:
        is_file = path.is_file()
    except (OSError, ValueError):   # "1:02.3" is not a valid file name on every system
        is_file = False
    if is_file:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise UsageError(f"cannot read cuts file: {exc}") from exc
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            data = data.get("cuts")
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            data = [data]
        if isinstance(data, list):
            try:
                return sorted({round(float(x), 3) for x in data})
            except (TypeError, ValueError) as exc:
                raise UsageError(f"cuts file {path.name}: every cut must be a number of seconds") from exc
        if data is not None:
            raise UsageError(f"cuts file {path.name}: expected a list of seconds or {{\"cuts\": [...]}}")
    items = [t for t in re.split(r"[,\s]+", text) if t]
    if not items:
        raise UsageError("--cuts is empty")
    return sorted({round(parse_time(t), 3) for t in items})


def parse_mask(text: str) -> tuple[int, int, int, int]:
    """'X,Y,W,H' in video pixels -> a tuple of four non-negative integers (width and height above zero)."""
    try:
        values = [int(float(p)) for p in text.split(",")]
    except ValueError:
        values = []
    if len(values) != 4 or min(values) < 0 or values[2] == 0 or values[3] == 0:
        raise UsageError(f"--mask takes X,Y,W,H in video pixels (e.g. 0,0,480,1080), got {text!r}")
    return values[0], values[1], values[2], values[3]


# ----------------------------------------------------------------------------------------------- detection (pure)

def grid_frames(raw: bytes, cells: int = GRID_COLS * GRID_ROWS) -> list[list[float]]:
    """Raw gray16le grid frames back to back -> per-frame lists of cell means on the 0-255 scale."""
    size = cells * 2
    out = []
    for i in range(len(raw) // size):
        values = array.array("H")
        values.frombytes(raw[i * size:(i + 1) * size])
        if sys.byteorder == "big":
            values.byteswap()
        out.append([v / 257.0 for v in values])   # gray8 -> gray16 maps 255 to 65535
    return out


def _region(indices: list[int], cols: int, rows: int) -> list[float]:
    """Bounding box of grid cells as fractions of the frame: [left, top, right, bottom]."""
    xs = [i % cols for i in indices]
    ys = [i // cols for i in indices]
    return [round(min(xs) / cols, 3), round(min(ys) / rows, 3),
            round((max(xs) + 1) / cols, 3), round((max(ys) + 1) / rows, 3)]


def find_marks(diffs: list[list[float]], cuts: list[float] | None = None, jump: float = SCREEN_JUMP,
               step: float = CELL_JUMP, fps: float = FPS, cols: int = GRID_COLS,
               rows: int = GRID_ROWS) -> list[dict]:
    """Per-step cell differences -> marks.

    diffs[i] holds the mean absolute difference of every grid cell between sampled frames i and i+1, so the
    change is first visible at (i + 1) / fps seconds.
    """
    cuts = sorted(cuts or [])
    n = len(diffs)
    overall = [sum(d) / len(d) if d else 0.0 for d in diffs]
    marks: list[dict] = []
    for i in range(n):
        t = round((i + 1) / fps, 3)
        near = [k for k in cuts if abs(k - t) <= CUT_TOLERANCE_S + 1e-9]
        mark = None
        if overall[i] >= jump and (near or not cuts):
            mark = {"time": t, "kind": JUMP, "overall": round(overall[i], 1)}
        elif CALM_STEPS <= i < n - CALM_STEPS and overall[i] < FRAME_CALM:
            popped = []
            for j, value in enumerate(diffs[i]):
                if value < step:
                    continue
                before = max(diffs[k][j] for k in range(i - CALM_STEPS, i))
                after = max(diffs[k][j] for k in range(i + 1, i + 1 + CALM_STEPS))
                if before < CELL_CALM and after < CELL_CALM:
                    popped.append(j)
            if len(popped) >= POP_MIN_CELLS:
                mark = {"time": t, "kind": OVERLAY, "cells": len(popped), "overall": round(overall[i], 1),
                        "region": _region(popped, cols, rows)}
        if mark is None or (marks and t - marks[-1]["time"] < MIN_GAP_S):
            continue
        mark["at_cut"] = bool(near)
        if near:
            mark["cut"] = min(near, key=lambda k: abs(k - t))
        marks.append(mark)
    return marks


def describe(mark: dict) -> str:
    """One line of plain English for a mark."""
    if mark["kind"] == OVERLAY:
        left, top, right, bottom = mark["region"]
        text = (f"{mark['cells']} cells appeared or vanished in one frame (x {left:.0%}-{right:.0%}, "
                f"y {top:.0%}-{bottom:.0%})")
    else:
        text = f"the whole frame changed in one step (difference {mark['overall']:g})"
    if mark.get("at_cut"):
        text += f", at the cut {fmt_time(mark['cut'])}"
    return text


# ----------------------------------------------------------------------------------------------- measuring

def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise UsageError(f"{' and '.join(missing)} not found on PATH; install ffmpeg (https://ffmpeg.org) first")


def _run(cmd: list[str], timeout: float) -> tuple[int, bytes, str]:
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout or b"", (proc.stderr or b"").decode("utf-8", errors="replace")


def probe(video: Path) -> dict:
    """ffprobe -> {"width", "height", "duration"} of the first picture stream."""
    code, out, err = _run(["ffprobe", "-v", "error", "-of", "json", "-show_format", "-show_streams", str(video)],
                          FFPROBE_TIMEOUT_S)
    if code != 0:
        raise UsageError(f"ffprobe cannot read {video.name}: {err.strip()[-300:]}")
    try:
        data = json.loads(out.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise UsageError(f"ffprobe output for {video.name} is not readable: {exc}") from exc
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic"):
            try:
                duration = float(stream.get("duration") or data.get("format", {}).get("duration") or 0.0)
            except ValueError:
                duration = 0.0
            return {"width": int(stream.get("width") or 0), "height": int(stream.get("height") or 0),
                    "duration": duration, "index": stream.get("index")}
    raise UsageError(f"{video.name} has no video stream")


def analysis_size(width: int, height: int) -> tuple[int, int, int, int]:
    """(analysis width, height, grid columns, rows): a vertical video keeps 10x10-pixel cells upright."""
    if height > width:
        return ANALYSIS_H, ANALYSIS_W, GRID_ROWS, GRID_COLS
    return ANALYSIS_W, ANALYSIS_H, GRID_COLS, GRID_ROWS


def build_grid_cmd(video: Path, width: int, height: int, masks: list[tuple[int, int, int, int]] | None = None,
                   index: int | None = None) -> list[str]:
    """ffmpeg command that prints one gray16le grid frame per step: the absolute difference of consecutive
    10 fps frames (tblend), averaged per cell (area scaling). Masked regions are painted black first."""
    aw, ah, cols, rows = analysis_size(width, height)
    chain = [f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black:t=fill" for x, y, w, h in masks or []]
    chain += [f"fps={FPS}", f"scale={aw}:{ah}", "format=gray", "tblend=all_mode=difference", "format=gray16le",
              f"scale={cols}:{rows}:flags=area", "format=gray16le"]
    vin = f"0:{index}" if index is not None else "0:v:0"
    return ["ffmpeg", "-hide_banner", "-nostats", "-v", "error", "-i", str(video), "-map", vin, "-an", "-sn",
            "-vf", ",".join(chain), "-f", "rawvideo", "-pix_fmt", "gray16le", "pipe:1"]


def measure(video: Path, masks: list[tuple[int, int, int, int]] | None = None) -> tuple[dict, list[list[float]],
                                                                                       str | None]:
    """Probe the video and read its grid differences. Returns (info, diffs, decode warning or None)."""
    info = probe(video)
    aw, ah, cols, rows = analysis_size(info["width"], info["height"])
    info.update({"analysis": f"{aw}x{ah}", "cols": cols, "rows": rows})
    code, raw, err = _run(build_grid_cmd(video, info["width"], info["height"], masks, info.get("index")),
                          FFMPEG_TIMEOUT_S)
    diffs = grid_frames(raw, cols * rows)
    warning = None
    if code != 0:
        last = err.strip().splitlines()[-1] if err.strip() else f"ffmpeg exited with {code}"
        if not diffs:
            raise UsageError(f"ffmpeg cannot decode {video.name}: {last[-300:]}")
        warning = f"ffmpeg stopped early: {last[-200:]}"
    if not info["duration"]:
        info["duration"] = (len(diffs) + 1) / FPS
    return info, diffs, warning


def save_pair(video: Path, t: float, duration: float, out: Path) -> bool:
    """Save the frame just before and just after `t` side by side as a PNG. True when the file was written."""
    before = max(0.0, t - PAIR_BEFORE_S)
    after = min(t + PAIR_AFTER_S, max(0.0, duration - 0.05))
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-ss", f"{before:.3f}", "-i", str(video),
           "-ss", f"{after:.3f}", "-i", str(video), "-filter_complex",
           f"[0:v:0]scale=-2:{PAIR_HEIGHT}[a];[1:v:0]scale=-2:{PAIR_HEIGHT}[b];[a][b]hstack=inputs=2",
           "-frames:v", "1", "-update", "1", str(out)]
    try:
        code, _, _ = _run(cmd, FFPROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False
    return code == 0 and out.is_file()


# ----------------------------------------------------------------------------------------------- output

def human_lines(report: dict, report_rel: str) -> list[str]:
    marks = report["marks"]
    lines = [f"transitions: {report['video']}  {fmt_time(report['duration_s'])}  {report['fps']} fps, "
             f"{report['grid']} grid"]
    if report.get("cuts") is not None:
        lines[0] += f", {len(report['cuts'])} cuts"
    for mark in marks:
        lines.append(f"  {fmt_time(mark['time'])}  {LABELS[mark['kind']]:<12} {describe(mark)}")
    counts = report["counts"]
    lines.append(f"{len(marks)} mark(s): {counts[OVERLAY]} overlay pop(s), {counts[JUMP]} screen jump(s). A mark is a "
                 "place to look, not a failure.")
    if report.get("decode_warning"):
        lines.append(f"Note: {report['decode_warning']}")
    if report.get("frames_dir"):
        saved = sum(1 for m in marks if m.get("frames"))
        lines.append(f"Frame pairs (before | after): {report['frames_dir']} ({saved} saved)")
    lines.append(f"Report: {report_rel}")
    return lines


def _display_path(path: Path, project: Path) -> str:
    """A relative path for messages: relative to the project when inside it, else to the working directory."""
    try:
        inside = os.path.commonpath([str(path.resolve()), str(project.resolve())]) == str(project.resolve())
    except ValueError:  # different drive on Windows
        inside = False
    if inside:
        return rel_path(path, project)
    try:
        cwd_rel = os.path.relpath(str(path.resolve()), os.getcwd())
    except ValueError:
        return path.name
    if os.path.isabs(cwd_rel) or cwd_rel.startswith(".."):
        return path.name
    return Path(cwd_rel).as_posix()


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Review aid: mark overlays that pop in or out in one frame and hard screen jumps, "
                               "with optional before/after frame pairs. Exit 0 report, 2 usage error.")
    parser.add_argument("video", help="the rendered video file (e.g. render/final.mp4)")
    parser.add_argument("--cuts", help="edit points: inline seconds or m:ss (comma separated) or a file")
    parser.add_argument("--mask", action="append", default=[], metavar="X,Y,W,H",
                        help="region to ignore in video pixels, e.g. a face cam (repeatable)")
    parser.add_argument("--frames", metavar="DIR", help="save a before | after PNG pair for every mark here")
    parser.add_argument("--jump", type=float, default=SCREEN_JUMP,
                        help=f"screen-jump threshold, mean difference 0-255 (default {SCREEN_JUMP:g})")
    parser.add_argument("--step", type=float, default=CELL_JUMP,
                        help=f"overlay cell threshold, mean difference 0-255 (default {CELL_JUMP:g})")
    parser.add_argument("--out", help="report path (default <project>/qa/transitions.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        video = Path(args.video)
        if not video.is_file():
            raise UsageError(f"video not found: {args.video}")
        if args.jump <= 0 or args.step <= 0:
            raise UsageError("--jump and --step must be above zero")
        cuts = parse_cuts(args.cuts) if args.cuts else None
        masks = [parse_mask(m) for m in args.mask]
        require_tools()
        project = find_project(video)
        info, diffs, warning = measure(video, masks)
    except UsageError as exc:
        print(f"transitions: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("transitions: error: ffmpeg timed out", file=sys.stderr)
        return 2

    marks = find_marks(diffs, cuts, jump=args.jump, step=args.step, cols=info["cols"], rows=info["rows"])
    frames_dir = None
    if args.frames:
        frames_dir = Path(args.frames)
        try:
            frames_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"transitions: error: cannot create {args.frames}: {exc}", file=sys.stderr)
            return 2
        for mark in marks:
            png = frames_dir / f"pair-{mark['time']:07.2f}.png"
            if save_pair(video, mark["time"], info["duration"], png):
                mark["frames"] = rel_path(png, project)

    report = {
        "tool": TOOL,
        "version": VERSION,
        "video": rel_path(video, project),
        "sha256": sha256_file(video),
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": round(info["duration"], 2),
        "fps": FPS,
        "analysis_size": info["analysis"],
        "grid": f"{info['cols']}x{info['rows']}",
        "thresholds": {"screen_jump": args.jump, "cell_jump": args.step, "cell_calm": CELL_CALM,
                       "frame_calm": FRAME_CALM, "min_cells": POP_MIN_CELLS, "calm_window_s": CALM_STEPS / FPS,
                       "cut_tolerance_s": CUT_TOLERANCE_S},
        "cuts": cuts,
        "masks": [list(m) for m in masks],
        "counts": {OVERLAY: sum(1 for m in marks if m["kind"] == OVERLAY),
                   JUMP: sum(1 for m in marks if m["kind"] == JUMP)},
        "marks": marks,
    }
    if warning:
        report["decode_warning"] = warning
    if frames_dir is not None:
        report["frames_dir"] = _display_path(frames_dir, project)

    out_path = Path(args.out) if args.out else project / "qa" / "transitions.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"transitions: error: cannot write report: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(report, _display_path(out_path, project))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
