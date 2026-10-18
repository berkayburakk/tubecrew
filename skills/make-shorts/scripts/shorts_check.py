#!/usr/bin/env python3
"""shorts_check.py - the code gate for a finished YouTube Short.

What it does
    Runs every render_check.py measurement on the Short (streams, sound to the end, total silence and dead air,
    loudness, black and frozen picture, the motion band, subtitle timing against the words) with the whole Short
    treated as the hook and no end-screen allowance, because a Short has no end screen. Then it adds the Shorts
    rules:

    aspect      vertical or square, as the picture is shown (rotation-aware); landscape is a regular video
    duration    at most 180 s (YouTube counts square and vertical uploads up to three minutes as Shorts since
                15 October 2024: support.google.com/youtube/answer/15424877); WARN within 2 s of it
    resolution  WARN under 1080x1920 (under 1080x1080 for a square video)
    opening     the first 2 s move (the long-form motion band scaled to the swipe window) or show text (OCR, or
                captions burned in from the first second); a still, textless opening FAILs
    loop        INFO: how close the last frame is to the first (a seamless loop is rewatched)
    safe_zone   burned-in captions (--burned with an ASS file, or an SRT with {\\an}/{\\pos} tags) or OCR'd text
                (--ocr) inside the bottom 25% or the right 15% of the frame, where YouTube draws the title,
                description and buttons; without either it is listed for the reviewer
    music       INFO over 60 s: an active Content ID claim blocks a Short longer than one minute

    Writes <project>/qa/shorts-check-<name>.json with the verdict and the SHA-256 of the video. upload.py reads it
    when upload.json says "short": true.

Why
    A Short is judged in its first two seconds and loops at its end; the long-form gate looks at a 45 s hook and
    forgives a static end screen. Same measurements, different rules.

How to run
    python shorts_check.py shorts/<name>.mp4 [--srt shorts/<name>.srt | --srt shorts/<name>.ass --burned]
                           [--words shorts/<name>.words.json] [--ocr] [--music] [--profile animated|live]
                           [--motion-band 38,2.2,4.9] [--fast] [--out PATH] [--json]

    --burned says the caption file was burned into the picture, so its positions are judged and its first cue
    counts as text in the opening. Without it the file is a caption track YouTube draws itself.
    --ocr reads the text on screen once a second (pip install rapidocr-onnxruntime).
    --profile live: a face or cursor carries the picture, so a still opening and the motion band only warn.
    The project is the parent of `shorts/` or `render/` (else the video's folder). Exemptions work as in
    render_check.py: qa/RENDER-EXEMPTION-<date>.md bound to this video's hash; aspect and duration cannot be waived.

Exit codes
    0  verdict PASS     1  verdict FAIL: fix the Short     2  usage or environment error

Requires ffmpeg and ffprobe on PATH. Standard library only (rapidocr-onnxruntime only with --ocr); imports
render_check.py and text_dose.py from this plugin by file path.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = "shorts_check.py"
VERSION = "0.1.0"

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
RENDER_CHECK = PLUGIN_ROOT / "skills" / "check-render" / "scripts" / "render_check.py"
TEXT_DOSE = PLUGIN_ROOT / "skills" / "check-render" / "scripts" / "text_dose.py"

# ----------------------------------------------------------------------------------------------- thresholds
SHORTS_MAX_S = 180.0           # support.google.com/youtube/answer/15424877: square or vertical, up to three minutes
NEAR_LIMIT_S = 2.0             # containers round; within 2 s of the limit is a WARN
MUSIC_CLAIM_S = 60.0           # same page: a Short over one minute with an active Content ID claim is blocked
TARGET_W, TARGET_H = 1080, 1920
OPEN_S = 2.0                   # the swipe decision
# The long-form band counts slow runs of 3 s inside a 45 s hook; a 2 s window at 4 fps has only 7 steps, so here
# "still" means most steps under render_check's slow threshold and no visual event (cut, pop) at all.
OPEN_STILL_SHARE = 0.5
OPEN_TEXT_CUE_S = 1.0          # burned captions that start within 1 s count as text in the opening
LOOP_SEAMLESS_DIFF = 6.0       # mean absolute difference (0-255, 96x54 gray) of last vs first frame
ZONE_BOTTOM = 0.25             # bottom 25%: title, description, channel name, audio
ZONE_RIGHT = 0.15              # right 15%: like, comment, share, remix buttons
ZONE_HIT_SHARE = 0.1           # a text box with more than 10% of its area in a zone is partly hidden
OCR_EVERY_S = 1.0
ZONE_LISTED = 5
# libass defaults for SRT input (what ffmpeg's subtitles filter uses when an SRT is burned): PlayRes 384x288,
# Arial 16, margins 10, bottom centre.
SRT_PLAY_RES = (384, 288)
SRT_FONT, SRT_MARGIN = 16, 10
CHAR_W, LINE_H = 0.5, 1.2      # rough text box: a character is half the font size wide, a line 1.2 font sizes tall
FFMPEG_TIMEOUT_S = 600

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"
SHORTS_CHECKS = ("aspect", "duration", "resolution", "opening", "loop", "safe_zone", "music", "end_screen")
NOT_EXEMPTABLE = {"decode", "aspect", "duration"}   # platform facts, not taste


class UsageError(Exception):
    """Raised for exit-code-2 problems."""


def _load(path: Path, name: str):
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rc = _load(RENDER_CHECK, "tubecrew_render_check")
td = _load(TEXT_DOSE, "tubecrew_text_dose")


def check(name: str, status: str, detail: str, start=None, end=None) -> dict:
    item = {"name": name, "status": status, "detail": detail}
    if start is not None:
        item["start"] = round(float(start), 2)
    if end is not None:
        item["end"] = round(float(end), 2)
    return item


def find_project(video: Path) -> Path:
    parent = video.resolve().parent
    return parent.parent if parent.name.lower() in ("render", "shorts") else parent


# ----------------------------------------------------------------------------------------------- geometry (pure)

def rotation_of(stream: dict) -> int:
    values = [(stream.get("tags") or {}).get("rotate")]
    values += [sd.get("rotation") for sd in stream.get("side_data_list") or [] if isinstance(sd, dict)]
    for value in values:
        try:
            return int(round(float(value))) % 360
        except (TypeError, ValueError):
            continue
    return 0


def display_geometry(ffprobe_json: str) -> dict:
    """ffprobe JSON -> {"width", "height", "rotation"} as the picture is shown (90/270 degrees swap the sides)."""
    data = json.loads(ffprobe_json or "{}")
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video" and not (stream.get("disposition") or {}).get("attached_pic"):
            w, h, rot = int(stream.get("width") or 0), int(stream.get("height") or 0), rotation_of(stream)
            if rot % 180 == 90:
                w, h = h, w
            return {"width": w, "height": h, "rotation": rot}
    return {"width": None, "height": None, "rotation": 0}


def zones_hit(box: tuple) -> list:
    """Normalised (x0, y0, x1, y1) -> the UI zones holding more than ZONE_HIT_SHARE of its area."""
    x0, y0, x1, y1 = box
    area = max(1e-9, (x1 - x0) * (y1 - y0))
    hits = []
    for name, (zx0, zy0, zx1, zy1) in (("bottom", (0.0, 1.0 - ZONE_BOTTOM, 1.0, 1.0)),
                                       ("right", (1.0 - ZONE_RIGHT, 0.0, 1.0, 1.0))):
        inter = max(0.0, min(x1, zx1) - max(x0, zx0)) * max(0.0, min(y1, zy1) - max(y0, zy0))
        if inter / area > ZONE_HIT_SHARE:
            hits.append(name)
    return hits


def text_box(play_w: float, play_h: float, font: float, align: int, margins: tuple, lines: list,
             pos: tuple | None = None) -> tuple:
    """Estimated normalised box of a caption: numpad `align` (1-3 bottom, 4-6 middle, 7-9 top; left, centre,
    right), margins (left, right, vertical) in script pixels, optional \\pos anchor. Lines wrap at the margins."""
    ml, mr, mv = margins
    avail = max(font, play_w - ml - mr)
    char_w, line_h = CHAR_W * font, LINE_H * font
    count, widest = 0, 0.0
    for line in lines or [""]:
        width = len(line) * char_w
        count += max(1, math.ceil(width / avail))
        widest = max(widest, min(width, avail))
    height = count * line_h
    col, row = (align - 1) % 3, (align - 1) // 3
    if pos is not None:
        ax, ay = pos
    else:
        ax = (ml, ml + (play_w - ml - mr) / 2, play_w - mr)[col]
        ay = (play_h - mv, play_h / 2, mv)[row]
    x0 = (ax, ax - widest / 2, ax - widest)[col]
    y0 = (ay - height, ay - height / 2, ay)[row]
    return (round(x0 / play_w, 4), round(y0 / play_h, 4), round((x0 + widest) / play_w, 4),
            round((y0 + height) / play_h, 4))


_POS_RE = re.compile(r"\\pos\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\)")
_AN_RE = re.compile(r"\\an([1-9])")
_FS_RE = re.compile(r"\\fs([\d.]+)")
_OVERRIDE_RE = re.compile(r"\{[^}]*\}")
_ASS_TIME_RE = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[.](\d{1,2})")


def _ass_seconds(text: str) -> float:
    m = _ASS_TIME_RE.fullmatch(text.strip())
    if not m:
        raise ValueError(f"bad ASS time {text!r}")
    h, mi, s, cs = m.groups()
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(cs.ljust(2, "0")) / 100.0


def parse_ass(text: str) -> list:
    """ASS file -> cues [{"index", "start", "end", "lines", "box"}] with each cue's estimated normalised box."""
    play_w, play_h = SRT_PLAY_RES
    styles, style_fmt, event_fmt, section = {}, [], [], ""
    cues = []
    for raw in text.lstrip("\ufeff").replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line.lower()
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if section == "[script info]" and key in ("playresx", "playresy"):
            try:
                if key == "playresx":
                    play_w = float(value)
                else:
                    play_h = float(value)
            except ValueError:
                pass
        elif section.startswith("[v4") and key == "format":
            style_fmt = [f.strip().lower() for f in value.split(",")]
        elif section.startswith("[v4") and key == "style" and style_fmt:
            parts = [p.strip() for p in value.split(",", len(style_fmt) - 1)]
            styles[parts[0]] = dict(zip(style_fmt, parts))
        elif section == "[events]" and key == "format":
            event_fmt = [f.strip().lower() for f in value.split(",")]
        elif section == "[events]" and key == "dialogue" and event_fmt:
            parts = [p.strip() for p in value.split(",", len(event_fmt) - 1)]
            ev = dict(zip(event_fmt, parts))
            try:
                start, end = _ass_seconds(ev.get("start", "")), _ass_seconds(ev.get("end", ""))
            except ValueError:
                continue
            st = styles.get(ev.get("style", ""), {})

            def num(name, default, ev=ev, st=st):
                for source in (ev, st):
                    try:
                        value = float(source.get(name, ""))
                        if source is st or value:
                            return value
                    except ValueError:
                        continue
                return default
            body = ev.get("text", "")
            font = float(_FS_RE.search(body).group(1)) if _FS_RE.search(body) else num("fontsize", SRT_FONT)
            align = int(_AN_RE.search(body).group(1)) if _AN_RE.search(body) else int(num("alignment", 2))
            pos_m = _POS_RE.search(body)
            pos = (float(pos_m.group(1)), float(pos_m.group(2))) if pos_m else None
            lines = [ln.strip() for ln in _OVERRIDE_RE.sub("", body).replace("\\n", "\\N").split("\\N")]
            margins = (num("marginl", SRT_MARGIN), num("marginr", SRT_MARGIN), num("marginv", SRT_MARGIN))
            cues.append({"index": len(cues) + 1, "start": start, "end": end, "lines": [ln for ln in lines if ln],
                         "box": text_box(play_w, play_h, font, max(1, min(9, align)), margins, lines, pos)})
    return cues


def srt_positions(text: str) -> list:
    """SRT text -> cues with estimated boxes under libass's SRT defaults (bottom centre unless {\\anN}/{\\pos})."""
    cues = rc.parse_srt(text)
    raw_blocks = [b for b in re.split(r"\n\s*\n", text.lstrip("\ufeff").replace("\r\n", "\n")) if "-->" in b]
    out = []
    for cue, block in zip(cues, raw_blocks):
        an, pos = _AN_RE.search(block), _POS_RE.search(block)
        align = int(an.group(1)) if an else 2
        anchor = (float(pos.group(1)), float(pos.group(2))) if pos else None
        out.append(dict(cue, box=text_box(SRT_PLAY_RES[0], SRT_PLAY_RES[1], SRT_FONT, align,
                                          (SRT_MARGIN, SRT_MARGIN, SRT_MARGIN), cue["lines"], anchor),
                        positioned=bool(an or pos)))
    return out


# ----------------------------------------------------------------------------------------------- frames (pure)

def opening_summary(diffs: list) -> dict:
    """Frame differences of the first OPEN_S seconds (4 fps, 96x54 gray) -> events, still share, moves."""
    steps = len(diffs)
    if not steps:
        return {"steps": 0, "events": 0, "still_share": None, "frozen_share": None, "moves": None}
    events = sum(1 for d in diffs if d > rc.EVENT_DIFF)
    still = sum(1 for d in diffs if d < rc.SLOW_DIFF) / steps
    frozen = sum(1 for d in diffs if d < rc.FROZEN_DIFF) / steps
    return {"steps": steps, "events": events, "still_share": round(still, 2), "frozen_share": round(frozen, 2),
            "median_diff": round(sorted(diffs)[steps // 2], 2), "moves": events > 0 or still <= OPEN_STILL_SHARE}


def frame_difference(a: bytes, b: bytes) -> float | None:
    if not a or len(a) != len(b):
        return None
    return round(sum(abs(x - y) for x, y in zip(a, b)) / len(a), 2)


# ----------------------------------------------------------------------------------------------- Shorts rules (pure)

def shorts_checks(geo: dict, duration: float, opening: dict, opening_text: list, loop_diff: float | None,
                  zone: dict, profile: str = "animated") -> list:
    """The Shorts rules from measurements. `opening_text`: sources that put text on screen in the first OPEN_S s
    (e.g. ["ocr"], ["burned captions"]). `zone`: {"measured": bool, "source", "hits": [...], "fail": bool}."""
    out = []
    w, h = geo.get("width"), geo.get("height")
    if not w or not h:
        out.append(check("aspect", FAIL, "no picture size: cannot confirm the Short is vertical or square"))
    elif w > h:
        out.append(check("aspect", FAIL, f"{w}x{h} is landscape: YouTube treats it as a regular video, not a Short "
                                         "(Shorts are square or vertical)"))
    else:
        shape = "square" if w == h else "vertical"
        out.append(check("aspect", PASS, f"{w}x{h} {shape}" + (f" (rotated {geo['rotation']} degrees)"
                                                               if geo.get("rotation") else "")))
    if duration > SHORTS_MAX_S:
        out.append(check("duration", FAIL, f"{duration:.1f} s: over YouTube's {SHORTS_MAX_S:g} s Shorts limit"))
    elif duration > SHORTS_MAX_S - NEAR_LIMIT_S:
        out.append(check("duration", WARN, f"{duration:.1f} s: within {NEAR_LIMIT_S:g} s of the {SHORTS_MAX_S:g} s "
                                           "limit; container rounding can push it over"))
    else:
        out.append(check("duration", PASS, f"{duration:.1f} s (limit {SHORTS_MAX_S:g} s)"))
    if w and h and w <= h:
        need_w, need_h = (TARGET_W, TARGET_W) if w == h else (TARGET_W, TARGET_H)
        if w < need_w or h < need_h:
            out.append(check("resolution", WARN, f"{w}x{h} is under {need_w}x{need_h}: it looks soft on phones"))
        elif abs(w / h - 9 / 16) > 0.01 and w != h:
            out.append(check("resolution", INFO, f"{w}x{h} is vertical but not 9:16: the player adds bars"))
        else:
            out.append(check("resolution", PASS, f"{w}x{h}"))
    if opening.get("moves") is None:
        out.append(check("opening", WARN, "the opening could not be measured (too few frames)"))
    elif opening["moves"]:
        out.append(check("opening", PASS, f"the first {OPEN_S:g} s move: {opening['events']} event(s), "
                                          f"{opening['still_share']:.0%} of steps look still", 0.0, OPEN_S))
    elif opening_text:
        out.append(check("opening", PASS, f"the picture is still in the first {OPEN_S:g} s, but text is on screen "
                                          f"({', '.join(opening_text)})", 0.0, OPEN_S))
    else:
        detail = (f"the first {OPEN_S:g} s stand still ({opening['still_share']:.0%} of steps look still, no cut or "
                  "pop) and no text was detected: that is where the viewer decides to swipe. Open on movement or "
                  "a text hook (if a text card carries it, run with --ocr)")
        status = FAIL
        if profile == "live":
            status, detail = WARN, detail + " (profile live: check by eye that a face carries the opening)"
        out.append(check("opening", status, detail, 0.0, OPEN_S))
    if loop_diff is None:
        out.append(check("loop", INFO, "first and last frame not compared"))
    elif loop_diff <= LOOP_SEAMLESS_DIFF:
        out.append(check("loop", INFO, f"the last frame matches the first (difference {loop_diff:g}): it can loop "
                                       "without a visible seam"))
    else:
        out.append(check("loop", INFO, f"the last frame differs from the first (difference {loop_diff:g}): the loop "
                                       "shows a cut; fine when the last line leads back into the first"))
    if not zone.get("measured"):
        out.append(check("safe_zone", INFO, "text positions not measured: pass the burned caption file with --burned "
                                            "or run --ocr; until then the reviewer checks that no text sits in the "
                                            f"bottom {ZONE_BOTTOM:.0%} or the right {ZONE_RIGHT:.0%}"))
    elif zone.get("hits"):
        first = zone["hits"][0]
        listed = "; ".join(f"{h['t']:.1f} s \"{h['text'][:30]}\" ({'+'.join(h['zones'])})"
                           for h in zone["hits"][:ZONE_LISTED])
        more = f" and {len(zone['hits']) - ZONE_LISTED} more" if len(zone["hits"]) > ZONE_LISTED else ""
        status = FAIL if zone.get("fail") else WARN
        out.append(check("safe_zone", status, f"{len(zone['hits'])} text item(s) from {zone['source']} inside the UI "
                                              f"zones (bottom {ZONE_BOTTOM:.0%}, right {ZONE_RIGHT:.0%}), where "
                                              f"YouTube's title and buttons cover them: {listed}{more}",
                         first["t"], first.get("end")))
    else:
        out.append(check("safe_zone", PASS, f"no text from {zone['source']} in the bottom {ZONE_BOTTOM:.0%} or the "
                                            f"right {ZONE_RIGHT:.0%}"))
    if duration > MUSIC_CLAIM_S:
        out.append(check("music", INFO, f"over {MUSIC_CLAIM_S:g} s: a Short longer than one minute with an active "
                                        "Content ID claim is blocked, and Shorts library songs are limited in length; "
                                        "use music you own or have cleared"))
    out.append(check("end_screen", INFO, "Shorts have no end screen: the last seconds were judged like the rest"))
    return out


# ----------------------------------------------------------------------------------------------- measuring

def _ffmpeg_bytes(cmd: list) -> bytes:
    proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S, stdin=subprocess.DEVNULL)
    return proc.stdout or b""


def gray_cmd(video: Path, seconds: float | None = None, from_end: float | None = None, fps: bool = True) -> list:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error"]
    if from_end:
        cmd += ["-sseof", f"-{from_end:g}"]
    cmd += ["-i", str(video)]
    if seconds:
        cmd += ["-t", f"{seconds:g}"]
    chain = (f"fps={rc.MOTION_FPS}," if fps else "") + f"scale={rc.MOTION_W}:{rc.MOTION_H},format=gray"
    return cmd + ["-map", "0:v:0", "-vf", chain, "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]


def measure_frames(video: Path) -> tuple:
    """(opening diffs, first frame, last frame) at render_check's motion sampling (96x54 gray)."""
    size = rc.MOTION_W * rc.MOTION_H
    head = _ffmpeg_bytes(gray_cmd(video, seconds=OPEN_S))
    tail = _ffmpeg_bytes(gray_cmd(video, from_end=0.5, fps=False))
    last = tail[(len(tail) // size - 1) * size:(len(tail) // size) * size] if len(tail) >= size else b""
    return rc.frame_diffs(head, size), head[:size], last


def load_ocr():
    """The OCR engine (rapidocr-onnxruntime, through text_dose.py), loaded before any slow decode."""
    try:
        return td.load_ocr()
    except td.UsageError:
        raise UsageError("--ocr needs the optional OCR package: pip install rapidocr-onnxruntime") from None


def ocr_text(video: Path, ocr) -> list:
    """Text boxes read once a second: [{"t", "text", "box"}] with normalised boxes."""
    found = []
    with tempfile.TemporaryDirectory(prefix="shorts-check-") as tmp:
        try:
            files = td.extract_frames(video, OCR_EVERY_S, Path(tmp))
        except td.UsageError as exc:
            raise UsageError(str(exc)) from exc
        if not files:
            return found
        width, height = td.jpeg_size(files[0])
        for i, path in enumerate(files):
            for quad, text, confidence in ocr(path) or []:
                text = str(text).strip()
                try:
                    if float(confidence) < td.MIN_CONFIDENCE or len(text) < td.MIN_CHARS:
                        continue
                except (TypeError, ValueError):
                    continue
                xs, ys = [p[0] for p in quad], [p[1] for p in quad]
                found.append({"t": round(i * OCR_EVERY_S, 1), "text": text,
                              "box": (round(min(xs) / width, 4), round(min(ys) / height, 4),
                                      round(max(xs) / width, 4), round(max(ys) / height, 4))})
    return found


def load_captions(path: Path) -> tuple:
    """(cues for render_check's subtitle checks, positioned cues or None)."""
    text = rc.read_subtitles(path)
    if path.suffix.lower() in (".ass", ".ssa"):
        cues = parse_ass(text)
        return [{k: c[k] for k in ("index", "start", "end", "lines")} for c in cues], cues
    return rc.parse_srt(text), srt_positions(text)


def zone_report(positioned: list | None, burned: bool, ocr_found: list | None) -> dict:
    """Which text sits in the UI zones, from burned caption positions (FAIL) or OCR (WARN: it may be footage)."""
    if burned and positioned is not None:
        hits = []
        for c in positioned:
            zones = zones_hit(c["box"])
            if zones:
                hits.append({"t": c["start"], "end": c["end"], "text": " ".join(c["lines"]), "zones": zones})
        source = "the burned captions" + ("" if any(c.get("positioned", True) for c in positioned)
                                          else " (default bottom position)")
        return {"measured": True, "source": source, "hits": hits, "fail": True}
    if ocr_found is not None:
        hits = [{"t": f["t"], "text": f["text"], "zones": zones_hit(f["box"])} for f in ocr_found]
        return {"measured": True, "source": "OCR", "hits": [h for h in hits if h["zones"]], "fail": False}
    return {"measured": False}


def load_exemptions(project: Path, sha256: str) -> tuple:
    """RENDER-EXEMPTION files that name this Short's hash -> (exemptions, notes)."""
    names = (set(rc.CHECK_NAMES) | set(SHORTS_CHECKS)) - NOT_EXEMPTABLE
    matching, notes = [], []
    qa = project / "qa"
    if not qa.is_dir():
        return matching, notes
    for path in sorted(qa.glob(rc.EXEMPTION_GLOB)):
        shown = f"qa/{path.name}"
        try:
            parsed = rc.parse_exemption(rc.read_text_any(path))
        except OSError as exc:
            notes.append(check("exemption", WARN, f"cannot read {shown}: {exc}"))
            continue
        if not any(sha256.startswith(h) for _, h in parsed["videos"]):
            continue
        if len(parsed["reason"] or "") < rc.EXEMPTION_REASON_MIN:
            notes.append(check("exemption", WARN, f"{shown} has no 'Reason:' line; not applied"))
            continue
        refused = [c for c in parsed["checks"] if c not in names]
        if refused:
            notes.append(check("exemption", WARN, f"{shown} names check(s) that cannot be exempted for a Short: "
                                                  f"{', '.join(refused)}"))
        known = [c for c in parsed["checks"] if c in names]
        if known:
            matching.append({"file": shown, "checks": known, "reason": parsed["reason"]})
    return matching, notes


# ----------------------------------------------------------------------------------------------- output

def human_lines(report: dict, report_rel: str) -> list:
    s = report["measured"]["shorts"]
    head = (f"shorts_check: {report['video']}  {rc.fmt_time(report['measured'].get('duration'))}  "
            f"{s['display_width']}x{s['display_height']}  profile {report['measured'].get('profile')}")
    lines = [head]
    width = max([len(c["name"]) for c in report["checks"]] + [4])
    for c in report["checks"]:
        span = ""
        if "start" in c:
            span = rc.fmt_time(c["start"]) + ("-" + rc.fmt_time(c["end"]) if "end" in c else "")
        lines.append(f"{c['status']:<4}  {c['name']:<{width}}  {span:<15}  {c['detail']}")
    if report["for_reviewer"]:
        lines.append("For the reviewer (render-auditor):")
        lines += [f"  - {item}" for item in report["for_reviewer"]]
    lines.append(f"VERDICT: {report['verdict']}")
    lines.append(f"Report: {report_rel}")
    return lines


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: list | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Code gate for a YouTube Short: render_check's measurements plus aspect, length, "
                               "opening, loop and UI safe zones. Exit 0 pass, 1 fail, 2 usage error.")
    parser.add_argument("video", help="the Short (e.g. shorts/the-deal.mp4)")
    parser.add_argument("--srt", help="its captions (.srt, or the .ass that shorts_cut.py --burn wrote)")
    parser.add_argument("--burned", action="store_true", help="the captions are burned into the picture")
    parser.add_argument("--words", help="word-timed JSON in the Short's time (shorts_cut.py --words writes one)")
    parser.add_argument("--ocr", action="store_true", help="read on-screen text once a second (rapidocr-onnxruntime)")
    parser.add_argument("--music", action="store_true", help="music-only soundtrack (stricter dead-air limits)")
    parser.add_argument("--profile", choices=("animated", "live"), default="animated",
                        help="live: a face or cursor carries the picture; a still opening and the band only warn")
    parser.add_argument("--motion-band", default=None, help="EVENTS_PER_MIN,FROZEN_PCT,SLOW_PCT (as render_check)")
    parser.add_argument("--fast", action="store_true", help="coarser black and freeze sampling (previews)")
    parser.add_argument("--out", help="report path (default <project>/qa/shorts-check-<name>.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        if rc is None or td is None:
            raise UsageError("make-shorts needs the check-render skill of this plugin (render_check.py, "
                             "text_dose.py) next to it")
        video = Path(args.video)
        if not video.is_file():
            raise UsageError(f"video not found: {args.video}")
        srt = Path(args.srt) if args.srt else None
        if srt is not None and not srt.is_file():
            raise UsageError(f"caption file not found: {args.srt}")
        if args.burned and srt is None:
            raise UsageError("--burned describes a caption file: pass it with --srt")
        words_path = Path(args.words) if args.words else None
        if words_path is not None and srt is None:
            raise UsageError("--words needs --srt: the words are compared with the caption cues")
        if words_path is not None and not words_path.is_file():
            raise UsageError(f"words file not found: {args.words}")
        try:
            band = rc.parse_band(args.motion_band) if args.motion_band else dict(rc.DEFAULT_BAND)
            rc.require_tools()
        except rc.UsageError as exc:
            raise UsageError(str(exc)) from exc
        project = find_project(video)
        ocr = load_ocr() if args.ocr else None
        cues = positioned = None
        if srt is not None:
            try:
                cues, positioned = load_captions(srt)
            except OSError as exc:
                raise UsageError(f"cannot read caption file: {exc}") from exc
        try:
            words = rc.load_words(words_path) if words_path is not None else None
            measured, decode_error = rc.measure(video, fast=args.fast, hook_seconds=SHORTS_MAX_S, music=args.music)
        except rc.UsageError as exc:
            raise UsageError(str(exc)) from exc
        probe = subprocess.run(["ffprobe", "-v", "error", "-of", "json", "-show_streams", str(video)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                               stdin=subprocess.DEVNULL)
        geo = display_geometry(probe.stdout)
        diffs, first, last = measure_frames(video) if measured.get("has_video") else ([], b"", b"")
        ocr_found = ocr_text(video, ocr) if ocr is not None and measured.get("has_video") else None
    except UsageError as exc:
        print(f"shorts_check: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("shorts_check: error: ffmpeg timed out", file=sys.stderr)
        return 2

    duration = measured.get("video_duration") or measured.get("duration") or 0.0
    measured.update(profile=args.profile, hook_seconds=round(duration, 3), motion_band=band, shorts_rules=True)
    inputs = [p for p in (srt, words_path) if p is not None]
    if inputs:
        measured["inputs_checked"] = [rc.rel_path(p, project) for p in inputs]
        measured["stale_inputs"] = rc.stale_inputs(video, inputs, project)
    checks = rc.evaluate(measured, cues, hook_seconds=duration, profile=args.profile, words=words, band=band)
    for c in checks:
        if c["name"] == "freeze" and c["status"] == PASS:   # render_check's wording assumes an end screen
            c["detail"] = c["detail"].replace(" outside the end screen", "") + " (no end-screen allowance)"
    if decode_error:
        checks.insert(0, check("decode", FAIL, f"ffmpeg could not decode the whole file: {decode_error[-200:]}"))

    opening = opening_summary(diffs)
    opening_text = []
    if ocr_found and any(f["t"] < OPEN_S for f in ocr_found):
        opening_text.append("OCR")
    if args.burned and cues and min(c["start"] for c in cues) < OPEN_TEXT_CUE_S:
        opening_text.append("burned captions")
    zone = zone_report(positioned, args.burned, ocr_found)
    loop_diff = frame_difference(first, last)
    checks += shorts_checks(geo, duration, opening, opening_text, loop_diff, zone, args.profile)

    digest = rc.sha256_file(video)
    exemptions, notes = load_exemptions(project, digest)
    rc.apply_exemptions(checks, exemptions)
    checks += notes
    verdict = rc.verdict_of(checks)

    for_reviewer = ["does a stranger get the point from the first 2 s, with the sound off and on?",
                    "does the last line pay off and lead back into the first (the loop)?",
                    "no intro, no 'subscribe' card, no end screen layout, no long-video chapter cards"]
    if not zone.get("measured"):
        for_reviewer.append(f"no text or caption in the bottom {ZONE_BOTTOM:.0%} or the right {ZONE_RIGHT:.0%} of "
                            "the frame (YouTube's title and buttons sit there)")
    measured["shorts"] = {"display_width": geo["width"], "display_height": geo["height"],
                          "rotation": geo["rotation"], "opening": opening, "opening_text": opening_text,
                          "loop_first_last_diff": loop_diff, "ocr": ocr_found is not None,
                          "safe_zone": {"bottom": ZONE_BOTTOM, "right": ZONE_RIGHT, **zone}}
    if srt is not None:
        measured["srt"] = rc.rel_path(srt, project)
        measured["srt_burned"] = args.burned
    if words_path is not None:
        measured["words"] = rc.rel_path(words_path, project)
    report = rc._finite({
        "tool": TOOL, "version": VERSION,
        "video": rc.rel_path(video, project), "sha256": digest, "size": video.stat().st_size,
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": verdict, "measured": measured, "checks": checks, "exemptions": exemptions,
        "for_reviewer": for_reviewer,
    })
    out_path = Path(args.out) if args.out else project / "qa" / f"shorts-check-{video.stem}.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"shorts_check: error: cannot write report: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(report, rc._display_path(out_path, project))))
    return 1 if verdict == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
