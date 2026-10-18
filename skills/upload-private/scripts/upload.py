#!/usr/bin/env python3
"""Upload a finished video to YouTube as PRIVATE, and only after every gate passes for the exact file.

What it does: reads `<project>/upload.json` and the render in `<project>/render/`, then checks the metadata against
YouTube's limits (title, description bytes, tag weight, chapters), the thumbnail file, a duplicate upload, and that
the code render check and both independent reviews (`qa/render-check.json`, `qa/RENDER-AUDIT-*.md`,
`qa/UPLOAD-GATE-*.md`) approved these exact bytes (SHA-256). The default is a dry run: no network, nothing is
uploaded. With `--upload` it confirms that the token belongs to `upload.json`'s `channel_id`, uploads resumably in
8 MB chunks, sets the thumbnail and logs the upload in `qa/upload-log.json`.

Why private only: a private upload can be checked, fixed or removed quietly; a public one cannot be taken back
unseen (subscriber notifications, feeds and the first hours of data are spent at once). Publishing, scheduling and
deleting are the creator's decisions in YouTube Studio, so this script has no option for any of them: the request
body always says `privacyStatus: "private"`, and it never sends a publish time or a delete request.

Run:
    python upload.py <project>                                   dry run: gate table, no network
    python upload.py <project> --upload --profile mychannel      upload as private
    python upload.py --login --profile mychannel                 one-time browser login (the only time one opens)

Credentials never live in the project: `~/.tubecrew/client_secret.json` (your own Google Cloud OAuth client) and
`~/.tubecrew/tokens/<profile>-upload.json`; set `TUBECREW_HOME` to use another folder. See
`../references/oauth-setup.md`. Needs `pip install google-auth google-auth-oauthlib requests` for --upload and
--login only; the dry run is standard library (plus `ffprobe` on PATH for the chapter end check).

Exit codes: 0 every gate passes (dry run) or the upload finished; 1 a gate failed, the token belongs to another
channel or the upload failed; 2 usage or environment error (missing package, token, client file or argument).
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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

TITLE_MAX = 100
TITLE_MOBILE = 60
DESCRIPTION_MAX_BYTES = 5000
TAG_WEIGHT_MAX = 500
THUMBNAIL_MAX_BYTES = 50 * 1024 * 1024  # thumbnails.set mediaUpload maxSize (52428800) in the API discovery document
THUMBNAIL_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
CHAPTER_MIN = 3
CHAPTER_GAP_S = 10
VERDICT_LINES = 10
SKIP_REASON_MIN = 12
CHUNK_BYTES = 8 * 1024 * 1024  # a multiple of 256 KiB, as the resumable protocol requires
MAX_RETRIES = 5
RETRY_STATUS = {500, 502, 503, 504}
DEFAULT_CATEGORY = "22"

UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
                 "https://www.googleapis.com/auth/youtube.readonly"]
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMBNAIL_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
PIP_HINT = "pip install google-auth google-auth-oauthlib requests"

UPLOAD_JSON = "upload.json"
RENDER_CHECK = "render-check.json"
UPLOAD_LOG = "upload-log.json"
KNOWN_FIELDS = {"title", "description", "tags", "category_id", "default_language", "default_audio_language",
                "made_for_kids", "synthetic_media", "channel_id", "thumbnail"}

_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_PROFILE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_CHAPTER = re.compile(r"^\s*(?:[-*•]\s+)?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+(\S.*)$")
_VERDICT_NAME = re.compile(r"^(?P<kind>RENDER-AUDIT|UPLOAD-GATE)-(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<n>\d+))?\.md$")
_VERDICT_LINE = re.compile(r"^[\s#>*_`]*VERDICT[\s*_`]*:(?P<rest>.*)$", re.I)
_HASH_LINE = re.compile(r"^[\s#>*_`-]*(?P<label>Video|Metadata|Thumbnail)[\s*_`]*:\s*(?P<path>.*?)\s*sha256\s*[:=]\s*`?"
                        r"(?P<sha>[0-9a-fA-F]{12,64})", re.I)


class EnvError(Exception):
    """Usage or environment problem (exit 2): the message says what to run or install."""


class UploadError(RuntimeError):
    """The resumable upload could not finish."""


@dataclass
class Check:
    gate: str
    status: str  # PASS | WARN | FAIL
    detail: str


@dataclass
class Plan:
    project: Path
    upload_mode: bool
    meta: Optional[dict] = None
    meta_sha: Optional[str] = None
    video: Optional[Path] = None
    video_sha: Optional[str] = None
    duration: Optional[float] = None
    thumbnail: Optional[Path] = None
    thumb_sha: Optional[str] = None
    skip_reason: Optional[str] = None
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    @property
    def failed(self) -> int:
        return sum(c.status == "FAIL" for c in self.checks)


# --- pure validators ----------------------------------------------------------------------------------------------

def tag_weight(tags: list) -> int:
    """YouTube's 500-character tag limit: a tag with a space counts its two quotes, plus one comma between tags."""
    if not tags:
        return 0
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + len(tags) - 1


def validate_title(title) -> tuple:
    """(errors, warnings) for the title."""
    if not isinstance(title, str) or not title.strip():
        return ["title is missing or empty"], []
    errors, warnings = [], []
    if len(title) > TITLE_MAX:
        errors.append(f"title is {len(title)} characters (YouTube allows {TITLE_MAX})")
    if re.search(r"[<>]", title):
        errors.append("title contains < or > (YouTube rejects them)")
    if len(title) > TITLE_MOBILE:
        warnings.append(f"title is {len(title)} characters and may be cut on mobile and in search after the first "
                        f"{TITLE_MOBILE}: \"{title[:TITLE_MOBILE]}\"")
    return errors, warnings


def validate_description(description) -> tuple:
    """(errors, warnings) for the description: at most 5000 UTF-8 bytes, no angle brackets."""
    if description is None:
        description = ""
    if not isinstance(description, str):
        return ["description must be a string"], []
    errors, warnings = [], []
    size = len(description.encode("utf-8"))
    if size > DESCRIPTION_MAX_BYTES:
        errors.append(f"description is {size} UTF-8 bytes (YouTube allows {DESCRIPTION_MAX_BYTES})")
    if re.search(r"[<>]", description):
        errors.append("description contains < or > (YouTube rejects them)")
    if not description.strip():
        warnings.append("description is empty: the first two lines are what viewers and search see")
    return errors, warnings


def validate_tags(tags) -> tuple:
    """(errors, warnings): a list of non-empty strings with a weight of at most 500."""
    if tags is None:
        tags = []
    if not isinstance(tags, list):
        return ["tags must be a list of strings"], []
    if not all(isinstance(t, str) and t.strip() for t in tags):
        return ["every tag must be a non-empty string"], []
    errors, warnings = [], []
    weight = tag_weight(tags)
    if weight > TAG_WEIGHT_MAX:
        errors.append(f"tag weight is {weight} (YouTube allows {TAG_WEIGHT_MAX}; a tag with a space counts +2, "
                      "plus one per comma)")
    if not tags:
        warnings.append("no tags")
    elif len({t.strip().casefold() for t in tags}) != len(tags):
        warnings.append("some tags repeat")
    return errors, warnings


def parse_chapters(description) -> tuple:
    """Timestamp lines (`0:00 Intro`, `1:02:03 Q&A`) -> ([(seconds, title)], format errors)."""
    chapters, errors = [], []
    if not isinstance(description, str):
        return chapters, errors
    for line in description.splitlines():
        m = _CHAPTER.match(line)
        if not m:
            continue
        hours, minutes, seconds = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        if seconds >= 60 or (m.group(1) is not None and minutes >= 60):
            errors.append(f"invalid timestamp in line: {line.strip()[:60]}")
            continue
        chapters.append((hours * 3600 + minutes * 60 + seconds, m.group(4).strip()))
    return chapters, errors


def validate_chapters(chapters: list, duration: Optional[float]) -> tuple:
    """YouTube's chapter rules -> (errors, warnings). No chapters means nothing to check."""
    if not chapters:
        return [], []
    errors, warnings = [], []
    starts = [c[0] for c in chapters]
    if len(starts) < CHAPTER_MIN:
        errors.append(f"{len(starts)} chapters (YouTube needs at least {CHAPTER_MIN})")
    if starts[0] != 0:
        errors.append(f"first chapter starts at {fmt_time(starts[0])}, not 0:00")
    for a, b in zip(starts, starts[1:]):
        if b <= a:
            errors.append(f"chapter {fmt_time(b)} does not come after {fmt_time(a)}")
        elif b - a < CHAPTER_GAP_S:
            errors.append(f"chapters {fmt_time(a)} and {fmt_time(b)} are less than {CHAPTER_GAP_S} s apart")
    if duration is None:
        warnings.append("video duration unknown (ffprobe not on PATH or could not read the file): the check that "
                        f"the last chapter lasts at least {CHAPTER_GAP_S} s was skipped")
    elif starts[-1] > duration - CHAPTER_GAP_S:
        errors.append(f"last chapter starts at {fmt_time(starts[-1])}, less than {CHAPTER_GAP_S} s before the end "
                      f"of the video ({fmt_time(duration)})")
    return errors, warnings


def validate_metadata(meta: dict, upload_mode: bool = False) -> tuple:
    """(errors, warnings) for every upload.json field. Pure: does not look at files."""
    if not isinstance(meta, dict):
        return ["upload.json is not a JSON object"], []
    errors, warnings = [], []
    for part in (validate_title(meta.get("title")), validate_description(meta.get("description")),
                 validate_tags(meta.get("tags"))):
        errors += part[0]
        warnings += part[1]
    for key, what in (("made_for_kids", "YouTube's made-for-kids setting"),
                      ("synthetic_media", "YouTube's altered or synthetic content disclosure")):
        if not isinstance(meta.get(key), bool):
            errors.append(f"{key} must be true or false ({what}); there is no default, decide it")
    category = meta.get("category_id", DEFAULT_CATEGORY)
    if isinstance(category, bool) or not str(category).isdigit():
        errors.append(f"category_id {category!r} is not a number (for example \"22\" or \"27\")")
    for key in ("default_language", "default_audio_language"):
        value = meta.get(key)
        if value is not None and (not isinstance(value, str) or not _LANGUAGE.fullmatch(value)):
            errors.append(f"{key} {value!r} is not a language code (en, de, pt-BR ...)")
    if meta.get("default_language") is None:
        warnings.append("default_language not set: YouTube will not know the language of the title")
    channel = meta.get("channel_id")
    if channel is None or channel == "":
        if upload_mode:
            errors.append("channel_id missing: a real upload needs it to confirm the target channel")
        else:
            warnings.append("channel_id missing: --upload will refuse until it is set")
    elif not isinstance(channel, str) or not _CHANNEL_ID.fullmatch(channel):
        errors.append(f"channel_id {channel!r} is not a channel id (UC + 22 characters)")
    unknown = sorted(set(meta) - KNOWN_FIELDS)
    if unknown:
        warnings.append(f"unknown field(s) ignored: {', '.join(unknown)} (uploads are always private; publish and "
                        "schedule in YouTube Studio)")
    return errors, warnings


# --- verdict files ------------------------------------------------------------------------------------------------

def verdict_key(name: str) -> Optional[tuple]:
    """`RENDER-AUDIT-2026-10-18-2.md` -> ("RENDER-AUDIT", "2026-10-18", 2); no suffix counts as 1."""
    m = _VERDICT_NAME.match(name)
    if not m:
        return None
    return m.group("kind"), m.group("date"), int(m.group("n") or 1)


def newest_verdict(qa_dir: Path, kind: str) -> Optional[Path]:
    """Newest `<kind>-<date>[-N].md`: by the date in the name, then by the -N suffix (an explicit `-1` is newer than
    the file without a suffix)."""
    if not qa_dir.is_dir():
        return None
    found = []
    for p in qa_dir.glob(f"{kind}-*.md"):
        key = verdict_key(p.name)
        if key and key[0] == kind and p.is_file():
            found.append(((key[1], key[2], p.name != f"{kind}-{key[1]}.md", p.name), p))
    return max(found)[1] if found else None


def parse_verdict(text: str) -> dict:
    """Reads the fixed lines in the first 10 lines: the first VERDICT line and the Video/Metadata/Thumbnail hashes."""
    out = {"verdict": None, "video_path": None, "video_sha": None, "metadata_sha": None, "thumbnail_sha": None}
    for line in text.splitlines()[:VERDICT_LINES]:
        if out["verdict"] is None:
            m = _VERDICT_LINE.match(line)
            if m:
                out["verdict"] = re.sub(r"[*_`]", "", m.group("rest")).strip().upper()
                continue
        m = _HASH_LINE.match(line)
        if m:
            key = m.group("label").lower()
            if out[f"{key}_sha"] is None:
                out[f"{key}_sha"] = m.group("sha").lower()[:12]
                if key == "video":
                    out["video_path"] = m.group("path").strip("` ")
    return out


def _read_text(path: Path) -> str:
    """UTF-8 with or without a BOM, or UTF-16 with a BOM (what Windows PowerShell 5.1's `>` writes)."""
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    return data.decode("utf-8-sig", errors="replace")


def check_render_json(qa_dir: Path, video_sha: str) -> tuple:
    """(ok, detail) for `qa/render-check.json`: verdict PASS and the full sha256 of this video."""
    path = qa_dir / RENDER_CHECK
    if not path.is_file():
        return False, f"qa/{RENDER_CHECK} missing: run the check-render script on this video"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False, f"qa/{RENDER_CHECK} is not valid JSON"
    if not isinstance(data, dict):
        return False, f"qa/{RENDER_CHECK} is not a JSON object"
    if data.get("verdict") != "PASS":
        return False, f"qa/{RENDER_CHECK} verdict is {data.get('verdict')!r}, not \"PASS\""
    measured = str(data.get("sha256") or "").lower()
    if measured != video_sha:
        return False, (f"qa/{RENDER_CHECK} measured sha256:{measured[:12] or '?'} but the video is "
                       f"sha256:{video_sha[:12]}: re-run the render check on the current file")
    return True, f"qa/{RENDER_CHECK} PASS for sha256:{video_sha[:12]}"


def check_review(qa_dir: Path, kind: str, video_sha: str, meta_sha: Optional[str] = None,
                 thumb_sha: Optional[str] = None) -> tuple:
    """(ok, detail) for the newest RENDER-AUDIT (expects PASS) or UPLOAD-GATE (expects GO, the metadata hash and,
    when upload.json sets a thumbnail, the thumbnail hash)."""
    expected = "PASS" if kind == "RENDER-AUDIT" else "GO"
    agent = "render-auditor" if kind == "RENDER-AUDIT" else "upload-checker"
    path = newest_verdict(qa_dir, kind)
    if path is None:
        return False, f"no qa/{kind}-<YYYY-MM-DD>.md: run the {agent} agent"
    v = parse_verdict(_read_text(path))
    if v["verdict"] is None:
        return False, f"{path.name}: no VERDICT line in the first {VERDICT_LINES} lines"
    if v["verdict"] != expected:
        return False, f"{path.name}: verdict is {v['verdict']!r}; the line must be exactly 'VERDICT: {expected}'"
    if v["video_sha"] is None:
        return False, f"{path.name}: no 'Video: <path> sha256:<12 hex>' line in the first {VERDICT_LINES} lines"
    if v["video_sha"] != video_sha[:12]:
        return False, (f"{path.name} reviewed sha256:{v['video_sha']} but the video is sha256:{video_sha[:12]}: "
                       f"the {agent} must review the current file")
    if kind == "UPLOAD-GATE":
        if v["metadata_sha"] is None:
            return False, (f"{path.name}: no 'Metadata: upload.json sha256:<12 hex>' line in the first "
                           f"{VERDICT_LINES} lines")
        if v["metadata_sha"] != (meta_sha or "")[:12]:
            return False, (f"{path.name} reviewed upload.json sha256:{v['metadata_sha']} but it is now "
                           f"sha256:{(meta_sha or '')[:12]}: upload.json changed after the review")
        if thumb_sha and v["thumbnail_sha"] is None:
            return False, (f"{path.name}: upload.json sets a thumbnail but the review has no "
                           f"'Thumbnail: <path> sha256:<12 hex>' line in the first {VERDICT_LINES} lines")
        if thumb_sha and v["thumbnail_sha"] != thumb_sha[:12]:
            return False, (f"{path.name} reviewed thumbnail sha256:{v['thumbnail_sha']} but it is now "
                           f"sha256:{thumb_sha[:12]}: the thumbnail changed after the review")
    return True, f"{path.name}: {expected} for sha256:{video_sha[:12]}"


def rejects_this_file(qa_dir: Path, kind: str, video_sha: str, meta_sha: Optional[str] = None) -> bool:
    """True when the newest review of this kind says FAIL or NO-GO for these exact bytes (and, for UPLOAD-GATE, this
    upload.json). --skip-review stands in for a missing or outdated review, never for a rejection."""
    path = newest_verdict(qa_dir, kind)
    if path is None:
        return False
    v = parse_verdict(_read_text(path))
    if v["verdict"] not in ("FAIL", "NO-GO") or v["video_sha"] != video_sha[:12]:
        return False
    return kind != "UPLOAD-GATE" or v["metadata_sha"] == (meta_sha or "")[:12]


# --- files --------------------------------------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def probe_duration(path: Path) -> Optional[float]:
    """Video duration in seconds via ffprobe, or None when ffprobe is missing or cannot read the file."""
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                            str(path)], capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        value = float(r.stdout.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    return value if value > 0 else None


def rel(path: Path, project: Path) -> str:
    """Project-relative POSIX path; a file outside the project shows its name only (logs never hold full paths)."""
    try:
        return path.resolve().relative_to(project.resolve()).as_posix()
    except ValueError:
        return path.name


def find_video(project: Path) -> tuple:
    """(video, checks): the single or newest .mp4 in render/."""
    render = project / "render"
    mp4s = sorted((p for p in render.glob("*.mp4") if p.is_file()), key=lambda p: (p.stat().st_mtime, p.name)) \
        if render.is_dir() else []
    if not mp4s:
        return None, [Check("video", "FAIL", "no .mp4 in render/ (or pass --video)")]
    checks = []
    if len(mp4s) > 1:
        checks.append(Check("video", "WARN", f"{len(mp4s)} .mp4 files in render/, using the newest "
                                             f"({mp4s[-1].name}); pass --video to choose"))
    return mp4s[-1], checks


def check_thumbnail(project: Path, value) -> tuple:
    """(path or None, Check) for upload.json's optional thumbnail."""
    if value is None or value == "":
        return None, Check("thumbnail", "PASS", "none set (add one in YouTube Studio)")
    if not isinstance(value, str):
        return None, Check("thumbnail", "FAIL", "thumbnail must be a path relative to the project")
    if Path(value).is_absolute():
        return None, Check("thumbnail", "FAIL", f"thumbnail {value!r} must be relative to the project")
    root = project.resolve()
    path = (root / value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None, Check("thumbnail", "FAIL", f"thumbnail {value!r} points outside the project")
    if not path.is_file():
        return None, Check("thumbnail", "FAIL", f"thumbnail not found: {value}")
    if path.suffix.lower() not in THUMBNAIL_TYPES:
        return None, Check("thumbnail", "FAIL", f"thumbnail is {path.suffix or 'without extension'} "
                                                "(YouTube takes .jpg, .jpeg or .png)")
    size = path.stat().st_size
    if size > THUMBNAIL_MAX_BYTES:
        return None, Check("thumbnail", "FAIL", f"thumbnail is {size / 1024 / 1024:.1f} MB (the YouTube API allows "
                                                f"{THUMBNAIL_MAX_BYTES // 1024 // 1024} MB; save it as JPEG)")
    return path, Check("thumbnail", "PASS", f"{rel(path, project)}, {size // 1024} KB")


def read_upload_log(path: Path) -> list:
    """Entries of `qa/upload-log.json` ([] if it does not exist). An unreadable log raises ValueError: it is never
    treated as "no uploads" and never overwritten."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = data.get("uploads") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("qa/upload-log.json has no 'uploads' list")
    return [e for e in entries if isinstance(e, dict)]


def find_duplicate(entries: list, video_sha: str) -> Optional[dict]:
    for entry in entries:
        if entry.get("sha256") == video_sha and entry.get("video_id"):
            return entry
    return None


def write_upload_log(path: Path, entries: list) -> None:
    """Writes the whole log atomically (temporary file, then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"uploads": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# --- the plan: everything read from disk, no network --------------------------------------------------------------

def build_plan(project: Path, video: Optional[Path] = None, upload_mode: bool = False,
               skip_reason: Optional[str] = None, again: bool = False) -> Plan:
    plan = Plan(project=project, upload_mode=upload_mode, skip_reason=skip_reason)
    add = plan.checks.append

    if video is None:
        plan.video, video_checks = find_video(project)
        plan.checks += video_checks
    else:
        plan.video = video
    if plan.video is not None:
        plan.video_sha = sha256_file(plan.video)
        plan.duration = probe_duration(plan.video)
        size_mb = plan.video.stat().st_size / 1e6
        length = fmt_time(plan.duration) if plan.duration else "unknown"
        add(Check("video", "PASS", f"{rel(plan.video, project)}, {size_mb:.1f} MB, duration {length}"))

    meta_path = project / UPLOAD_JSON
    if not meta_path.is_file():
        add(Check("metadata", "FAIL", f"{UPLOAD_JSON} missing in the project"))
    else:
        plan.meta_sha = sha256_file(meta_path)
        try:
            plan.meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            add(Check("metadata", "FAIL", f"{UPLOAD_JSON} is not valid UTF-8 JSON"))
    meta = plan.meta if isinstance(plan.meta, dict) else None
    if plan.meta is not None and meta is None:
        add(Check("metadata", "FAIL", f"{UPLOAD_JSON} is not a JSON object"))

    if meta is not None:
        errors, warnings = validate_metadata(meta, upload_mode)
        for e in errors:
            add(Check("metadata", "FAIL", e))
        for w in warnings:
            add(Check("metadata", "WARN", w))
        if not errors:
            tags = meta.get("tags") or []
            desc = meta.get("description") or ""
            add(Check("metadata", "PASS", f"title {len(meta['title'])} chars, description "
                                          f"{len(desc.encode('utf-8'))} bytes, {len(tags)} tags (weight "
                                          f"{tag_weight(tags)}/{TAG_WEIGHT_MAX})"))
        chapters, chapter_format = parse_chapters(meta.get("description"))
        if chapters or chapter_format:
            c_errors, c_warnings = validate_chapters(chapters, plan.duration)
            for e in chapter_format + c_errors:
                add(Check("chapters", "FAIL", e))
            for w in c_warnings:
                add(Check("chapters", "WARN", w))
            if not chapter_format and not c_errors:
                add(Check("chapters", "PASS", f"{len(chapters)} chapters: "
                                              + " ".join(fmt_time(c[0]) for c in chapters)))
        else:
            add(Check("chapters", "PASS", "no timestamp lines in the description (no chapters)"))
        plan.thumbnail, thumb_check = check_thumbnail(project, meta.get("thumbnail"))
        add(thumb_check)
        if plan.thumbnail:
            plan.thumb_sha = sha256_file(plan.thumbnail)

    qa = project / "qa"
    if plan.video_sha is None:
        for gate in ("render check", "render audit", "upload gate", "duplicate"):
            add(Check(gate, "FAIL", "no video to bind the verdict to"))
        return plan

    ok, detail = check_render_json(qa, plan.video_sha)
    add(Check("render check", "PASS" if ok else "FAIL", detail))
    for gate, kind in (("render audit", "RENDER-AUDIT"), ("upload gate", "UPLOAD-GATE")):
        ok, detail = check_review(qa, kind, plan.video_sha, plan.meta_sha, plan.thumb_sha)
        if ok:
            add(Check(gate, "PASS", detail))
        elif skip_reason and not rejects_this_file(qa, kind, plan.video_sha, plan.meta_sha):
            add(Check(gate, "WARN", f"skipped by --skip-review ({skip_reason}); otherwise: {detail}"))
        elif skip_reason:
            add(Check(gate, "FAIL", f"{detail} (--skip-review does not override a rejection of these exact files)"))
        else:
            add(Check(gate, "FAIL", detail))

    try:
        previous = find_duplicate(read_upload_log(qa / UPLOAD_LOG), plan.video_sha)
    except (OSError, ValueError) as e:
        add(Check("duplicate", "FAIL", f"qa/{UPLOAD_LOG} is unreadable ({e}); fix it by hand, this script never "
                                       "overwrites it"))
    else:
        if previous is None:
            add(Check("duplicate", "PASS", "this video was not uploaded before"))
        else:
            when = previous.get("uploaded_at", "earlier")
            if again:
                add(Check("duplicate", "WARN", f"already uploaded as {previous['video_id']} ({when}); --again "
                                               "uploads a second copy"))
            else:
                add(Check("duplicate", "FAIL", f"already uploaded as {previous['video_id']} ({when}); YouTube "
                                               "never replaces a video file. Pass --again for a second copy"))
    return plan


def build_body(meta: dict) -> dict:
    """`videos.insert` body. Private is a constant here; nothing in upload.json or on the command line changes it."""
    snippet = {"title": meta["title"], "description": meta.get("description") or "",
               "categoryId": str(meta.get("category_id", DEFAULT_CATEGORY))}
    if meta.get("tags"):
        snippet["tags"] = list(meta["tags"])
    if meta.get("default_language"):
        snippet["defaultLanguage"] = meta["default_language"]
    if meta.get("default_audio_language"):
        snippet["defaultAudioLanguage"] = meta["default_audio_language"]
    body = {"snippet": snippet,
            "status": {"privacyStatus": "private",
                       "selfDeclaredMadeForKids": bool(meta["made_for_kids"]),
                       "containsSyntheticMedia": bool(meta["synthetic_media"])}}
    assert_private(body)
    return body


def assert_private(body: dict) -> None:
    status = body.get("status") or {}
    if status.get("privacyStatus") != "private" or "publishAt" in status:
        raise RuntimeError(f"refusing to upload: the request body is not private ({status!r})")


# --- network (only with --upload or --login) ----------------------------------------------------------------------

def tubecrew_home() -> Path:
    env = os.environ.get("TUBECREW_HOME")
    return Path(env).expanduser() if env else Path.home() / ".tubecrew"


def token_path(profile: str) -> Path:
    return tubecrew_home() / "tokens" / f"{profile}-upload.json"


def login_command(profile: str) -> str:
    script = sys.argv[0] if sys.argv and sys.argv[0].endswith(".py") else "upload.py"
    return f'python "{script}" --login --profile {profile}'


def _google_auth():
    try:
        from google.auth.exceptions import GoogleAuthError, RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        import requests  # noqa: F401  (google.auth.transport.requests needs it too)
    except ImportError as e:
        raise EnvError(f"missing Python package ({e.name}): {PIP_HINT}") from e
    return Credentials, Request, RefreshError, GoogleAuthError


def new_session():
    try:
        import requests
    except ImportError as e:
        raise EnvError(f"missing Python package (requests): {PIP_HINT}") from e
    return requests.Session()


def load_upload_credentials(path: Path, login_cmd: str):
    """Loads and refreshes the profile's upload token. Missing, unreadable or revoked -> EnvError (exit 2)."""
    Credentials, Request, RefreshError, GoogleAuthError = _google_auth()
    if not path.is_file():
        raise EnvError(f"no upload token for this profile ({path.name}). Log in once: {login_cmd}")
    try:
        cred = Credentials.from_authorized_user_file(str(path), UPLOAD_SCOPES)
    except (OSError, ValueError) as e:
        raise EnvError(f"token file {path.name} is unreadable ({type(e).__name__}). Log in again: {login_cmd}") from e
    if not cred.refresh_token:
        if cred.valid:
            return cred
        raise EnvError(f"token {path.name} has expired and cannot be refreshed. Log in again: {login_cmd}")
    try:
        cred.refresh(Request())
    except RefreshError as e:
        why = ("the login was revoked or has expired (invalid_grant; in Testing mode Google expires it after "
               "about 7 days)" if "invalid_grant" in str(e) else f"refreshing the token failed ({str(e)[:160]})")
        raise EnvError(f"{why}. Log in again: {login_cmd}") from e
    except GoogleAuthError as e:
        raise EnvError(f"could not reach Google to refresh the token ({type(e).__name__}); check the connection "
                       "and try again") from e
    try:
        path.write_text(cred.to_json(), encoding="utf-8")
    except OSError:
        pass
    return cred


def _refresh_if_needed(cred) -> None:
    """Long uploads can outlive the one-hour access token; thumbnails.set needs a live one."""
    expiry = getattr(cred, "expiry", None)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)  # google-auth keeps expiry as naive UTC
    soon = expiry is not None and expiry - now < dt.timedelta(minutes=5)
    if getattr(cred, "refresh_token", None) and (soon or not getattr(cred, "valid", True)):
        _, Request, _, _ = _google_auth()
        cred.refresh(Request())


def _header(response, name: str) -> Optional[str]:
    for key, value in (getattr(response, "headers", None) or {}).items():
        if key.lower() == name.lower():
            return value
    return None


def _text(response) -> str:
    try:
        return str(response.text)[:300]
    except Exception:  # noqa: BLE001 - an error body is only for the message
        return ""


def channel_ids(session, token: str) -> list:
    """Channel(s) the token can upload to: channels.list?part=id&mine=true."""
    r = session.get(f"{API}/channels", params={"part": "id", "mine": "true"},
                    headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code != 200:
        raise UploadError(f"channels.list returned HTTP {r.status_code}: {_text(r)}")
    return [str(item.get("id", "")) for item in (r.json().get("items") or [])]


def _received_bytes(response) -> int:
    """Bytes the server holds, from the 308 `Range: bytes=0-N` header; no header means none."""
    m = re.search(r"(\d+)\s*-\s*(\d+)", _header(response, "Range") or "")
    return int(m.group(2)) + 1 if m else 0


def resumable_upload(session, token: str, video: Path, body: dict, chunk_size: int = CHUNK_BYTES,
                     sleep: Callable = time.sleep, progress: Optional[Callable] = None) -> dict:
    """YouTube resumable upload: POST the metadata, then PUT chunks with Content-Range.

    A 308 answer carries `Range` (what the server holds); the next chunk starts right after it. A 5xx answer or a
    connection error waits (exponential backoff) and asks the server where it stands with `Content-Range:
    bytes */<size>`, then resumes from there. Gives up after MAX_RETRIES failures in a row.
    """
    assert_private(body)
    size = video.stat().st_size
    if size == 0:
        raise UploadError("the video file is empty")
    failures = 0

    def fail(why: str) -> None:
        nonlocal failures
        failures += 1
        if failures > MAX_RETRIES:
            raise UploadError(f"gave up after {MAX_RETRIES} retries ({why})")
        sleep(min(2 ** (failures - 1), 32))

    init_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)}
    session_url = None
    while session_url is None:
        try:
            r = session.post(UPLOAD_URL, params={"uploadType": "resumable", "part": "snippet,status"},
                             headers=init_headers, json=body, timeout=60)
        except OSError as e:
            fail(f"starting the upload: {type(e).__name__}")
            continue
        if r.status_code in RETRY_STATUS:
            fail(f"starting the upload: HTTP {r.status_code}")
            continue
        if r.status_code != 200:
            raise UploadError(f"starting the upload failed: HTTP {r.status_code}: {_text(r)}")
        session_url = _header(r, "Location")
        if not session_url:
            raise UploadError("starting the upload failed: no Location header in the answer")
    failures = 0

    offset, ask = 0, False
    with video.open("rb") as f:
        while True:
            if ask:
                headers, data = {"Content-Range": f"bytes */{size}"}, b""
            else:
                f.seek(offset)
                data = f.read(chunk_size)
                headers = {"Content-Type": "video/mp4",
                           "Content-Range": f"bytes {offset}-{offset + len(data) - 1}/{size}"}
            was_query = ask
            try:
                r = session.put(session_url, headers=headers, data=data, timeout=(30, 300))
            except OSError as e:
                fail(type(e).__name__)
                ask = True
                continue
            code = r.status_code
            if code in (200, 201):
                if progress:
                    progress(size, size)
                return r.json()
            if code == 308:
                received = _received_bytes(r)
                if received > offset:
                    failures = 0
                elif not was_query:
                    fail("the server kept none of the last chunk")
                offset = received
                if progress:
                    progress(offset, size)
                ask = offset >= size
                if ask and was_query:
                    fail("the server holds every byte but has not answered with the video")
                continue
            if code in (404, 410):
                raise UploadError(f"the upload session expired (HTTP {code}); run the command again")
            if code not in RETRY_STATUS:
                raise UploadError(f"HTTP {code}: {_text(r)}")
            fail(f"HTTP {code}")
            ask = True


def set_thumbnail(session, token: str, video_id: str, path: Path) -> tuple:
    """thumbnails.set -> (HTTP status or None, short message). Never raises: the thumbnail is not the upload."""
    try:
        r = session.post(THUMBNAIL_URL, params={"videoId": video_id},
                         headers={"Authorization": f"Bearer {token}",
                                  "Content-Type": THUMBNAIL_TYPES[path.suffix.lower()]},
                         data=path.read_bytes(), timeout=120)
        return r.status_code, _text(r)
    except Exception as e:  # noqa: BLE001 - a thumbnail failure never fails the upload
        return None, type(e).__name__


# --- output -------------------------------------------------------------------------------------------------------

class Out:
    """Human lines go to stdout, or to stderr when --json keeps stdout for the JSON result."""

    def __init__(self, as_json: bool):
        self.stream = sys.stderr if as_json else sys.stdout

    def __call__(self, line: str = "") -> None:
        print(line, file=self.stream, flush=True)


def print_plan(plan: Plan, say: Out) -> None:
    mode = "UPLOAD (private only)" if plan.upload_mode else "DRY RUN (no network; nothing is uploaded)"
    say(f"upload.py - {mode}")
    say(f"project: {plan.project.name}")
    say("")
    width = max([len(c.gate) for c in plan.checks] + [4])
    say(f"{'GATE'.ljust(width)}  RESULT  DETAIL")
    for c in plan.checks:
        say(f"{c.gate.ljust(width)}  {c.status.ljust(6)}  {c.detail}")
    if plan.video_sha:
        say("")
        say("Lines for the reviewers (first lines of their verdict files):")
        say(f"  Video: {rel(plan.video, plan.project)} sha256:{plan.video_sha[:12]}")
        if plan.meta_sha:
            say(f"  Metadata: {UPLOAD_JSON} sha256:{plan.meta_sha[:12]}")
        if plan.thumbnail and plan.thumb_sha:
            say(f"  Thumbnail: {rel(plan.thumbnail, plan.project)} sha256:{plan.thumb_sha[:12]}")
    say("")


def plan_json(plan: Plan) -> dict:
    return {"project": plan.project.name,
            "mode": "upload" if plan.upload_mode else "dry-run",
            "video": rel(plan.video, plan.project) if plan.video else None,
            "sha256": plan.video_sha,
            "metadata_sha256": plan.meta_sha,
            "thumbnail_sha256": plan.thumb_sha,
            "duration_s": plan.duration,
            "ok": plan.ok,
            "checks": [{"gate": c.gate, "status": c.status, "detail": c.detail} for c in plan.checks]}


# --- commands -----------------------------------------------------------------------------------------------------

def do_upload(plan: Plan, profile: str, say: Out) -> tuple:
    """Network part of --upload; every local gate already passed. Returns (exit code, result dict)."""
    meta, login_cmd = plan.meta, login_command(profile)
    cred = load_upload_credentials(token_path(profile), login_cmd)
    session = new_session()
    try:
        ids = channel_ids(session, cred.token)
    except (UploadError, OSError, ValueError) as e:
        say(f"UPLOAD REFUSED - could not read the token's channel: {e}. Nothing was uploaded.")
        return 1, {"uploaded": False, "error": str(e)[:300]}
    if ids != [meta["channel_id"]]:
        say(f"UPLOAD REFUSED - WRONG CHANNEL: this token belongs to {', '.join(ids) or 'no channel'}, upload.json "
            f"says {meta['channel_id']}. Nothing was uploaded. Log in with the right Google account: {login_cmd}")
        return 1, {"uploaded": False, "error": "wrong channel", "token_channels": ids}
    say(f"channel: {meta['channel_id']} confirmed")

    body = build_body(meta)
    size = plan.video.stat().st_size
    say(f"UPLOADING as PRIVATE: {rel(plan.video, plan.project)} ({size / 1e6:.1f} MB)")
    shown = {"pct": -1}

    def progress(done: int, total: int) -> None:
        pct = int(done * 100 / total)
        if pct >= shown["pct"] + 10 or done == total:
            shown["pct"] = pct
            say(f"  {pct}%")

    try:
        answer = resumable_upload(session, cred.token, plan.video, body, progress=progress)
    except (UploadError, OSError, ValueError) as e:
        say(f"UPLOAD FAILED: {e}\nNothing was logged. If the last chunk went through, the video may exist anyway: "
            "look in YouTube Studio before running the command again.")
        return 1, {"uploaded": False, "error": str(e)[:300]}
    video_id = answer.get("id") if isinstance(answer, dict) else None
    if not video_id:
        say(f"UPLOAD FAILED: the answer has no video id: {str(answer)[:300]}")
        return 1, {"uploaded": False, "error": "no video id"}
    privacy = (answer.get("status") or {}).get("privacyStatus")
    say(f"UPLOADED: {video_id} (privacy: {privacy or 'not in the answer'})")

    entry = {"video_id": video_id, "video": rel(plan.video, plan.project), "sha256": plan.video_sha,
             "uploaded_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
             "privacy": "private", "channel_id": meta["channel_id"], "title": meta["title"]}
    if plan.skip_reason:
        entry["skip_review_reason"] = plan.skip_reason
    if privacy and privacy != "private":
        entry["alarm"] = f"YouTube answered privacyStatus {privacy!r}"
    log_path = plan.project / "qa" / UPLOAD_LOG
    entries = None
    try:
        entries = read_upload_log(log_path) + [entry]
        write_upload_log(log_path, entries)
    except (OSError, ValueError) as e:
        say(f"WARNING: qa/{UPLOAD_LOG} could not be written ({e}). Add this entry by hand:\n"
            + json.dumps(entry, ensure_ascii=False))
        entries = None

    if plan.thumbnail:
        try:
            _refresh_if_needed(cred)
        except Exception as e:  # noqa: BLE001 - the thumbnail step is best effort
            say(f"WARNING: token refresh before the thumbnail failed ({type(e).__name__})")
        status, message = set_thumbnail(session, cred.token, video_id, plan.thumbnail)
        entry["thumbnail_http"] = status
        if status is not None and 200 <= status < 300:
            say(f"thumbnail: set (HTTP {status})")
        else:
            hint = " (the channel may need phone verification for custom thumbnails)" if status == 403 else ""
            say(f"WARNING: thumbnail not set (HTTP {status}){hint}: {message[:160]}. The video is uploaded; add "
                "the thumbnail in YouTube Studio.")
        if entries is not None:
            try:
                write_upload_log(log_path, entries)
            except OSError:
                pass

    result = {"uploaded": True, "video_id": video_id, "privacy": privacy, "thumbnail_http": entry.get("thumbnail_http"),
              "studio_url": f"https://studio.youtube.com/video/{video_id}/edit"}
    if privacy and privacy != "private":
        say(f"ALARM: YouTube answered privacyStatus {privacy!r}, not private. Open YouTube Studio now and check it.")
        return 1, result
    say(f"https://studio.youtube.com/video/{video_id}/edit")
    say("Left for you in Studio: end screen, cards, audio tracks, then publish or schedule.")
    return 0, result


def do_login(profile: str, say: Out) -> int:
    """Browser login for the profile's upload token. The only path that opens a browser."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as e:
        raise EnvError(f"missing Python package ({e.name}): {PIP_HINT}") from e
    _google_auth()
    secret = tubecrew_home() / "client_secret.json"
    if not secret.is_file():
        raise EnvError(f"no OAuth client file at {secret}. Create a Desktop app OAuth client in Google Cloud and "
                       "save its JSON there (see references/oauth-setup.md).")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), scopes=UPLOAD_SCOPES)
    cred = flow.run_local_server(port=0)
    path = token_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cred.to_json(), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    say(f"Saved the upload token for profile '{profile}' ({path.name}).")
    try:
        r = new_session().get(f"{API}/channels", params={"part": "id,snippet", "mine": "true"},
                              headers={"Authorization": f"Bearer {cred.token}"}, timeout=60)
        items = (r.json().get("items") or []) if r.status_code == 200 else []
    except Exception as e:  # noqa: BLE001 - the token is saved; the channel name is a courtesy
        say(f"Could not read the channel name ({type(e).__name__}).")
        return 0
    if not items:
        say("This Google account has no YouTube channel (or the token cannot see it).")
    for item in items:
        say(f"Channel: {(item.get('snippet') or {}).get('title', '?')} ({item.get('id')}) - put this id in "
            "upload.json as channel_id")
    return 0


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    ap = argparse.ArgumentParser(description="Gate and upload a finished video to YouTube as PRIVATE.")
    ap.add_argument("project", nargs="?", type=Path, help="the video project folder (with upload.json, render/, qa/)")
    ap.add_argument("--video", help="the video file (default: the single or newest .mp4 in render/)")
    ap.add_argument("--upload", action="store_true", help="really upload, as private (default: dry run, no network)")
    ap.add_argument("--profile", default="default", help="credential profile, one per channel (default: default)")
    ap.add_argument("--login", action="store_true", help="open the browser once to authorize this profile")
    ap.add_argument("--skip-review", metavar="REASON",
                    help="proceed without matching reviewer verdicts (WARN instead of FAIL); the reason is logged")
    ap.add_argument("--again", action="store_true", help="allow a second upload of a video already in the log")
    ap.add_argument("--json", action="store_true", help="print a machine-readable result")
    a = ap.parse_args(argv)
    say = Out(a.json)

    def usage(message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return 2

    if not _PROFILE.fullmatch(a.profile):
        return usage("--profile may only use letters, digits, '.', '_' and '-'")
    try:
        if a.login:
            return do_login(a.profile, say)
        if a.project is None:
            return usage("give the project folder (or --login)")
        project = a.project.expanduser().resolve()
        if not project.is_dir():
            return usage(f"project folder not found: {a.project}")
        skip = None
        if a.skip_review is not None:
            skip = a.skip_review.strip()
            if len(skip) < SKIP_REASON_MIN:
                return usage(f"--skip-review needs a reason of at least {SKIP_REASON_MIN} characters")
        video = None
        if a.video:
            video = Path(a.video).expanduser()
            if not video.is_absolute():
                video = project / video if (project / video).is_file() else video.resolve()
            if not video.is_file():
                return usage(f"video not found: {a.video}")

        plan = build_plan(project, video, a.upload, skip, a.again)
        print_plan(plan, say)
        result = plan_json(plan)
        if not a.upload:
            say("RESULT: every gate passes." if plan.ok else
                f"RESULT: {plan.failed} gate line(s) FAIL - --upload would be refused.")
            say("DRY RUN - nothing was uploaded. Add --upload to upload as PRIVATE.")
            code = 0 if plan.ok else 1
        elif not plan.ok:
            say(f"UPLOAD REFUSED - {plan.failed} gate line(s) FAIL (above). Nothing was uploaded.")
            code = 1
        else:
            code, upload_result = do_upload(plan, a.profile, say)
            result.update(upload_result)
    except EnvError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        if a.json:
            print(json.dumps({"ok": False, "error": str(e)}))
        return 2
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
