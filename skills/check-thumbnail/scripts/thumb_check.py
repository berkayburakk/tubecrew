#!/usr/bin/env python3
"""thumb_check.py - the code gate for a thumbnail, or a side-by-side check of several candidates.

What it does
    Measures what code can measure about a YouTube thumbnail: the real file format (JPG or PNG, and that the
    bytes match the extension), 16:9 shape, resolution, file size, how much of the frame is near-black at feed
    size, a colour-clutter proxy, and, when the text lines on the thumbnail are known, the text rules (line
    count, line length, line balance, hard abbreviations) and whether the thumbnail repeats the title. It writes
    one preview image with every candidate at three feed sizes on YouTube's dark and light backgrounds, so the
    choice is made at the size viewers actually see.

Why
    Thumbnail candidates that look different at full size often look the same at feed size, and the reverse:
    a detailed background turns into mud, small text disappears, a black text column reads as an empty half.
    The mechanical part (format, size, empty area, too many lines) repeats from video to video and is cheap to
    measure; judging meaning and taste stays with the agent and the creator, looking at the preview.

How to run
    python thumb_check.py --project <video>                       the upload.json thumbnail, title and text
    python thumb_check.py a.jpg b.jpg c.png --title "..." --text "SOLD AT|THE TOP"
    python thumb_check.py a.jpg b.jpg --text "LINE ONE|LINE TWO" --text "OTHER|TEXT" --project <video>

    Text lines are separated by "|". One --text applies to every image; otherwise give one --text per image,
    in the same order. Without --text, upload.json's optional `thumbnail_text` (a list of lines) applies to the
    image that upload.json names as `thumbnail`. The title comes from --title or upload.json.

    The project folder is --project, else the folder of --upload-json, else the first image's folder (or its
    parent when only the parent holds upload.json). Results go to <project>/qa/thumbnail-check.json and
    <project>/qa/thumbnail-preview.png unless --out / --preview say otherwise. Paths in the report are relative
    to the project folder.

    Genre-dependent limits are flags: --dark-max, --clutter-max, --max-line-chars.

Exit codes
    0  every checked image passes (warnings may be listed)
    1  at least one checked image fails a check
    2  usage or environment error (no image, file missing, ffmpeg/ffprobe not on PATH, unreadable upload.json)

Requires ffmpeg and ffprobe on PATH. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

TOOL = "thumb_check.py"
VERSION = "0.1.0"

# ----------------------------------------------------------------------------------------------- limits
# Same value as upload-private's upload.py (THUMBNAIL_MAX_BYTES): thumbnails.set mediaUpload maxSize in the YouTube
# Data API discovery document. Keep the two in step.
THUMBNAIL_MAX_BYTES = 50 * 1024 * 1024
THUMBNAIL_TYPES = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}  # the types upload.py sends (image/jpeg, png)
CODEC_FORMATS = {"mjpeg": "JPEG", "png": "PNG", "webp": "WebP", "gif": "GIF", "bmp": "BMP", "tiff": "TIFF",
                 "av1": "AVIF", "hevc": "HEIC", "jpeg2000": "JPEG 2000", "jpegxl": "JPEG XL"}

TARGET_ASPECT = 16 / 9
ASPECT_PASS_TOL = 0.01     # 1920x1088 or 1280x718 are 16:9 for every practical purpose
ASPECT_FAIL_TOL = 0.05     # beyond 5% off, YouTube crops the picture or adds bars around it
MIN_WIDTH = 640            # YouTube's documented minimum thumbnail width
RECOMMENDED = (1280, 720)  # YouTube's recommended thumbnail size

FEED_WIDTHS = (210, 320, 630)  # roughly: sidebar suggestion / phone, desktop home grid, a large row or search
STATS_WIDTH = 210              # measured at the smallest feed size, where empty space and clutter cost the most
DARK_LUMA = 25                 # luma (0-255) below 25 reads as black at feed size
# More than half the frame near-black means half the thumbnail is empty. Calibrated on real thumbnails: designs
# with a scene behind the text measured 17-39%, a layout with a plain black text column 50-57%. Dark genres
# (night, space, horror) can raise it with --dark-max.
DARK_MAX = 0.50
COLOR_LEVELS = 4               # 4 levels per channel = 64 coarse colours, so JPEG noise and gradients merge
#                                (a power of two: pixel_stats() quantizes with a bit shift)
CLUSTER_MIN_SHARE = 0.01       # a coarse colour counts as a cluster when it covers at least 1% of the frame
# A proxy for "too many separate elements", not a verdict. Calibrated on about forty real thumbnails at 210 px:
# clean single-subject designs scored 5-12, typical creator thumbnails up to 14, logo-and-panel collages 18-22.
# Rich photographic styles can raise it with --clutter-max.
CLUTTER_MAX = 14

MAX_LINES = 3                  # more lines than this turns into texture at feed size
MAX_LINE_CHARS = 12            # with a heavy display font, a 12-character line still reads at 210 px wide
LINE_RATIO_MAX = 2.0           # the longest line sets the font size; a 3.5x spread made the short line vanish
CONTENT_WORD_MIN = 4           # shorter words (the, why, how, $4M) carry no repetition signal

# Number + a unit abbreviation that viewers do not decode at a glance ($6BN, 4MN, 2TN). K, M and B stay allowed:
# they are common enough to read in a feed.
_UNIT_ABBREV_RE = re.compile(r"[$€£¥]?\d(?:[\d.,]*\d)?\s?(BN|BLN|MN|MLN|MM|TN|TRN|MRD|MIO|BIL|THOU)"
                             r"(?![A-Za-z])", re.I)
_WORD_ABBREV_RE = re.compile(r"(?<![\w/])(YOY|QOQ|YTD|MGMT|GOVT|GOV'T|INTL|DEPT|ACCT|AMT|QTY|APPROX|EST|W/O|W/|B/C)"
                             r"(?![\w/])", re.I)
_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*")
STOPWORDS = frozenset("""
about above after again against also because been before being below between both could does doing down during each
even ever every from further have having here into just like made make many more most much must only other over same
should some such than that their them then there these they this those through under until very were what when where
which while will with would your yours
""".split())

PREVIEW_PAD = 12                      # space around each tile in the preview, px
PREVIEW_BACKGROUNDS = (("dark", "0x0f0f0f"), ("light", "0xffffff"))  # YouTube's dark and light page colours
FFMPEG_TIMEOUT_S = 120

PASS, WARN, FAIL, INFO = "PASS", "WARN", "FAIL", "INFO"


class UsageError(Exception):
    """Raised for exit-code-2 problems (missing file, missing tool, unreadable upload.json)."""


# ----------------------------------------------------------------------------------------------- helpers

def check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def rel_path(path: Path, base: Path) -> str:
    """Path relative to base in POSIX form; never absolute (falls back to the file name)."""
    try:
        rel = os.path.relpath(str(Path(path).resolve()), str(Path(base).resolve()))
    except ValueError:  # different drive on Windows
        return Path(path).name
    if os.path.isabs(rel) or rel == ".." or rel.startswith(".." + os.sep) or rel.startswith("../"):
        return Path(path).name  # outside the project: keep only the name, never parent folder names
    return Path(rel).as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(cmd: list, binary: bool = False) -> tuple:
    if binary:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S)
        return proc.returncode, proc.stdout or b"", (proc.stderr or b"").decode("utf-8", "replace")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=FFMPEG_TIMEOUT_S)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def split_lines(value) -> list:
    """'A|B' or ['A', 'B'] -> clean list of non-empty lines."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[|\n]", value)
    elif isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        parts = list(value)
    else:
        raise UsageError("thumbnail_text must be a list of strings (one per line on the thumbnail)")
    return [p.strip() for p in parts if p.strip()]


# ----------------------------------------------------------------------------------------------- pure checks

def pixel_stats(raw: bytes) -> dict:
    """rgb24 bytes -> {"dark_ratio", "clusters"}. Luma uses the ITU-R 601 weights (as most image libraries)."""
    n = len(raw) // 3
    if n == 0:
        return {"dark_ratio": None, "clusters": None}
    shift = 8 - (COLOR_LEVELS.bit_length() - 1)  # 4 levels -> keep the top 2 bits of each channel
    dark = 0
    counts: dict = {}
    for i in range(0, n * 3, 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        if r * 299 + g * 587 + b * 114 < DARK_LUMA * 1000:
            dark += 1
        key = (r >> shift, g >> shift, b >> shift)
        counts[key] = counts.get(key, 0) + 1
    min_count = CLUSTER_MIN_SHARE * n
    return {"dark_ratio": round(dark / n, 3), "clusters": sum(1 for c in counts.values() if c >= min_count)}


def evaluate_image(info: dict, dark_max: float = DARK_MAX, clutter_max: int = CLUTTER_MAX) -> list:
    """File and picture facts -> checks. Pure.

    `info` keys: ext, codec, width, height, pix_fmt, size, dark_ratio, clusters (None when not measured),
    decode_error (str or None).
    """
    out = []
    ext = (info.get("ext") or "").lower()
    real = CODEC_FORMATS.get(info.get("codec") or "", (info.get("codec") or "unknown").upper())
    if info.get("decode_error"):
        return [check("format", FAIL, f"not a readable image: {info['decode_error'][:160]}")]
    if ext not in THUMBNAIL_TYPES:
        out.append(check("format", FAIL, f"{ext or 'no extension'} ({real}): YouTube uploads here take JPG or PNG; "
                                         "save it as .jpg"))
    elif THUMBNAIL_TYPES[ext] != real:
        out.append(check("format", FAIL, f"the file is {real} data named {ext}: the upload sends the type from the "
                                         f"extension, so re-save it as a real {THUMBNAIL_TYPES[ext]}"))
    else:
        out.append(check("format", PASS, real))
    pix_fmt = info.get("pix_fmt") or ""
    if pix_fmt.startswith(("rgba", "bgra", "argb", "abgr", "ya", "gbrap", "yuva")):
        out.append(check("transparency", WARN, f"the image has an alpha channel ({pix_fmt}): flatten it onto a solid "
                                               "background, YouTube fills transparent pixels with its own colour"))

    width, height = info.get("width") or 0, info.get("height") or 0
    if width and height:
        off = abs((width / height) / TARGET_ASPECT - 1.0)
        if off <= ASPECT_PASS_TOL:
            out.append(check("aspect", PASS, f"16:9 ({width}x{height})"))
        elif off <= ASPECT_FAIL_TOL:
            out.append(check("aspect", WARN, f"{width}x{height} is {off:.0%} off 16:9: the edges get cropped"))
        else:
            out.append(check("aspect", FAIL, f"{width}x{height} is not 16:9 ({width / height:.2f}:1): YouTube crops it "
                                             "or adds bars; make it 1280x720"))
        if width < MIN_WIDTH:
            out.append(check("resolution", FAIL, f"{width}x{height}: YouTube needs at least {MIN_WIDTH} px wide"))
        elif width < RECOMMENDED[0] or height < RECOMMENDED[1]:
            out.append(check("resolution", WARN, f"{width}x{height}: below the recommended "
                                                 f"{RECOMMENDED[0]}x{RECOMMENDED[1]}, soft on large screens"))
        else:
            out.append(check("resolution", PASS, f"{width}x{height}"))

    size = info.get("size") or 0
    if size > THUMBNAIL_MAX_BYTES:
        out.append(check("file_size", FAIL, f"{size / 1024 / 1024:.1f} MB: the YouTube API allows "
                                            f"{THUMBNAIL_MAX_BYTES // 1024 // 1024} MB; save it as JPEG"))
    else:
        out.append(check("file_size", PASS, f"{size // 1024} KB"))

    dark = info.get("dark_ratio")
    if dark is not None:
        if dark > dark_max:
            out.append(check("dark_area", WARN, f"{dark:.0%} of the frame is near-black at feed size (warn above "
                                                f"{dark_max:.0%}): that part of the thumbnail reads as empty"))
        else:
            out.append(check("dark_area", PASS, f"{dark:.0%} near-black at feed size"))
    clusters = info.get("clusters")
    if clusters is not None:
        if clusters > clutter_max:
            out.append(check("clutter", WARN, f"{clusters} colour clusters at feed size (warn above {clutter_max}): "
                                              "many separate elements, none dominant; check the preview"))
        else:
            out.append(check("clutter", PASS, f"{clusters} colour clusters at feed size"))
    return out


def find_abbreviations(lines: list) -> list:
    found = []
    for line in lines:
        for m in _UNIT_ABBREV_RE.finditer(line):
            found.append(m.group(0).strip())
        for m in _WORD_ABBREV_RE.finditer(line):
            found.append(m.group(0))
    return found


def _stem(word: str) -> str:
    word = word.replace("’", "'")
    if word.endswith("'s"):
        word = word[:-2]
    if len(word) > CONTENT_WORD_MIN:
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("sses"):
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss"):
            return word[:-1]
    return word


def content_words(text: str) -> dict:
    """Content words (4+ characters, not a function word) -> {stem: first spelling seen}."""
    out: dict = {}
    for token in _WORD_RE.findall(text.lower().replace("’", "'")):
        if len(token) < CONTENT_WORD_MIN or token in STOPWORDS:
            continue
        out.setdefault(_stem(token), token)
    return out


def shared_words(title: str, text: str) -> list:
    """Content words the title and the thumbnail text have in common, in thumbnail order."""
    in_title = content_words(title)
    return [word for stem, word in content_words(text).items() if stem in in_title]


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("’", "'")).strip()


def _words_within(text: str, title: str) -> bool:
    """True when the text's words appear in the title as a whole-word run ("ART" is not inside "Startups")."""
    words = re.findall(r"[\w']+", _normalized(text))
    pool = re.findall(r"[\w']+", _normalized(title))
    n = len(words)
    return n > 0 and any(pool[i:i + n] == words for i in range(len(pool) - n + 1))


def evaluate_text(lines: list, title: str | None = None, max_line_chars: int = MAX_LINE_CHARS) -> list:
    """Text lines on the thumbnail (and the title) -> checks. Pure."""
    lines = [ln.strip() for ln in lines if ln and ln.strip()]
    if not lines:
        return [check("text", INFO, "no thumbnail text given (upload.json thumbnail_text or --text): text rules "
                                    "skipped")]
    out = []
    if len(lines) > MAX_LINES:
        out.append(check("text_lines", WARN, f"{len(lines)} lines (warn above {MAX_LINES}): at feed size more lines "
                                             "turn into texture"))
    else:
        out.append(check("text_lines", PASS, f"{len(lines)} line(s)"))
    long_lines = [ln for ln in lines if len(ln) > max_line_chars]
    if long_lines:
        out.append(check("text_length", WARN, f"{len(long_lines[0])} characters in {long_lines[0]!r} (warn above "
                                              f"{max_line_chars}): a long line shrinks the font of every line"))
    else:
        out.append(check("text_length", PASS, f"longest line {max(len(ln) for ln in lines)} characters"))
    if len(lines) >= 2:
        longest, shortest = max(lines, key=len), min(lines, key=len)
        ratio = len(longest) / max(len(shortest), 1)
        if ratio > LINE_RATIO_MAX:
            out.append(check("text_balance", WARN, f"the longest line is {ratio:.1f}x the shortest ({longest!r} vs "
                                                   f"{shortest!r}, warn above {LINE_RATIO_MAX:g}x): the font size is "
                                                   "set by the longest line, so the short line gets small"))
        else:
            out.append(check("text_balance", PASS, f"longest/shortest {ratio:.1f}x"))
    abbrevs = find_abbreviations(lines)
    if abbrevs:
        out.append(check("abbreviation", WARN, f"{', '.join(abbrevs)}: viewers do not decode this at a glance; write "
                                               "the word or use K / M / B"))
    if title and title.strip():
        joined = " ".join(lines)
        shared = shared_words(title, joined)
        if _words_within(joined, title):
            out.append(check("title_overlap", WARN, "the thumbnail text appears word for word in the title: the "
                                                    "thumbnail repeats the title instead of adding to it"))
        elif shared:
            out.append(check("title_overlap", WARN, f"the thumbnail repeats the title ({', '.join(shared)}): the two "
                                                    "should say different things"))
        else:
            out.append(check("title_overlap", PASS, "no content word shared with the title"))
    return out


def verdict_of(checks: list) -> str:
    return FAIL if any(c["status"] == FAIL for c in checks) else PASS


# ----------------------------------------------------------------------------------------------- measuring

def require_tools() -> None:
    missing = [t for t in ("ffmpeg", "ffprobe") if shutil.which(t) is None]
    if missing:
        raise UsageError(f"{' and '.join(missing)} not found on PATH; install ffmpeg (https://ffmpeg.org) first")


def measure(path: Path) -> dict:
    """ffprobe facts + one ffmpeg decode at feed size -> info dict for evaluate_image()."""
    info = {"ext": path.suffix.lower(), "size": path.stat().st_size, "codec": None, "width": None, "height": None,
            "pix_fmt": None, "dark_ratio": None, "clusters": None, "decode_error": None}
    code, out, err = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                           "stream=codec_name,width,height,pix_fmt", "-of", "json", str(path)])
    streams = []
    if code == 0:
        try:
            streams = json.loads(out).get("streams") or []
        except ValueError:
            streams = []
    if not streams:
        info["decode_error"] = (err.strip().splitlines() or ["ffprobe found no picture in the file"])[-1]
        return info
    s = streams[0]
    info.update(codec=s.get("codec_name"), width=s.get("width"), height=s.get("height"), pix_fmt=s.get("pix_fmt"))
    code, raw, err = _run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-vf",
                           f"scale={STATS_WIDTH}:-1:flags=area,format=rgb24", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-"], binary=True)
    if code != 0 or not raw:
        info["decode_error"] = (err.strip().splitlines() or [f"ffmpeg exited with {code}"])[-1]
        return info
    info.update(pixel_stats(raw))
    return info


def build_preview_cmd(images: list, out: Path) -> list:
    """One ffmpeg call: every image at each feed width; all rows on dark, then all rows on light."""
    tile_h = round(max(FEED_WIDTHS) * 9 / 16) + 2 * PREVIEW_PAD
    copies = len(FEED_WIDTHS) * len(PREVIEW_BACKGROUNDS)
    parts, rows = [], []
    for i in range(len(images)):
        parts.append(f"[{i}:v]format=rgb24,split={copies}" + "".join(f"[c{i}_{k}]" for k in range(copies)))
    for b, (bg_name, color) in enumerate(PREVIEW_BACKGROUNDS):
        for i in range(len(images)):
            tiles = []
            for w_i, width in enumerate(FEED_WIDTHS):
                k = b * len(FEED_WIDTHS) + w_i
                height = round(width * 9 / 16)
                label = f"t{i}_{bg_name}_{width}"
                parts.append(f"[c{i}_{k}]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                             f"pad={width + 2 * PREVIEW_PAD}:{tile_h}:{PREVIEW_PAD}:{PREVIEW_PAD}:color={color},"
                             f"format=rgb24[{label}]")
                tiles.append(f"[{label}]")
            parts.append("".join(tiles) + f"hstack=inputs={len(tiles)}[r{i}_{bg_name}]")
            rows.append(f"[r{i}_{bg_name}]")
    parts.append("".join(rows) + f"vstack=inputs={len(rows)}[out]")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for img in images:
        cmd += ["-i", str(img)]
    return cmd + ["-filter_complex", ";".join(parts), "-map", "[out]", "-frames:v", "1", "-update", "1", str(out)]


def make_preview(images: list, out: Path) -> str | None:
    """Write the preview; returns an error message or None."""
    if not images:
        return "no readable image to preview"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        code, _, err = _run(build_preview_cmd(images, out))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if code != 0 or not out.is_file():
        return (err.strip().splitlines() or [f"ffmpeg exited with {code}"])[-1]
    return None


# ----------------------------------------------------------------------------------------------- inputs

def load_upload_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{path.name} is not a JSON object")
    return data


def resolve_inputs(args) -> tuple:
    """-> (project, meta, images, texts, title, meta_thumb). texts holds one list of lines per image; meta_thumb is
    the resolved upload.json thumbnail path or None."""
    images = [Path(p) for p in args.images]
    for img in images:
        if not img.is_file():
            raise UsageError(f"image not found: {img}")
    if args.upload_json:
        upload_json = Path(args.upload_json)
        if not upload_json.is_file():
            raise UsageError(f"upload.json not found: {args.upload_json}")
        project = upload_json.resolve().parent
    elif args.project:
        project = Path(args.project).resolve()
        if not project.is_dir():
            raise UsageError(f"project folder not found: {args.project}")
        upload_json = project / "upload.json"
    elif images:
        folder = images[0].resolve().parent
        project = folder.parent if not (folder / "upload.json").is_file() and (
            folder.parent / "upload.json").is_file() else folder
        upload_json = project / "upload.json"
    else:
        raise UsageError("give at least one image, or --project / --upload-json with a thumbnail in upload.json")
    meta = load_upload_json(upload_json) if upload_json.is_file() else {}

    meta_thumb = None
    if meta.get("thumbnail"):
        if not isinstance(meta["thumbnail"], str) or Path(meta["thumbnail"]).is_absolute():
            raise UsageError("upload.json thumbnail must be a path relative to the project")
        meta_thumb = (project / meta["thumbnail"]).resolve()
    if not images:
        if meta_thumb is None:
            raise UsageError("no image given and upload.json sets no thumbnail")
        if not meta_thumb.is_file():
            raise UsageError(f"thumbnail not found: {meta['thumbnail']}")
        images = [meta_thumb]

    if args.text:
        if len(args.text) not in (1, len(images)):
            raise UsageError(f"{len(args.text)} --text values for {len(images)} images: give one for all, or one "
                             "per image in the same order")
        texts = [split_lines(t) for t in (args.text * len(images) if len(args.text) == 1 else args.text)]
    else:
        meta_text = split_lines(meta.get("thumbnail_text"))
        texts = [meta_text if (meta_thumb is not None and img.resolve() == meta_thumb) else [] for img in images]
    title = args.title if args.title is not None else meta.get("title")
    if title is not None and not isinstance(title, str):
        raise UsageError("upload.json title must be a string")
    return project, meta, images, texts, title, meta_thumb


# ----------------------------------------------------------------------------------------------- output

def _cell(x: dict | None, cand: dict) -> str:
    """Short table cell for one check of one candidate: status + the measured value."""
    if x is None:
        return "-"
    m, text = cand.get("measured") or {}, cand.get("text") or []
    w, h = cand.get("width") or 0, cand.get("height") or 0
    name = x["name"]
    if name == "format":
        value = cand.get("format") or "?"
    elif name == "aspect":
        value = "16:9" if x["status"] == PASS else (f"{w / h:.2f}:1" if h else "?")
    elif name == "resolution":
        value = f"{w}x{h}"
    elif name == "file_size":
        value = f"{(cand.get('size') or 0) // 1024} KB"
    elif name == "dark_area" and m.get("dark_ratio") is not None:
        value = f"{m['dark_ratio']:.0%} dark"
    elif name == "clutter":
        value = f"{m.get('clusters')} clusters"
    elif name == "text_lines":
        value = f"{len(text)} lines"
    elif name == "text_length" and text:
        value = f"{max(len(t) for t in text)} chars"
    elif name == "text_balance" and text:
        value = f"{len(max(text, key=len)) / max(len(min(text, key=len)), 1):.1f}x"
    elif name == "title_overlap":
        value = "repeats title" if x["status"] == WARN else "different"
    elif name == "abbreviation":
        value = x["detail"].split(": ")[0]
    elif name == "text":
        value = "skipped"
    else:
        value = name
    return f"{x['status']} {value}"[:22]


def human_lines(report: dict, report_rel: str) -> list:
    cands = report["candidates"]
    if len(cands) == 1:
        c = cands[0]
        lines = [f"thumb_check: {c['image']}"]
        width = max([len(x["name"]) for x in c["checks"]] + [4])
        lines += [f"{x['status']:<4}  {x['name']:<{width}}  {x['detail']}" for x in c["checks"]]
    else:
        names = [Path(c["image"]).name[:22] for c in cands]
        order = []
        for c in cands:
            for x in c["checks"]:
                if x["name"] not in order:
                    order.append(x["name"])
        lines = [f"thumb_check: {len(cands)} candidates side by side"]
        lines.append(f"{'check':<14}" + "".join(f"  {n:<22}" for n in names))
        for name in order:
            row = [next((x for x in c["checks"] if x["name"] == name), None) for c in cands]
            lines.append(f"{name:<14}" + "".join(f"  {_cell(x, c):<22}" for x, c in zip(row, cands)))
        lines.append(f"{'verdict':<14}" + "".join(f"  {c['verdict']:<22}" for c in cands))
        details = [(n, x) for n, c in zip(names, cands) for x in c["checks"] if x["status"] in (WARN, FAIL)]
        if details:
            lines.append("")
            lines += [f"{n}: {x['status']} {x['name']}: {x['detail']}" for n, x in details]
    if report.get("preview"):
        order = ", ".join(Path(c["image"]).name for c in cands if c.get("in_preview"))
        lines.append(f"Preview: {report['preview']}  (rows: {order} on dark, then the same on light; columns "
                     f"{', '.join(str(w) for w in FEED_WIDTHS)} px wide). Judge at these sizes, not full size.")
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
        prog=TOOL, description="Code gate for YouTube thumbnails: format, 16:9, size, empty area, clutter, text rules "
                               "and title overlap, plus a feed-size preview. Exit 0 pass, 1 fail, 2 usage error.")
    parser.add_argument("images", nargs="*", help="thumbnail image(s); default: upload.json's thumbnail")
    parser.add_argument("--project", help="the video project folder (reads upload.json, writes qa/)")
    parser.add_argument("--upload-json", help="an upload.json to read (its folder is the project)")
    parser.add_argument("--title", help="the video title (default: upload.json title)")
    parser.add_argument("--text", action="append", help="text lines on the thumbnail, separated by '|'; once for all "
                                                        "images or once per image")
    parser.add_argument("--dark-max", type=float, default=DARK_MAX,
                        help=f"warn when more than this share of the frame is near-black (default {DARK_MAX:g})")
    parser.add_argument("--clutter-max", type=int, default=CLUTTER_MAX,
                        help=f"warn above this many colour clusters (default {CLUTTER_MAX})")
    parser.add_argument("--max-line-chars", type=int, default=MAX_LINE_CHARS,
                        help=f"warn when a text line is longer than this (default {MAX_LINE_CHARS})")
    parser.add_argument("--preview", help="preview path (default <project>/qa/thumbnail-preview.png)")
    parser.add_argument("--no-preview", action="store_true", help="skip the preview image")
    parser.add_argument("--out", help="report path (default <project>/qa/thumbnail-check.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        if not 0 < args.dark_max <= 1:
            raise UsageError("--dark-max must be between 0 and 1")
        if args.clutter_max < 1 or args.max_line_chars < 1:
            raise UsageError("--clutter-max and --max-line-chars must be positive")
        project, _meta, images, texts, title, meta_thumb = resolve_inputs(args)
        require_tools()
        infos = [measure(img) for img in images]
    except UsageError as exc:
        print(f"thumb_check: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("thumb_check: error: ffmpeg timed out", file=sys.stderr)
        return 2

    candidates = []
    for img, info, lines in zip(images, infos, texts):
        checks = evaluate_image(info, dark_max=args.dark_max, clutter_max=args.clutter_max)
        if not info.get("decode_error"):
            checks += evaluate_text(lines, title, max_line_chars=args.max_line_chars)
        candidates.append({
            "image": rel_path(img, project), "sha256": sha256_file(img), "size": info["size"],
            "format": CODEC_FORMATS.get(info.get("codec") or "", info.get("codec")), "width": info.get("width"),
            "height": info.get("height"), "text": lines,
            "measured": {"feed_width": STATS_WIDTH, "dark_ratio": info.get("dark_ratio"),
                         "clusters": info.get("clusters")},
            "verdict": verdict_of(checks), "checks": checks, "in_preview": not info.get("decode_error")})

    preview_rel = None
    if not args.no_preview:
        preview = Path(args.preview) if args.preview else project / "qa" / "thumbnail-preview.png"
        readable = [img for img, c in zip(images, candidates) if c["in_preview"]]
        error = make_preview(readable, preview)
        if error:
            print(f"thumb_check: warning: preview not written: {error[-200:]}", file=sys.stderr)
        else:
            preview_rel = rel_path(preview, project)

    chosen = next((c for img, c in zip(images, candidates) if meta_thumb is not None and img.resolve() == meta_thumb),
                  None)
    report = {
        "tool": TOOL,
        "version": VERSION,
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": FAIL if any(c["verdict"] == FAIL for c in candidates) else PASS,
        "thumbnail": chosen["image"] if chosen else None,
        "sha256": chosen["sha256"] if chosen else None,
        "title": title,
        "preview": preview_rel,
        "settings": {"dark_max": args.dark_max, "clutter_max": args.clutter_max,
                     "max_line_chars": args.max_line_chars},
        "candidates": candidates,
    }

    out_path = Path(args.out) if args.out else project / "qa" / "thumbnail-check.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"thumb_check: error: cannot write report: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(report, rel_path(out_path, project))))
    return 1 if report["verdict"] == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
