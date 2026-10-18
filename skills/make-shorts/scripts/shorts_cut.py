#!/usr/bin/env python3
"""shorts_cut.py - cut one Short out of the long render and reframe it to vertical 1080x1920.

What it does
    Cuts a window (a candidate from shorts_pick.py, or --start/--end) out of the finished long video with ffmpeg,
    re-encodes it to 1080x1920 H.264 + AAC and keeps the sound (with a few milliseconds of fade at both edges so
    the loop seam does not click). Two framings:

    crop  a 9:16 slice of the picture, centred (--center, the default) or from a fixed left edge (--x, in source
          pixels). Best when the action sits in one place: a face, a single chart.
    fit   the whole 16:9 picture, scaled to the width and centred on a blurred, darkened copy of itself. Nothing is
          cut off; --fill 0-0.4 trims that share of the width so the picture grows (0.2 is a good start for
          slides with margins).

    Captions are optional and are never burned in by default. --srt re-times the long video's caption file to the
    cut; --words builds short caption cues (up to 4 words) from a word-timed transcript and also writes the
    window's words, re-timed, for shorts_check.py. Both write <name>.srt next to the Short. --burn draws them into
    the picture from an ASS file placed above the bottom UI zone and inside the right one (it needs an ffmpeg with
    libass; without it the script stops with exit 2).

Why
    A Short cut by hand drifts: a cut that starts half a word early, a crop that slices a chart in half, captions
    that run on the old timeline or sit under YouTube's buttons. One command keeps the cut on the word boundaries
    shorts_pick.py measured and the framing repeatable.

How to run
    python shorts_cut.py render/final.mp4 --candidate 1 [--candidates qa/shorts-candidates.json]
    python shorts_cut.py render/final.mp4 --start 132.4 --end 170.0 --name the-deal
        [--mode crop|fit] [--x 640 | --center] [--fill 0.2]
        [--srt voice/captions.srt | --words qa/voice-check-transcript.json [--words-offset 0]] [--burn]
        [--out PATH] [--force] [--crf 20] [--preset medium] [--json]

    The Short goes to <project>/shorts/<name>.mp4 (the project is the video's folder, or the parent of render/).
    --out writes elsewhere, for example straight into the Short's own upload folder (<short>/render/<name>.mp4).
    The source is never overwritten, and an existing Short is replaced only with --force.

Exit codes
    0  the Short was written
    2  usage or environment error (missing file, bad window, ffmpeg or libass missing, ffmpeg failed)

Requires ffmpeg and ffprobe on PATH. Standard library only; imports render_check.py's parsers by file path.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL = "shorts_cut.py"
VERSION = "0.1.0"

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
RENDER_CHECK = PLUGIN_ROOT / "skills" / "check-render" / "scripts" / "render_check.py"

OUT_W, OUT_H = 1080, 1920
SHORTS_MAX_S = 180.0           # support.google.com/youtube/answer/15424877 (square or vertical, since 15 Oct 2024)
MIN_CUT_S = 1.0
FILL_MAX = 0.4                 # trimming more than 40% of the width is a crop, use --mode crop
BG_W, BG_H = 270, 480          # the blurred background is made small, blurred, then scaled up (cheap and smooth)
BG_BLUR_SIGMA = 12
BG_DARKEN = 0.6                # luma multiplier: the background stays behind the picture
FADE_IN_S, FADE_OUT_S = 0.02, 0.06
CAPTION_MAX_WORDS = 4          # vertical captions: a few words at a time
CAPTION_MAX_S = 2.0
CAPTION_TAIL_S = 0.1
CUE_MIN_S = 0.05
# Burned captions: the bottom 25% and the right 15% of a Short sit under YouTube's title, description and buttons.
ASS_FONT_SIZE = 64
ASS_MARGIN_V = 576             # 30% of 1920: the bottom of the caption block sits above the bottom UI zone
ASS_MARGIN_LR = 186            # 15% of 1080 + 24 px: centred text stays clear of the right-hand buttons
FFMPEG_TIMEOUT_S = 3600
FFPROBE_TIMEOUT_S = 120
CANDIDATES = "shorts-candidates.json"
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


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


# ----------------------------------------------------------------------------------------------- helpers

def even(value: float) -> int:
    """Nearest even integer, at least 2 (H.264 4:2:0 needs even sizes)."""
    return max(2, int(round(value / 2.0)) * 2)


def find_project(video: Path) -> Path:
    parent = video.resolve().parent
    return parent.parent if parent.name.lower() in ("render", "shorts") else parent


def rel_path(path: Path, base: Path) -> str:
    try:
        rel = os.path.relpath(str(Path(path).resolve()), str(Path(base).resolve()))
    except ValueError:
        return Path(path).name
    if os.path.isabs(rel) or rel == ".." or rel.startswith(".." + os.sep) or rel.startswith("../"):
        return Path(path).name
    return Path(rel).as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def rotation_of(stream: dict) -> int:
    """Display rotation in degrees from a video stream (the `rotate` tag or the display matrix side data)."""
    values = [(stream.get("tags") or {}).get("rotate")]
    values += [sd.get("rotation") for sd in stream.get("side_data_list") or [] if isinstance(sd, dict)]
    for value in values:
        try:
            return int(round(float(value))) % 360
        except (TypeError, ValueError):
            continue
    return 0


def display_size(width: int, height: int, rotation: int) -> tuple:
    """(width, height) as the picture is shown: a 90 or 270 degree rotation swaps them (ffmpeg auto-rotates)."""
    return (height, width) if rotation % 180 == 90 else (width, height)


def probe_video(video: Path) -> dict:
    proc = subprocess.run(["ffprobe", "-v", "error", "-of", "json", "-show_format", "-show_streams", str(video)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=FFPROBE_TIMEOUT_S, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise UsageError(f"ffprobe cannot read {video.name}: {(proc.stderr or '').strip()[-300:]}")
    data = json.loads(proc.stdout or "{}")
    info = rc.parse_ffprobe(proc.stdout)
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
                   and not (s.get("disposition") or {}).get("attached_pic")), None)
    if stream is None:
        raise UsageError(f"{video.name} has no video stream")
    rotation = rotation_of(stream)
    info["rotation"] = rotation
    info["display_width"], info["display_height"] = display_size(int(stream.get("width") or 0),
                                                                 int(stream.get("height") or 0), rotation)
    return info


# ----------------------------------------------------------------------------------------------- filters (pure)

def crop_filter(width: int, height: int, x: int | None = None) -> str:
    """9:16 slice of a source shown at width x height, scaled to 1080x1920. `x` is the slice's left edge in source
    pixels (None = centred). A source narrower than 9:16 is cut top and bottom instead (centred)."""
    if width * 16 > height * 9:
        crop_w = min(width, even(height * 9 / 16))
        left = (width - crop_w) // 2 if x is None else int(x)
        if left < 0 or left + crop_w > width:
            raise UsageError(f"--x {x} puts the {crop_w}-px slice outside the {width}-px picture (0..{width - crop_w})")
        crop = f"crop={crop_w}:{height}:{left}:0"
    else:
        if x is not None:
            raise UsageError("--x needs a source wider than 9:16; this one is cut top and bottom")
        crop_h = min(height, even(width * 16 / 9))
        crop = f"crop={width}:{crop_h}:0:{(height - crop_h) // 2}"
    return f"[0:v]{crop},scale={OUT_W}:{OUT_H},setsar=1,format=yuv420p[v]"


def fit_size(width: int, height: int, fill: float = 0.0) -> tuple:
    """(picture width, picture height, trimmed source width) for fit mode: the picture as wide as the Short (or as
    tall, for a tall source) after trimming `fill` of the source width."""
    src_w = even(width * (1.0 - fill)) if fill else width
    fg_w, fg_h = OUT_W, even(OUT_W * height / src_w)
    if fg_h > OUT_H:
        fg_w, fg_h = even(OUT_H * src_w / height), OUT_H
    return fg_w, fg_h, src_w


def fit_filter(width: int, height: int, fill: float = 0.0) -> str:
    """The whole picture centred on a blurred, darkened copy of itself, 1080x1920."""
    fg_w, fg_h, src_w = fit_size(width, height, fill)
    trim = f"crop={src_w}:{height}:{(width - src_w) // 2}:0," if src_w != width else ""
    return (f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={BG_W}:{BG_H}:force_original_aspect_ratio=increase,crop={BG_W}:{BG_H},"
            f"gblur=sigma={BG_BLUR_SIGMA},scale={OUT_W}:{OUT_H},lutyuv=y=val*{BG_DARKEN},setsar=1[bg];"
            f"[fgsrc]{trim}scale={fg_w}:{fg_h},setsar=1[fg];"
            f"[bg][fg]overlay=x={(OUT_W - fg_w) // 2}:y={(OUT_H - fg_h) // 2},format=yuv420p[v]")


def build_cmd(source: Path, out: Path, start: float, duration: float, graph: str, has_audio: bool,
              crf: int = 20, preset: str = "medium", burn: str | None = None) -> list:
    """The ffmpeg argv. Input seeking (-ss before -i) is frame accurate when re-encoding and starts the cut's
    timestamps at zero, which the captions and the audio fades rely on."""
    if burn:
        graph = graph[:-len("[v]")] + f"[vb];[vb]subtitles={burn}[v]"
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
           "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{duration:.3f}",
           "-filter_complex", graph, "-map", "[v]"]
    if has_audio:
        fade_out_at = max(0.0, duration - FADE_OUT_S)
        cmd += ["-map", "0:a:0", "-af", f"afade=t=in:st=0:d={FADE_IN_S:g},afade=t=out:st={fade_out_at:.3f}:"
                                        f"d={FADE_OUT_S:g}", "-c:a", "aac", "-b:a", "192k"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(out)]
    return cmd


# ----------------------------------------------------------------------------------------------- captions (pure)

def retime_cues(cues: list, start: float, end: float) -> tuple:
    """Cues of the long video -> the cues inside [start, end], shifted to the cut. Returns (cues, notes)."""
    out, notes = [], []
    length = end - start
    for cue in cues:
        if cue["end"] <= start or cue["start"] >= end:
            continue
        s, e = max(0.0, cue["start"] - start), min(length, cue["end"] - start)
        if e - s < CUE_MIN_S:
            continue
        if cue["start"] < start - 0.25:
            notes.append(f"cue {cue['index']} starts {start - cue['start']:.1f} s before the cut: its first words "
                         "were said before the Short begins; trim its text")
        if cue["end"] > end + 0.25:
            notes.append(f"cue {cue['index']} runs {cue['end'] - end:.1f} s past the cut: trim its text")
        out.append({"index": len(out) + 1, "start": round(s, 3), "end": round(e, 3), "lines": list(cue["lines"])})
    return out, notes


def window_words(words: list, start: float, end: float) -> list:
    """Words spoken inside [start, end], re-timed to the cut (a word must start inside the window)."""
    out = []
    for w in words:
        if w["s"] >= start - 0.05 and w["s"] < end and w["e"] <= end + 0.3:
            out.append({"word": w["w"], "start": round(max(0.0, w["s"] - start), 3),
                        "end": round(min(end - start, w["e"] - start), 3)})
    return out


def words_to_cues(words: list) -> list:
    """Re-timed words -> caption cues of up to CAPTION_MAX_WORDS words or CAPTION_MAX_S seconds, broken after a
    sentence end. A cue stays up until the next one starts (or briefly after its last word)."""
    groups, current = [], []
    for w in words:
        current.append(w)
        long_enough = len(current) >= CAPTION_MAX_WORDS or current[-1]["end"] - current[0]["start"] >= CAPTION_MAX_S
        if long_enough or w["word"].rstrip("\"')").endswith((".", "?", "!")):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    cues = []
    for gi, group in enumerate(groups):
        start, end = group[0]["start"], group[-1]["end"] + CAPTION_TAIL_S
        if gi + 1 < len(groups):
            end = min(end, groups[gi + 1][0]["start"])
        end = max(end, group[-1]["end"])
        cues.append({"index": gi + 1, "start": round(start, 3), "end": round(end, 3),
                     "lines": [" ".join(w["word"] for w in group)]})
    return cues


def srt_stamp(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def srt_text(cues: list) -> str:
    blocks = [f"{i}\n{srt_stamp(c['start'])} --> {srt_stamp(c['end'])}\n" + "\n".join(c["lines"])
              for i, c in enumerate(cues, 1)]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def ass_stamp(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    return f"{cs // 360000}:{cs % 360000 // 6000:02d}:{cs % 6000 // 100:02d}.{cs % 100:02d}"


def ass_text(cues: list) -> str:
    """ASS captions for burning: PlayRes 1080x1920 (style values are real pixels), bottom-centre above the bottom
    UI zone, side margins that keep centred text out of the right-hand one. The style is in the file, so shorts_check
    can read the positions back."""
    events = []
    for c in cues:
        text = r"\N".join(line.replace("{", "(").replace("}", ")") for line in c["lines"])
        events.append(f"Dialogue: 0,{ass_stamp(c['start'])},{ass_stamp(c['end'])},Short,,0,0,0,,{text}")
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Short,Arial,{ASS_FONT_SIZE},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
            f"5,0,2,{ASS_MARGIN_LR},{ASS_MARGIN_LR},{ASS_MARGIN_V},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            + "\n".join(events) + ("\n" if events else ""))


# ----------------------------------------------------------------------------------------------- environment

def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise UsageError(f"{' and '.join(missing)} not found on PATH; install ffmpeg (https://ffmpeg.org) first")


def has_filter(name: str) -> bool:
    """True when this ffmpeg build has the filter (`subtitles` needs libass)."""
    try:
        proc = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return any(len(parts) >= 2 and parts[1] == name for parts in (ln.split() for ln in proc.stdout.splitlines()))


def load_candidate(path: Path, rank: int) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read {path.name}: {exc} (run shorts_pick.py first)") from exc
    for cand in (data.get("candidates") if isinstance(data, dict) else None) or []:
        if cand.get("rank") == rank:
            return cand
    raise UsageError(f"no candidate {rank} in {path.name}")


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: list | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Cut a Short from the long render and reframe it to 1080x1920. "
                               "Exit 0 written, 2 usage or environment error.")
    parser.add_argument("video", help="the long render (e.g. render/final.mp4); never overwritten")
    window = parser.add_mutually_exclusive_group(required=True)
    window.add_argument("--candidate", type=int, help="rank of a candidate in qa/shorts-candidates.json")
    window.add_argument("--start", type=float, help="cut start in seconds (with --end)")
    parser.add_argument("--end", type=float, help="cut end in seconds (with --start)")
    parser.add_argument("--candidates", help="candidates file (default <project>/qa/shorts-candidates.json)")
    parser.add_argument("--name", help="output name (letters, digits, '-', '_'); default: the candidate's name")
    parser.add_argument("--mode", choices=("crop", "fit"), default="fit",
                        help="crop: a 9:16 slice; fit (default): the whole picture on a blurred copy of itself")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--x", type=int, help="crop: left edge of the 9:16 slice in source pixels")
    where.add_argument("--center", action="store_true", help="crop: centre the slice (the default)")
    parser.add_argument("--fill", type=float, default=0.0,
                        help=f"fit: share of the source width trimmed so the picture grows (0-{FILL_MAX:g})")
    captions = parser.add_mutually_exclusive_group()
    captions.add_argument("--srt", help="the long video's caption file, re-timed to the cut")
    captions.add_argument("--words", help="word-timed transcript JSON: captions built from it (and the window's "
                                          "words written for shorts_check.py)")
    parser.add_argument("--words-offset", type=float, default=0.0,
                        help="where the narration starts inside the long video (for --words)")
    parser.add_argument("--burn", action="store_true", help="burn the captions in (needs ffmpeg with libass)")
    parser.add_argument("--out", help="output .mp4 path (default <project>/shorts/<name>.mp4)")
    parser.add_argument("--force", action="store_true", help="replace an existing Short of the same name")
    parser.add_argument("--crf", type=int, default=20, help="x264 quality, lower is better (default 20)")
    parser.add_argument("--preset", default="medium", help="x264 preset (default medium)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    try:
        if rc is None:
            raise UsageError("make-shorts needs the check-render skill of this plugin (render_check.py) next to it")
        source = Path(args.video)
        if not source.is_file():
            raise UsageError(f"video not found: {args.video}")
        project = find_project(source)
        if args.candidate is not None:
            if args.end is not None:
                raise UsageError("--candidate takes its window from the candidates file; drop --end")
            cand_path = Path(args.candidates) if args.candidates else project / "qa" / CANDIDATES
            cand = load_candidate(cand_path, args.candidate)
            start, end = float(cand["cut_start"]), float(cand["cut_end"])
            name = args.name or cand.get("name") or f"short-{args.candidate}"
        else:
            if args.end is None:
                raise UsageError("--start needs --end")
            start, end = args.start, args.end
            name = args.name
            if not name and not args.out:
                raise UsageError("give the Short a --name (or an --out path)")
        if args.mode != "crop" and (args.x is not None or args.center):
            raise UsageError("--x and --center belong to --mode crop")
        if args.mode != "fit" and args.fill:
            raise UsageError("--fill belongs to --mode fit")
        if not 0.0 <= args.fill <= FILL_MAX:
            raise UsageError(f"--fill must be between 0 and {FILL_MAX:g}")
        out = Path(args.out) if args.out else project / "shorts" / f"{name}.mp4"
        if out.suffix.lower() != ".mp4":
            raise UsageError("the output must be an .mp4 file")
        if not args.out and not _NAME_RE.fullmatch(name or ""):
            raise UsageError("--name may use letters, digits, '-' and '_' (up to 64 characters)")
        if args.burn and not _NAME_RE.fullmatch(out.stem):
            raise UsageError("--burn needs an output name of letters, digits, '-' and '_' (it goes into the filter)")
        if args.burn and not (args.srt or args.words):
            raise UsageError("--burn needs captions: pass --srt or --words")
        if out.resolve() == source.resolve():
            raise UsageError("refusing to overwrite the source video")
        if out.exists() and not args.force:
            raise UsageError(f"{rel_path(out, project)} exists; pass --force to replace it")
        if args.srt and not Path(args.srt).is_file():
            raise UsageError(f"caption file not found: {args.srt}")
        if args.words and not Path(args.words).is_file():
            raise UsageError(f"words file not found: {args.words}")
        require_tools()
        if args.burn and not has_filter("subtitles"):
            raise UsageError("this ffmpeg has no 'subtitles' filter (it is built without libass): upload the .srt as "
                             "a caption track instead, or install an ffmpeg build with libass and run again")
        info = probe_video(source)
        length = info.get("video_duration") or info.get("duration") or 0.0
        if not (0.0 <= start < end):
            raise UsageError(f"the window {start:g}-{end:g} s is empty or negative")
        if end > length + 0.05:
            raise UsageError(f"the window ends at {end:.2f} s but the video is {length:.2f} s long")
        end = min(end, length)
        duration = end - start
        if duration < MIN_CUT_S:
            raise UsageError(f"the window is {duration:.2f} s; a Short needs at least {MIN_CUT_S:g} s")
        if duration > SHORTS_MAX_S:
            raise UsageError(f"the window is {duration:.1f} s; YouTube's Shorts limit is {SHORTS_MAX_S:g} s "
                             "(support.google.com/youtube/answer/15424877)")
        width, height = info["display_width"], info["display_height"]
        if args.mode == "crop":
            graph = crop_filter(width, height, args.x)
        else:
            graph = fit_filter(width, height, args.fill)
        cues, notes, words_out = None, [], None
        if args.srt:
            try:
                cues, notes = retime_cues(rc.parse_srt(rc.read_subtitles(Path(args.srt))), start, end)
            except OSError as exc:
                raise UsageError(f"cannot read caption file: {exc}") from exc
        elif args.words:
            try:
                words = rc.load_words(Path(args.words), args.words_offset)
            except rc.UsageError as exc:
                raise UsageError(str(exc)) from exc
            words_out = window_words(words, start, end)
            cues = words_to_cues(words_out)
            notes.append("captions come from the transcript: read them and fix misheard words before publishing")
        if cues is not None and not cues:
            raise UsageError("no captions fall inside the window (wrong file, or a --words-offset is missing?)")
    except UsageError as exc:
        print(f"shorts_cut: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("shorts_cut: error: ffprobe timed out", file=sys.stderr)
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    srt_path = out.with_suffix(".srt") if cues else None
    ass_path = out.with_suffix(".ass") if cues and args.burn else None
    words_path = out.with_name(out.stem + ".words.json") if words_out is not None else None
    try:
        if srt_path:
            srt_path.write_text(srt_text(cues), encoding="utf-8")
        if ass_path:
            with open(ass_path, "w", encoding="utf-8", newline="\n") as handle:   # no write_text(newline=) on 3.9
                handle.write(ass_text(cues))
        if words_path:
            words_path.write_text(json.dumps({"words": words_out}, ensure_ascii=False, indent=1) + "\n",
                                  encoding="utf-8")
    except OSError as exc:
        print(f"shorts_cut: error: cannot write captions: {exc}", file=sys.stderr)
        return 2
    cmd = build_cmd(source.resolve(), out.resolve(), start, duration, graph, bool(info.get("has_audio")),
                    args.crf, args.preset, ass_path.name if ass_path else None)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=FFMPEG_TIMEOUT_S, stdin=subprocess.DEVNULL,
                              cwd=str(out.parent.resolve()))
    except subprocess.TimeoutExpired:
        print("shorts_cut: error: ffmpeg timed out", file=sys.stderr)
        return 2
    if proc.returncode != 0 or not out.is_file():
        print(f"shorts_cut: error: ffmpeg failed: {(proc.stderr or '').strip()[-400:]}", file=sys.stderr)
        return 2

    result = {
        "tool": TOOL, "version": VERSION,
        "video": rel_path(out, project), "source": rel_path(source, project),
        "start": round(start, 3), "end": round(end, 3), "duration": round(duration, 3),
        "mode": args.mode, "x": args.x, "fill": args.fill, "size": f"{OUT_W}x{OUT_H}",
        "captions": rel_path(srt_path, project) if srt_path else None,
        "burned": rel_path(ass_path, project) if ass_path else None,
        "words": rel_path(words_path, project) if words_path else None,
        "sha256": sha256_file(out), "notes": notes,
    }
    check_cmd = f"shorts_check.py {result['video']}"
    if srt_path:
        check_cmd += f" --srt {result['burned'] or result['captions']}" + (" --burned" if ass_path else "")
    if words_path:
        check_cmd += f" --words {result['words']}"
    result["next"] = check_cmd
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"shorts_cut: wrote {result['video']} ({duration:.1f} s, {OUT_W}x{OUT_H}, {args.mode}"
              + (", captions burned in" if ass_path else "") + ")")
        if srt_path:
            print(f"Captions: {result['captions']}")
        for note in notes:
            print(f"Note: {note}")
        print(f"Next: python {check_cmd}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
