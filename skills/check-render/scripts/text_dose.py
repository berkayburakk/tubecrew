#!/usr/bin/env python3
"""text_dose.py - how much of a video is a wall of text? An OCR gate for on-screen text cards.

What it does
    Takes a frame every 2 seconds with ffmpeg, finds text boxes with a local OCR engine (no API) and marks a frame
    as a "text card" when large text covers a real part of it: boxes at least 4.5% of the frame height tall,
    outside the bottom subtitle band, together covering 4% or more of the frame. Small lines of a document or a
    screen recording (evidence), chart labels and burned-in subtitles do not count; full-screen kinetic type, word
    cards and giant numbers do. It writes qa/text-dose.json with the verdict, the worst stretches and the OCR boxes.

Why
    Viewers come to watch, not to read. A video that is text cards most of the time feels like a slideshow, and
    the fix (a picture instead of a word) is a storyboard decision that is cheap before the render and expensive
    after it. Reference explainers that hold attention well keep text cards near 18% of the runtime.

How to run
    pip install rapidocr-onnxruntime        (optional dependency, only this script needs it)
    python text_dose.py render/final.mp4 [--every 2] [--start 0] [--end 0] [--limit card_ratio=0.25]
                        [--out PATH] [--json]
    python text_dose.py --from-json qa/text-dose.json [--limit ...]   (re-score saved OCR boxes, no OCR)

    Gates (change one with --limit NAME=VALUE for a text-heavy genre such as a coding tutorial):
      card_ratio       share of sampled frames that are text cards         <= 0.20
      worst_30s        text-card share of the worst 30 s slice             <= 0.60
      longest_run_s    longest unbroken run of text cards, in seconds      <= 18
      entries_per_min  text cards appearing per minute                     <= 3.5

    The report goes to <project>/qa/text-dose.json (the project is the video's folder, or the parent of
    `render/`) unless --out is given. Meant for long-form videos; run it on a low-resolution preview before the
    full render, and on the final file.

Exit codes
    0  every gate passes
    1  a gate failed: replace text cards with pictures
    2  usage or environment error (file missing, ffmpeg not on PATH, rapidocr-onnxruntime not installed)
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = "text_dose.py"
VERSION = "0.3.0"

# ----------------------------------------------------------------------------------------------- definitions
# Calibrated on reference explainer videos (text cards ~18% of the runtime, worst 30 s slice 60%, about 3 card
# entries a minute, longest run 14-18 s) against a rejected draft that was text most of the time (58%).
LARGE_TEXT = 0.045        # a box at least this share of the frame height tall is large text
CARD_AREA = 0.04          # large boxes covering this share of the frame make it a text card
MIN_CONFIDENCE = 0.6      # OCR guesses below this confidence are noise
MIN_CHARS = 2             # single characters are usually icons or arrows
OCR_WIDTH = 960           # frames are scaled to this width before OCR (small text stays readable, OCR stays fast)
SUBTITLE_BAND = 0.80      # a box whose centre sits below 80% of the height ...
SUBTITLE_MAX_H = 0.07     # ... and is shorter than 7% of it is a burned-in subtitle, not a text card
SLICE_S = 30.0            # the slice length for the worst-stretch gate
DEFAULT_EVERY_S = 2.0     # one sample every 2 s: enough to see 2.5 s cards, cheap enough for long videos
CARD_TEXT_KEPT = 4        # text snippets kept per card frame in the report
FFMPEG_TIMEOUT_S = 3600

LIMITS = {"card_ratio": 0.20, "worst_30s": 0.60, "longest_run_s": 18.0, "entries_per_min": 3.5}
LIMIT_UNITS = {"card_ratio": "", "worst_30s": "", "longest_run_s": " s", "entries_per_min": "/min"}


class UsageError(Exception):
    """Raised for exit-code-2 problems."""


# ----------------------------------------------------------------------------------------------- helpers

def fmt_time(seconds: float | None) -> str:
    """Seconds -> 'mm:ss.s'."""
    if seconds is None:
        return "--:--.-"
    minutes, sec = divmod(max(0.0, float(seconds)), 60.0)
    if round(sec, 1) >= 60.0:
        minutes, sec = minutes + 1, 0.0
    return f"{int(minutes):02d}:{sec:04.1f}"


def find_project(video: Path) -> Path:
    """The project folder: parent of `render/` if the video sits in a folder named render, else its folder."""
    parent = video.resolve().parent
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


def jpeg_size(path: Path) -> tuple[int, int]:
    """(width, height) of a JPEG file from its start-of-frame marker."""
    data = Path(path).read_bytes()
    if data[:2] != b"\xff\xd8":
        raise ValueError(f"{Path(path).name} is not a JPEG file")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD8:
            i += 1 if marker == 0xFF else 2
            continue
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    raise ValueError(f"no frame size in {Path(path).name}")


# ----------------------------------------------------------------------------------------------- pure measures

def box_size(quad) -> tuple[float, float]:
    """OCR quadrilateral [top-left, top-right, bottom-right, bottom-left] -> (line height, line width) in pixels.

    Measured along the box sides, not the axis-aligned bounds: a long line on a slightly tilted document would
    otherwise measure as tall text (a 2 degree tilt adds about 37 px to a 1,000 px line)."""
    def dist(a, b):
        return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    p0, p1, p2, p3 = quad[:4]
    return (dist(p0, p3) + dist(p1, p2)) / 2, (dist(p0, p1) + dist(p3, p2)) / 2


def boxes_of(result, width: int, height: int) -> list[list]:
    """OCR result [(quad, text, confidence)] -> [[text, height share, centre-y share, width share]]."""
    out = []
    for quad, text, confidence in result or []:
        text = str(text).strip()
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            continue
        if confidence < MIN_CONFIDENCE or len(text) < MIN_CHARS:
            continue
        box_h, box_w = box_size(quad)
        ys = [p[1] for p in quad]
        out.append([text, round(box_h / height, 3), round((max(ys) + min(ys)) / 2 / height, 2),
                    round(box_w / width, 3)])
    return out


def is_subtitle(h_share: float, cy_share: float) -> bool:
    """A small line in the bottom band is a burned-in subtitle, not part of a text card."""
    return cy_share >= SUBTITLE_BAND and h_share < SUBTITLE_MAX_H


def is_card(boxes: list[list]) -> tuple[bool, list[str]]:
    """(frame is a text card, texts of its large boxes)."""
    large = [b for b in boxes if not is_subtitle(b[1], b[2]) and b[1] >= LARGE_TEXT]
    return sum(b[1] * b[3] for b in large) >= CARD_AREA, [b[0] for b in large]


def runs(flags: list[bool], value: bool) -> list[tuple[int, int]]:
    """(start index, length) of every run of `value`."""
    out, start = [], None
    for i, flag in enumerate(flags + [not value]):
        if flag == value and start is None:
            start = i
        elif flag != value and start is not None:
            out.append((start, i - start))
            start = None
    return out


def summarize(frames: list[dict], every: float, limits: dict | None = None) -> dict:
    """Frames [{"t", "boxes"}] -> summary, slices, card frames, gates over the limit."""
    limits = limits or LIMITS
    if not frames:
        raise ValueError("no frames to summarize")
    cards, texts = [], []
    for frame in frames:
        card, text = is_card(frame["boxes"])
        cards.append(card)
        texts.append(text)
    n = len(cards)
    card_runs, gaps = runs(cards, True), runs(cards, False)
    step = max(1, int(round(SLICE_S / every)))
    slices = [{"start": frames[i]["t"], "samples": len(cards[i:i + step]),
               "ratio": round(sum(cards[i:i + step]) / len(cards[i:i + step]), 2)} for i in range(0, n, step)]
    # A short tail slice (a few samples) would swing to 100% on one card, so slices under half the length only
    # count when the whole video is shorter than one slice.
    scored = [s for s in slices if s["samples"] * 2 >= step] or slices
    worst = max(scored, key=lambda s: s["ratio"])
    longest = max(card_runs, key=lambda r: r[1], default=None)
    summary = {
        "duration_s": round(n * every, 1),
        "card_ratio": round(sum(cards) / n, 3),
        "worst_30s": worst["ratio"],
        "worst_30s_at": worst["start"],
        "longest_run_s": round(longest[1] * every, 1) if longest else 0.0,
        "longest_run_at": frames[longest[0]]["t"] if longest else None,
        "entries_per_min": round(len(card_runs) / (n * every / 60.0), 1),
        "run_median_s": round(statistics.median(r[1] for r in card_runs) * every, 1) if card_runs else 0.0,
        "gap_median_s": round(statistics.median(g[1] for g in gaps) * every, 1) if gaps else 0.0,
    }
    over = [{"name": k, "value": summary[k], "limit": v} for k, v in limits.items() if summary[k] > v]
    return {"summary": summary, "over": over, "slices": slices,
            "card_frames": [{"t": frames[i]["t"], "text": texts[i][:CARD_TEXT_KEPT]} for i in range(n) if cards[i]]}


def parse_limits(items: list[str] | None) -> dict:
    """['card_ratio=0.25'] -> LIMITS with that value replaced."""
    limits = dict(LIMITS)
    for item in items or []:
        name, _, value = item.partition("=")
        name = name.strip()
        if name not in LIMITS:
            raise UsageError(f"unknown limit {name!r}; use one of {', '.join(LIMITS)}")
        try:
            limits[name] = float(value)
        except ValueError:
            raise UsageError(f"--limit {item!r}: the value must be a number") from None
    return limits


# ----------------------------------------------------------------------------------------------- OCR and frames

def load_ocr():
    """The OCR engine as a callable: image path -> [(quad, text, confidence)]. Imported only when needed."""
    try:
        from rapidocr_onnxruntime import RapidOCR  # optional dependency
    except ImportError:
        raise UsageError("text_dose.py needs the optional OCR package: pip install rapidocr-onnxruntime") from None
    engine = RapidOCR()

    def run(path: Path):
        result, _ = engine(str(path))
        return result or []
    return run


def extract_frames(video: Path, every: float, folder: Path, start: float = 0.0, end: float = 0.0) -> list[Path]:
    """One JPEG every `every` seconds, scaled to OCR_WIDTH."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if start:
        cmd += ["-ss", f"{start:g}"]
    cmd += ["-i", str(video)]
    if end:
        cmd += ["-t", f"{end - start:g}"]
    cmd += ["-vf", f"fps=1/{every:g},scale={OCR_WIDTH}:-2", "-q:v", "3", str(folder / "f%05d.jpg")]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=FFMPEG_TIMEOUT_S)
    if proc.returncode != 0:
        raise UsageError(f"ffmpeg could not read {video.name}: {(proc.stderr or '').strip()[-300:]}")
    return sorted(folder.glob("f*.jpg"))


def ocr_video(video: Path, every: float, start: float, end: float, ocr) -> list[dict]:
    """Frames [{"t", "boxes"}] for the video (or the start..end slice of it)."""
    with tempfile.TemporaryDirectory(prefix="text-dose-") as tmp:
        files = extract_frames(video, every, Path(tmp), start, end)
        if not files:
            raise UsageError(f"no frames came out of {video.name} (is it a video, is the slice inside it?)")
        width, height = jpeg_size(files[0])
        frames = []
        for i, path in enumerate(files):
            frames.append({"t": round(start + i * every, 1), "boxes": boxes_of(ocr(path), width, height)})
    return frames


# ----------------------------------------------------------------------------------------------- output

def human_lines(report: dict, report_rel: str) -> list[str]:
    s = report["summary"]
    over = {o["name"] for o in report["over"]}
    lines = [f"text_dose: {report['video']}  one frame every {report['every_s']:g} s, {report['samples']} samples"]
    where = {"worst_30s": s.get("worst_30s_at"), "longest_run_s": s.get("longest_run_at")}
    for name, limit in report["limits"].items():
        status = "FAIL" if name in over else "PASS"
        at = f"  at {fmt_time(where[name])}" if where.get(name) is not None else ""
        lines.append(f"{status:<4}  {name:<16} {s[name]:<6g} (limit {limit:g}{LIMIT_UNITS[name]}){at}")
    top = sorted(report["slices"], key=lambda x: x["ratio"], reverse=True)[:3]
    if top and top[0]["ratio"] > 0:
        lines.append("Most text: " + ", ".join(f"{fmt_time(x['start'])} {x['ratio']:.2f}" for x in top if x["ratio"]))
    lines.append(f"VERDICT: {report['verdict']}")
    lines.append(f"Report: {report_rel}")
    return lines


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="OCR gate for on-screen text cards: share of the runtime, worst 30 s, longest run, "
                               "entries per minute. Exit 0 pass, 1 fail, 2 usage error.")
    parser.add_argument("video", nargs="?", help="the video to measure (e.g. render/final.mp4)")
    parser.add_argument("--every", type=float, default=DEFAULT_EVERY_S,
                        help=f"seconds between sampled frames (default {DEFAULT_EVERY_S:g})")
    parser.add_argument("--start", type=float, default=0.0, help="measure from this second (default 0)")
    parser.add_argument("--end", type=float, default=0.0, help="measure up to this second (default: the end)")
    parser.add_argument("--limit", action="append", metavar="NAME=VALUE",
                        help="change one gate, e.g. card_ratio=0.25 (repeatable)")
    parser.add_argument("--from-json", help="re-score the OCR boxes saved in an earlier report (no OCR, no video)")
    parser.add_argument("--out", help="report path (default <project>/qa/text-dose.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        limits = parse_limits(args.limit)
        if args.from_json:
            saved_path = Path(args.from_json)
            try:
                saved = json.loads(saved_path.read_text(encoding="utf-8-sig"))
                frames, every = saved["frames"], float(saved["every_s"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise UsageError(f"cannot re-score {saved_path.name}: {exc}") from exc
            base = {k: saved.get(k) for k in ("video", "sha256", "start_s", "end_s")}
            project = saved_path.resolve().parent.parent if saved_path.resolve().parent.name == "qa" \
                else saved_path.resolve().parent
        else:
            if not args.video:
                raise UsageError("give a video, or --from-json with an earlier report")
            video = Path(args.video)
            if not video.is_file():
                raise UsageError(f"video not found: {args.video}")
            if args.every <= 0:
                raise UsageError("--every must be more than zero")
            if args.start < 0 or (args.end and args.end <= args.start):
                raise UsageError("--start must be zero or more and --end after --start")
            if shutil.which("ffmpeg") is None:
                raise UsageError("ffmpeg not found on PATH; install ffmpeg (https://ffmpeg.org) first")
            ocr = load_ocr()
            project = find_project(video)
            every = args.every
            frames = ocr_video(video, every, args.start, args.end, ocr)
            base = {"video": rel_path(video, project), "sha256": sha256_file(video), "start_s": args.start,
                    "end_s": args.end or None}
        result = summarize(frames, every, limits)
    except UsageError as exc:
        print(f"text_dose: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("text_dose: error: ffmpeg timed out", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"text_dose: error: {exc}", file=sys.stderr)
        return 2

    verdict = "FAIL" if result["over"] else "PASS"
    report = {"tool": TOOL, "version": VERSION, **base,
              "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "every_s": every, "samples": len(frames), "verdict": verdict, "limits": limits, **result,
              "frames": frames}
    out_path = Path(args.out) if args.out else project / "qa" / "text-dose.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"text_dose: error: cannot write report: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({k: v for k, v in report.items() if k != "frames"}, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(report, rel_path(out_path, project))))
    return 1 if verdict == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
