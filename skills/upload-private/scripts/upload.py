#!/usr/bin/env python3
"""Upload a finished video to YouTube as PRIVATE, and only after every gate passes for the exact file.

What it does: reads `<project>/upload.json` and the render in `<project>/render/`, then checks the metadata against
YouTube's limits (title, description bytes, tag weight, chapters, translated titles and descriptions), the thumbnail
file, a duplicate upload, and that the code render check and both independent reviews (`qa/render-check.json`,
`qa/RENDER-AUDIT-*.md`, `qa/UPLOAD-GATE-*.md`) approved these exact bytes (SHA-256). The default is a dry run: no
network, nothing is uploaded. With `--upload` it takes a lock in `qa/` so a second run cannot upload the same video at
the same time, confirms that the token belongs to `upload.json`'s `channel_id`, refuses when the channel's latest
uploads already hold this exact title (an earlier run whose answer got lost may have uploaded it), uploads resumably
in 8 MB chunks, writes the video id to `qa/upload-log.json` at once, and only then sets the thumbnail.

Why private only: a private upload can be checked, fixed or removed quietly; a public one cannot be taken back
unseen (subscriber notifications, feeds and the first hours of data are spent at once). The request body always says
`privacyStatus: "private"`. Scheduling is opt-in: only when the creator writes `publish_at` in upload.json AND passes
`--schedule` does the body also carry `status.publishAt`. The video then stays private until YouTube publishes it at
that time, and the script reads the status back to prove it. There is no option to publish now or to delete.

Run:
    python upload.py <project>                                    dry run: gate table, no network
    python upload.py <project> --upload --profile mychannel       upload as private
    python upload.py <project> --upload --schedule --profile mychannel
                                                                  private now, published by YouTube at publish_at
    python upload.py <project> --login --profile mychannel        one-time browser login (the only time one opens);
                                                                  with a project, a login for another channel is
                                                                  refused before it is saved
    python upload.py <project> --check-token --profile mychannel  is the login alive, which channel, when it expires

Credentials never live in the project: `~/.tubecrew/client_secret.json` (your own Google Cloud OAuth client),
`~/.tubecrew/tokens/<profile>-upload.json` and its record `<profile>-upload.meta.json` (when the login was issued and
when Google says it expires; never a token value). Set `TUBECREW_HOME` to use another folder. See
`../references/oauth-setup.md`. Needs `pip install google-auth google-auth-oauthlib requests` for --upload, --login
and --check-token only; the dry run is standard library (plus `ffprobe` on PATH for the chapter end check).

Exit codes: 0 every gate passes (dry run), the upload finished, or the token is alive; 1 a gate failed, the token
belongs to another channel, the channel already has this title, another run holds the lock, the upload failed, or a
schedule could not be confirmed; 2 usage or environment error (missing package, token, client file or argument, or
a login that has expired).
"""
from __future__ import annotations

import argparse
import contextlib
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
TAG_WEIGHT_WARN = 460  # headroom under 500: YouTube's own count can differ from this formula for some characters
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
SCHEDULE_MIN_MINUTES = 15  # processing headroom; YouTube publishes a past publishAt at once, which cannot be undone
READBACK_TRIES = 5  # a read right after a write can still return the old value: ask again before calling it failed
READBACK_WAIT_S = 2.0
RECENT_UPLOADS = 50  # playlistItems.list's maxResults ceiling: one call (1 quota unit) covers the latest 50 uploads
EMPTY_RECORD_DAYS = 7  # a dropped upload leaves its empty record (duration P0D) close to the failed attempt
LOCK_NAME = ".upload.lock"
LOCK_STALE_HOURS = 6  # longer than a normal upload takes; an older lock was left behind by a killed run
TOKEN_WARN_HOURS = 48  # two days' notice: time to log in again before an upload day
TESTING_TOKEN_DAYS = 7  # Google: External OAuth apps in Testing status get refresh tokens that expire in 7 days
UNLIMITED_AFTER_DAYS = 8  # a login that still refreshes after 8 days with no reported expiry has no 7-day limit
REPLACEMENT_CHAR = "\ufffd"  # what a wrong-encoding conversion leaves where a character could not be decoded

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
                "made_for_kids", "synthetic_media", "channel_id", "thumbnail", "localizations", "publish_at",
                "thumbnail_text"}

_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
_PROFILE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_CHAPTER = re.compile(r"^\s*(?:[-*•]\s+)?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\s+(\S.*)$")
_VERDICT_NAME = re.compile(r"^(?P<kind>RENDER-AUDIT|UPLOAD-GATE)-(?P<date>\d{4}-\d{2}-\d{2})(?:-(?P<n>\d+))?\.md$")
_VERDICT_LINE = re.compile(r"^[\s#>*_`]*VERDICT[\s*_`]*:(?P<rest>.*)$", re.I)
_HASH_LINE = re.compile(r"^[\s#>*_`-]*(?P<label>Video|Metadata|Thumbnail)[\s*_`]*:\s*(?P<path>.*?)\s*sha256\s*[:=]\s*`?"
                        r"(?P<sha>[0-9a-fA-F]{12,64})", re.I)
_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")
_URL_QUERY = re.compile(r"((?:https?://|/)[^\s?'\"<>()]*)\?[^\s'\"<>()]*")
_UPLOAD_ID = re.compile(r"(upload_id=)[^&\s'\"<>()]+", re.I)


class EnvError(Exception):
    """Usage or environment problem (exit 2): the message says what to run or install."""


class UploadError(RuntimeError):
    """The resumable upload could not finish."""


class ScheduleTooClose(UploadError):
    """publish_at came closer than the margin while the file was uploading; the last chunk was not sent."""


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
    again: bool = False
    publish_at: Optional[str] = None  # UTC 'Z' form, set only with --schedule and a valid publish_at
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    @property
    def failed(self) -> int:
        return sum(c.status == "FAIL" for c in self.checks)


def utcnow() -> dt.datetime:
    """The clock, in one place so tests can move it."""
    return dt.datetime.now(dt.timezone.utc)


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
    """(errors, warnings): a list of non-empty strings with a weight of at most 500 (a warning above 460)."""
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
    elif weight > TAG_WEIGHT_WARN:
        warnings.append(f"tag weight is {weight}, close to YouTube's {TAG_WEIGHT_MAX}: keep it under "
                        f"{TAG_WEIGHT_WARN}, YouTube's own count can come out a little higher")
    if not tags:
        warnings.append("no tags")
    elif len({t.strip().casefold() for t in tags}) != len(tags):
        warnings.append("some tags repeat")
    return errors, warnings


def validate_localizations(localizations, default_language) -> tuple:
    """(errors, warnings) for `localizations: {"<lang>": {"title": ..., "description": ...}}`. Each language follows
    the title and description rules; YouTube needs the default language to accept translations
    (defaultLanguageNotSet), and a key equal to the default language would duplicate the main text."""
    if localizations is None or localizations == {}:
        return [], []
    if not isinstance(localizations, dict):
        return ['localizations must be an object: {"de": {"title": "...", "description": "..."}}'], []
    errors, warnings = [], []
    if not default_language:
        errors.append("localizations need default_language: YouTube refuses translated titles when it does not know "
                      "the language of the main title (defaultLanguageNotSet)")
    for lang, text in localizations.items():
        if not isinstance(lang, str) or not _LANGUAGE.fullmatch(lang):
            errors.append(f"localization key {lang!r} is not a language code (de, es, pt-BR ...)")
        elif isinstance(default_language, str) and lang.casefold() == default_language.casefold():
            errors.append(f"localization {lang!r} is the default language: the main title and description already "
                          "cover it; remove it")
        if not isinstance(text, dict) or set(text) != {"title", "description"}:
            errors.append(f"localization {lang!r} must have exactly \"title\" and \"description\"")
            continue
        for part_errors, part_warnings in (validate_title(text["title"]), validate_description(text["description"])):
            errors += [f"[{lang}] {e}" for e in part_errors]
            warnings += [f"[{lang}] {w}" for w in part_warnings]
    return errors, warnings


def replacement_chars(value, path: str = "") -> list:
    """Field paths whose text holds U+FFFD: a wrong-encoding conversion replaced characters it could not decode."""
    found = []
    if isinstance(value, str):
        if REPLACEMENT_CHAR in value:
            found.append(path or "value")
    elif isinstance(value, dict):
        for key, item in value.items():
            found += replacement_chars(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            found += replacement_chars(item, f"{path}[{i}]")
    return found


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
    """(errors, warnings) for every upload.json field. Pure: does not look at files or the clock (publish_at is
    checked in build_plan, because it depends on --schedule and on the time)."""
    if not isinstance(meta, dict):
        return ["upload.json is not a JSON object"], []
    errors, warnings = [], []
    for part in (validate_title(meta.get("title")), validate_description(meta.get("description")),
                 validate_tags(meta.get("tags")),
                 validate_localizations(meta.get("localizations"), meta.get("default_language"))):
        errors += part[0]
        warnings += part[1]
    for where in replacement_chars(meta):
        errors.append(f"{where} contains U+FFFD, the replacement character: the text was converted from another "
                      "encoding and lost characters. Fix the text and save upload.json as UTF-8")
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
    lines = meta.get("thumbnail_text")
    if lines is not None and not (isinstance(lines, list) and all(isinstance(t, str) for t in lines)):
        warnings.append("thumbnail_text should be a list of the thumbnail's text lines")
    unknown = sorted(set(meta) - KNOWN_FIELDS)
    if unknown:
        warnings.append(f"unknown field(s) ignored: {', '.join(unknown)} (uploads are always private; publish in "
                        "YouTube Studio, or schedule with publish_at plus --schedule)")
    return errors, warnings


# --- time ---------------------------------------------------------------------------------------------------------

def _fromiso(text: str) -> Optional[dt.datetime]:
    """ISO 8601 -> datetime (naive when the text has no offset), or None. Accepts a trailing Z and any number of
    fraction digits (Python 3.9's fromisoformat takes only 3 or 6)."""
    text = text.strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_time(value) -> Optional[dt.datetime]:
    """An aware timestamp from a record or an API answer, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    when = _fromiso(value)
    return when if when is not None and when.tzinfo is not None else None


def iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def parse_publish_at(value, now: dt.datetime) -> tuple:
    """upload.json's publish_at -> (YouTube publishAt in UTC 'Z' form, None) or (None, error). A time zone is
    required, and the time must be at least SCHEDULE_MIN_MINUTES ahead of `now`."""
    example = "for example 2026-11-02T16:00:00+01:00"
    if not isinstance(value, str) or not value.strip():
        return None, f"publish_at must be an ISO 8601 date and time with a time zone ({example})"
    when = _fromiso(value)
    if when is None:
        return None, f"publish_at {value!r} is not ISO 8601 ({example})"
    if when.tzinfo is None:
        return None, (f"publish_at {value!r} has no time zone; add one (+01:00, -05:00 or Z) so the time is not "
                      "guessed")
    when = when.astimezone(dt.timezone.utc)
    minutes = (when - now).total_seconds() / 60
    if minutes < SCHEDULE_MIN_MINUTES:
        where = "is in the past" if minutes < 0 else f"is only {minutes:.0f} minutes from now"
        return None, (f"publish_at {value!r} {where}: it must be at least {SCHEDULE_MIN_MINUTES} minutes ahead "
                      "(YouTube publishes a past time at once)")
    return when.strftime("%Y-%m-%dT%H:%M:%SZ"), None


def same_instant(a, b) -> bool:
    ta, tb = parse_time(a), parse_time(b)
    return ta is not None and tb is not None and ta == tb


def iso_duration_seconds(value) -> Optional[float]:
    """Data API duration (`PT8M12S`, `P0D`) -> seconds, or None when it cannot be read."""
    m = _DURATION.match(value or "") if isinstance(value, str) else None
    if not m or value in ("P", "PT") or value.endswith("T"):
        return None
    days, hours, minutes, seconds = m.groups()
    return int(days or 0) * 86400 + int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds or 0)


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


def read_upload_json(path: Path) -> tuple:
    """(data, None) or (None, error). Strict UTF-8 (a BOM is fine): a file saved in another encoding is refused here
    instead of turning its accented letters into U+FFFD that would then be uploaded."""
    try:
        data = path.read_bytes()
    except OSError as e:
        return None, f"{UPLOAD_JSON} could not be read ({type(e).__name__})"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return None, f"{UPLOAD_JSON} is saved as UTF-16; save it as UTF-8"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        return None, (f"{UPLOAD_JSON} is not UTF-8 (byte {e.start} cannot be decoded): it was saved in another "
                      "encoding, for example the ANSI code page that Windows PowerShell 5.1's Set-Content writes. "
                      "Save it as UTF-8")
    try:
        return json.loads(text), None
    except ValueError as e:
        where = f" at line {e.lineno}" if hasattr(e, "lineno") else ""
        return None, f"{UPLOAD_JSON} is not valid JSON ({getattr(e, 'msg', 'parse error')}{where})"


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


def previous_notes(entry: dict) -> str:
    """What the earlier upload left undone, for the duplicate line: a rerun never retries those steps."""
    notes = []
    if entry.get("thumbnail") in ("pending", "failed"):
        notes.append("its thumbnail was not set at upload, add it in YouTube Studio")
    if entry.get("publish_at") and entry.get("schedule_confirmed") is False:
        notes.append("its schedule was never confirmed, check it in YouTube Studio")
    if entry.get("alarm"):
        notes.append(f"ALARM at upload: {entry['alarm']}")
    return "; " + "; ".join(notes) if notes else ""


def write_upload_log(path: Path, entries: list) -> None:
    """Writes the whole log atomically (temporary file, then replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"uploads": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


# --- lock: one --upload per project at a time ---------------------------------------------------------------------

def read_lock(path: Path) -> Optional[dict]:
    """The lock's content ({"pid", "started_at"}), {} when it is unreadable, None when there is no lock."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def lock_age_hours(path: Path, info: dict, now: dt.datetime) -> Optional[float]:
    started = parse_time(info.get("started_at"))
    if started is None:
        try:
            started = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        except OSError:
            return None
    return (now - started).total_seconds() / 3600


def describe_lock(path: Path, info: dict, now: dt.datetime) -> str:
    age = lock_age_hours(path, info, now)
    return f"pid {info.get('pid', '?')}, " + (f"{age:.1f} h old" if age is not None else "age unknown")


@contextlib.contextmanager
def upload_lock(qa_dir: Path):
    """Yields (held, note). `qa/.upload.lock` (O_CREAT|O_EXCL) with the pid and start time, so two --upload runs
    cannot both pass the duplicate checks and upload twice. Someone else's lock is left alone unless it is older than
    LOCK_STALE_HOURS (a killed run); our own lock is removed on every exit, errors included. No qa/ folder means the
    review gates fail anyway, so there is nothing to protect."""
    path = qa_dir / LOCK_NAME
    if not qa_dir.is_dir():
        yield True, ""
        return
    now, note = utcnow(), ""
    info = read_lock(path)
    if info is not None:
        age = lock_age_hours(path, info, now)
        if age is None or age < LOCK_STALE_HOURS:
            yield False, describe_lock(path, info, now)
            return
        note = (f"replaced a stale lock ({describe_lock(path, info, now)}): a killed run left it; the title check on "
                "the channel still guards against an upload whose answer was lost")
        with contextlib.suppress(OSError):
            path.unlink()
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        yield False, "another run took the lock a moment ago"
        return
    mine = {"pid": os.getpid(), "started_at": iso(now)}
    try:
        try:
            os.write(fd, json.dumps(mine).encode("utf-8"))
        finally:
            os.close(fd)
        yield True, note
    finally:
        if read_lock(path) == mine:
            with contextlib.suppress(OSError):
                path.unlink()


# --- the plan: everything read from disk, no network --------------------------------------------------------------

def build_plan(project: Path, video: Optional[Path] = None, upload_mode: bool = False,
               skip_reason: Optional[str] = None, again: bool = False, schedule: bool = False) -> Plan:
    plan = Plan(project=project, upload_mode=upload_mode, skip_reason=skip_reason, again=again)
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
        plan.meta, error = read_upload_json(meta_path)
        if error:
            add(Check("metadata", "FAIL", error))
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
            languages = sorted(meta.get("localizations") or {})
            add(Check("metadata", "PASS", f"title {len(meta['title'])} chars, description "
                                          f"{len(desc.encode('utf-8'))} bytes, {len(tags)} tags (weight "
                                          f"{tag_weight(tags)}/{TAG_WEIGHT_MAX})"
                                          + (f", translations: {', '.join(languages)}" if languages else "")))
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
        raw = meta.get("publish_at")
        if schedule:
            if raw is None:
                add(Check("schedule", "FAIL", "--schedule needs publish_at in upload.json (ISO 8601 with a time "
                                              "zone, for example 2026-11-02T16:00:00+01:00)"))
            else:
                plan.publish_at, problem = parse_publish_at(raw, utcnow())
                if problem:
                    add(Check("schedule", "FAIL", problem))
                else:
                    add(Check("schedule", "PASS", f"publish_at {plan.publish_at} (upload.json: {raw}); the video "
                                                  f"stays private until then. The {SCHEDULE_MIN_MINUTES}-minute "
                                                  "margin is measured again right before the upload"))
        elif raw is not None:
            add(Check("schedule", "WARN", "publish_at is ignored: pass --schedule to use it (without it the video "
                                          "stays private with no publish time)"))
    elif schedule:
        add(Check("schedule", "FAIL", f"--schedule needs a readable {UPLOAD_JSON} with publish_at"))

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
            notes = previous_notes(previous)
            if again:
                add(Check("duplicate", "WARN", f"already uploaded as {previous['video_id']} ({when}); --again "
                                               f"uploads a second copy{notes}"))
            else:
                add(Check("duplicate", "FAIL", f"already uploaded as {previous['video_id']} ({when}); YouTube "
                                               f"never replaces a video file. Pass --again for a second copy{notes}"))
    return plan


def build_body(meta: dict, publish_at: Optional[str] = None) -> dict:
    """`videos.insert` body. Private is a constant here; nothing in upload.json or on the command line changes it.
    `publish_at` (already validated, UTC) is added only when the caller passes it, which main does only for
    --schedule; a `publish_at` field alone in upload.json never reaches the body."""
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
    if meta.get("localizations"):
        body["localizations"] = {lang: {"title": text["title"], "description": text.get("description") or ""}
                                 for lang, text in meta["localizations"].items()}
    if publish_at:
        body["status"]["publishAt"] = publish_at
    assert_private(body, allow_schedule=bool(publish_at))
    return body


def insert_part(body: dict) -> str:
    """The `part` parameter for videos.insert: a resource part sent in the body but missing here is dropped silently,
    so `localizations` is named whenever the body carries it."""
    return ",".join(p for p in ("snippet", "status", "localizations") if p in body)


def assert_private(body: dict, allow_schedule: bool = False) -> None:
    """Refuses any body that is not private. `publishAt` passes only when the caller explicitly allows a schedule."""
    status = body.get("status") or {}
    if status.get("privacyStatus") != "private" or ("publishAt" in status and not allow_schedule):
        raise RuntimeError(f"refusing to upload: the request body is not private ({status!r})")


# --- errors that may carry URLs -----------------------------------------------------------------------------------

def redact(text) -> str:
    """Drops URL query strings and any `upload_id`: the resumable session's upload_id works like a password for that
    upload, and error texts from `requests` quote the URL they were calling."""
    text = _URL_QUERY.sub(r"\1", str(text))
    return _UPLOAD_ID.sub(r"\1<hidden>", text)


def describe_error(e: BaseException) -> str:
    """Short, safe error text for the terminal and the JSON result."""
    message = redact(str(e))[:300]
    if isinstance(e, UploadError):
        return message or type(e).__name__
    return f"{type(e).__name__}: {message}" if message else type(e).__name__


# --- token record (sidecar next to the token; never holds a token value) ------------------------------------------

def tubecrew_home() -> Path:
    env = os.environ.get("TUBECREW_HOME")
    return Path(env).expanduser() if env else Path.home() / ".tubecrew"


def token_path(profile: str) -> Path:
    return tubecrew_home() / "tokens" / f"{profile}-upload.json"


def meta_path_for(token_file: Path) -> Path:
    """`<profile>-upload.json` -> `<profile>-upload.meta.json`."""
    return token_file.with_suffix(".meta.json")


def login_command(profile: str) -> str:
    """The login command to suggest. Never an absolute path: it can land in reports that get shared."""
    script = "upload.py"
    if sys.argv and sys.argv[0].endswith(".py"):
        try:
            script = Path(sys.argv[0]).resolve().relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            script = Path(sys.argv[0]).name
    return f'python "{script}" --login --profile {profile}'


def fingerprint(refresh_token) -> Optional[str]:
    """12 hex characters of the refresh token's SHA-256: tells whether the record belongs to the token on disk
    without storing anything that could be used as a credential."""
    if not refresh_token:
        return None
    return hashlib.sha256(str(refresh_token).encode("utf-8")).hexdigest()[:12]


def read_token_meta(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_token_meta(path: Path, meta: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def reported_lifetime(answer) -> Optional[int]:
    """`refresh_token_expires_in` (seconds) from a token endpoint answer (bytes, text or dict), or None. Google sends
    it when the login is time-limited, which is the case for OAuth apps in Testing mode (about 7 days)."""
    if isinstance(answer, (bytes, str)):
        try:
            answer = json.loads(answer or b"{}")
        except ValueError:
            return None
    if not isinstance(answer, dict):
        return None
    value = answer.get("refresh_token_expires_in")
    if isinstance(value, bool):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def update_token_meta(path: Path, refresh_token, lifetime: Optional[int], now: dt.datetime, issued: bool = False,
                      channel_id: Optional[str] = None) -> dict:
    """Records what is known about the login after a fresh login (`issued`) or a successful refresh. A record that
    belongs to another refresh token starts over (first_seen = now, issue time unknown)."""
    meta = read_token_meta(path)
    fp, stamp = fingerprint(refresh_token), iso(now)
    if issued or meta.get("refresh_fingerprint") != fp:
        meta = {"refresh_fingerprint": fp, "first_seen": stamp}
        if issued:
            meta["issued_at"] = stamp
    if lifetime is not None:
        meta["refresh_expires_at"] = iso(now + dt.timedelta(seconds=lifetime))
        meta.pop("no_limit", None)
    elif not issued:
        expires = parse_time(meta.get("refresh_expires_at"))
        if expires is not None and expires <= now:
            meta.pop("refresh_expires_at")  # it still refreshed after that time, so that expiry no longer holds
        start = parse_time(meta.get("issued_at")) or parse_time(meta.get("first_seen"))
        if "refresh_expires_at" not in meta and start and now - start >= dt.timedelta(days=UNLIMITED_AFTER_DAYS):
            meta["no_limit"] = True
    meta["checked_at"] = stamp
    if channel_id:
        meta["channel_id"] = channel_id
    write_token_meta(path, meta)
    return meta


def _span(delta: dt.timedelta) -> str:
    hours = delta.total_seconds() / 3600
    return f"{hours:.0f} hours" if abs(hours) < 48 else f"{hours / 24:.1f} days"


def describe_token(meta: dict, now: dt.datetime) -> dict:
    """Age and expiry of a login from its record -> {"status": PASS|WARN, "detail", "issued_at", "age_days",
    "expires_at", "hours_left", "expiry": reported|assumed|none|unknown}. WARN within TOKEN_WARN_HOURS of expiry."""
    issued, first = parse_time(meta.get("issued_at")), parse_time(meta.get("first_seen"))
    expires = parse_time(meta.get("refresh_expires_at"))
    start = issued or first
    out = {"status": "PASS", "issued_at": meta.get("issued_at"), "age_days": None, "expires_at": None,
           "hours_left": None, "expiry": "unknown"}
    if issued:
        age = f"issued {_span(now - issued)} ago ({issued:%Y-%m-%d %H:%M} UTC)"
    elif first:
        age = f"in use for at least {_span(now - first)} (issue time unknown)"
    else:
        age = "issue time unknown (no record from --login)"
    if start:
        out["age_days"] = round((now - start).total_seconds() / 86400, 1)

    def result(status: str, detail: str, **extra) -> dict:
        out.update(status=status, detail=detail, **extra)
        return out

    if expires:
        hours = (expires - now).total_seconds() / 3600
        extra = {"expires_at": iso(expires), "hours_left": round(hours, 1), "expiry": "reported"}
        if hours <= 0:
            return result("WARN", f"{age}; Google said the login expires {expires:%Y-%m-%d %H:%M} UTC, which has "
                                  "passed", **extra)
        if hours < TOKEN_WARN_HOURS:
            return result("WARN", f"{age}; the login expires in {hours:.0f} hours ({expires:%Y-%m-%d %H:%M} UTC)",
                          **extra)
        return result("PASS", f"{age}; the login expires {expires:%Y-%m-%d %H:%M} UTC ({hours / 24:.1f} days left)",
                      **extra)
    if meta.get("no_limit"):
        return result("PASS", f"{age}; no expiry: it still refreshes after {UNLIMITED_AFTER_DAYS}+ days",
                      expiry="none")
    if start is None:
        return result("WARN", f"{age}: run --check-token once to start the record")
    assumed = start + dt.timedelta(days=TESTING_TOKEN_DAYS)
    hours = (assumed - now).total_seconds() / 3600
    if hours < TOKEN_WARN_HOURS:
        return result("WARN", f"{age}; Google reported no expiry, but if the OAuth app is in Testing mode the login "
                              f"ends about {TESTING_TOKEN_DAYS} days after it was issued (around "
                              f"{assumed:%Y-%m-%d %H:%M} UTC)", expires_at=iso(assumed), hours_left=round(hours, 1),
                      expiry="assumed")
    return result("PASS", f"{age}; Google reported no expiry", expiry="assumed")


def token_check(profile: str, meta: Optional[dict], now: dt.datetime) -> Check:
    """Offline token line for the gate table: is there a login, how old, does it expire soon, which channel did it
    log in to. Reads only the record next to the token, never the token itself."""
    path = token_path(profile)
    if not path.is_file():
        return Check("token", "WARN", f"no upload token for profile '{profile}': log in before --upload "
                                      f"({login_command(profile)})")
    record = read_token_meta(meta_path_for(path))
    expected = (meta or {}).get("channel_id") if isinstance(meta, dict) else None
    logged_in = record.get("channel_id")
    if logged_in and isinstance(expected, str) and _CHANNEL_ID.fullmatch(expected) and logged_in != expected:
        return Check("token", "WARN", f"profile '{profile}' logged in to {logged_in}, upload.json says {expected}: "
                                      "--upload will refuse. Use the right --profile or log in again")
    info = describe_token(record, now)
    detail = f"profile '{profile}': {info['detail']}"
    if info["status"] == "WARN":
        detail += f"; log in again before uploading: {login_command(profile)}"
    return Check("token", info["status"], detail)


# --- network (only with --upload, --login or --check-token) -------------------------------------------------------

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


def _capturing_request():
    """google-auth transport that keeps the token endpoint's last answer: `refresh_token_expires_in` lives there and
    google-auth does not keep it."""
    _, Request, _, _ = _google_auth()

    class CapturingRequest(Request):
        last_data = None

        def __call__(self, *args, **kwargs):
            response = super().__call__(*args, **kwargs)
            self.last_data = getattr(response, "data", None)
            return response

    return CapturingRequest()


def refresh_credentials(cred) -> Optional[int]:
    """Refreshes `cred` in place; returns Google's reported refresh-token lifetime in seconds, or None."""
    request = _capturing_request()
    cred.refresh(request)
    return reported_lifetime(getattr(request, "last_data", None))


def load_upload_credentials(path: Path, login_cmd: str):
    """Loads and refreshes the profile's upload token and updates its record. Missing, unreadable or revoked ->
    EnvError (exit 2)."""
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
        lifetime = refresh_credentials(cred)
    except RefreshError as e:
        why = ("the login was revoked or has expired (invalid_grant; in Testing mode Google expires it after "
               "about 7 days)" if "invalid_grant" in str(e) else
               f"refreshing the token failed ({redact(str(e))[:160]})")
        raise EnvError(f"{why}. Log in again: {login_cmd}") from e
    except GoogleAuthError as e:
        raise EnvError(f"could not reach Google to refresh the token ({type(e).__name__}); check the connection "
                       "and try again") from e
    with contextlib.suppress(OSError):
        path.write_text(cred.to_json(), encoding="utf-8")
    with contextlib.suppress(OSError):
        update_token_meta(meta_path_for(path), cred.refresh_token, lifetime, utcnow())
    return cred


def _refresh_if_needed(cred) -> None:
    """Long uploads can outlive the one-hour access token; later calls need a live one."""
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
        return redact(str(response.text))[:300]
    except Exception:  # noqa: BLE001 - an error body is only for the message
        return ""


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _get_json(session, url: str, params: dict, token: str, what: str) -> dict:
    r = session.get(url, params=params, headers=_auth(token), timeout=60)
    if r.status_code != 200:
        raise UploadError(f"{what} returned HTTP {r.status_code}: {_text(r)}")
    data = r.json()
    return data if isinstance(data, dict) else {}


def channel_info(session, token: str) -> tuple:
    """(channel ids, uploads playlist id) of the token: channels.list?part=id,contentDetails&mine=true (1 unit)."""
    data = _get_json(session, f"{API}/channels", {"part": "id,contentDetails", "mine": "true"}, token,
                     "channels.list")
    items = data.get("items") or []
    uploads = None
    if len(items) == 1:
        uploads = ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    return [str(item.get("id", "")) for item in items], uploads


def recent_uploads(session, token: str, playlist_id: str) -> list:
    """The channel's latest uploads, newest first: playlistItems.list on its uploads playlist (1 unit). The owner's
    token sees private videos there too. A channel without any upload may answer 404 for that playlist."""
    r = session.get(f"{API}/playlistItems", params={"part": "snippet", "playlistId": playlist_id,
                                                    "maxResults": RECENT_UPLOADS},
                    headers=_auth(token), timeout=60)
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        raise UploadError(f"playlistItems.list returned HTTP {r.status_code}: {_text(r)}")
    out = []
    for item in (r.json() or {}).get("items") or []:
        snippet = item.get("snippet") or {}
        video_id = (snippet.get("resourceId") or {}).get("videoId")
        if video_id:
            out.append({"video_id": video_id, "title": snippet.get("title") or "",
                        "added_at": snippet.get("publishedAt")})
    return out


def video_states(session, token: str, ids: list) -> dict:
    """{video id: {"seconds": duration or None, "upload_status": ...}} via videos.list (1 unit for up to 50 ids)."""
    data = _get_json(session, f"{API}/videos", {"part": "contentDetails,status", "id": ",".join(ids[:50]),
                                                "maxResults": 50}, token, "videos.list")
    return {item.get("id"): {"seconds": iso_duration_seconds((item.get("contentDetails") or {}).get("duration")),
                             "upload_status": (item.get("status") or {}).get("uploadStatus")}
            for item in data.get("items") or []}


def same_title(a, b) -> bool:
    return isinstance(a, str) and isinstance(b, str) and " ".join(a.split()) == " ".join(b.split())


def recent(items: list, now: dt.datetime, days: int = EMPTY_RECORD_DAYS) -> list:
    """Uploads added to the uploads playlist in the last `days` days."""
    out = []
    for item in items:
        added = parse_time(item.get("added_at"))
        if added is not None and now - added <= dt.timedelta(days=days):
            out.append(item)
    return out


def find_empty(items: list, states: dict) -> list:
    """Uploads whose duration is zero (`P0D`): what a dropped upload can leave on the channel."""
    return [item for item in items if (states.get(item["video_id"]) or {}).get("seconds") == 0]


def read_status(session, token: str, video_id: str) -> Optional[dict]:
    """The video's status block via videos.list?part=status (1 unit); None when the video is not (yet) listed."""
    data = _get_json(session, f"{API}/videos", {"part": "status", "id": video_id}, token, "videos.list")
    items = data.get("items") or []
    return (items[0].get("status") or {}) if items else None


def confirm_schedule(session, token: str, video_id: str, publish_at: str, sleep: Callable = time.sleep) -> tuple:
    """("confirmed" | "not-private" | "unconfirmed", last status, last error). A video that is not private ends the
    check at once (nothing can undo it); a missing or different publishAt is read again, because a read right after
    the write can still be stale."""
    status, error = None, None
    for attempt in range(READBACK_TRIES):
        if attempt:
            sleep(READBACK_WAIT_S)
        try:
            status = read_status(session, token, video_id)
        except (UploadError, OSError, ValueError) as e:
            error = describe_error(e)
            continue
        if not status:
            continue
        if status.get("privacyStatus") not in (None, "private"):
            return "not-private", status, None
        if status.get("privacyStatus") == "private" and same_instant(status.get("publishAt"), publish_at):
            return "confirmed", status, None
    return "unconfirmed", status, error


def _received_bytes(response) -> int:
    """Bytes the server holds, from the 308 `Range: bytes=0-N` header; no header means none."""
    m = re.search(r"(\d+)\s*-\s*(\d+)", _header(response, "Range") or "")
    return int(m.group(2)) + 1 if m else 0


def resumable_upload(session, token: str, video: Path, body: dict, chunk_size: int = CHUNK_BYTES,
                     sleep: Callable = time.sleep, progress: Optional[Callable] = None, allow_schedule: bool = False,
                     before_last_chunk: Optional[Callable] = None) -> dict:
    """YouTube resumable upload: POST the metadata, then PUT chunks with Content-Range.

    A 308 answer carries `Range` (what the server holds); the next chunk starts right after it. A 5xx answer or a
    connection error waits (exponential backoff) and asks the server where it stands with `Content-Range:
    bytes */<size>`, then resumes from there. Gives up after MAX_RETRIES failures in a row. `before_last_chunk` runs
    right before the chunk that completes the file (the moment YouTube creates the video) and may raise to stop.
    """
    assert_private(body, allow_schedule)
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
            r = session.post(UPLOAD_URL, params={"uploadType": "resumable", "part": insert_part(body)},
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
                if before_last_chunk is not None and offset + len(data) >= size:
                    before_last_chunk()
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


def thumbnail_hint(status) -> str:
    if status == 403:
        return (" (custom thumbnails need a verified channel - phone verification in YouTube settings - or YouTube "
                "refused more thumbnail changes for this video for now)")
    if status == 429:
        return " (too many thumbnail requests for now; YouTube limits how often a thumbnail can be set)"
    return ""


# --- output -------------------------------------------------------------------------------------------------------

class Out:
    """Human lines go to stdout, or to stderr when --json keeps stdout for the JSON result."""

    def __init__(self, as_json: bool):
        self.stream = sys.stderr if as_json else sys.stdout

    def __call__(self, line: str = "") -> None:
        print(line, file=self.stream, flush=True)


def print_plan(plan: Plan, say: Out) -> None:
    if plan.upload_mode:
        mode = "UPLOAD (private only" + (", scheduled by publish_at)" if plan.publish_at else ")")
    else:
        mode = "DRY RUN (no network; nothing is uploaded)"
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
            "publish_at": plan.publish_at,
            "ok": plan.ok,
            "checks": [{"gate": c.gate, "status": c.status, "detail": c.detail} for c in plan.checks]}


# --- commands -----------------------------------------------------------------------------------------------------

def check_channel_uploads(session, token: str, playlist_id: Optional[str], plan: Plan, say: Out) -> tuple:
    """Before a real upload: the channel's latest uploads must not hold this exact title (an earlier run whose answer
    got lost may have uploaded it already), and recent empty records (duration P0D, left by a dropped upload) are
    reported. Returns (exit code, result fields)."""
    if not playlist_id:
        say("UPLOAD REFUSED - the channel's uploads list was not in the channels.list answer, so the title check "
            "could not run. Nothing was uploaded.")
        return 1, {"error": "no uploads playlist"}
    try:
        latest = recent_uploads(session, token, playlist_id)
        fresh = recent(latest, utcnow())
        states = video_states(session, token, [v["video_id"] for v in fresh]) if fresh else {}
    except (UploadError, OSError, ValueError) as e:
        say(f"UPLOAD REFUSED - could not list the channel's recent uploads ({describe_error(e)}). Nothing was "
            "uploaded.")
        return 1, {"error": describe_error(e)}
    empty = find_empty(fresh, states)
    for v in empty:
        say(f"WARNING: recent upload {v['video_id']} \"{v['title']}\" has no duration (P0D): a dropped upload can "
            "leave such an empty record. Look in YouTube Studio and delete it if it is a leftover.")
    same = [v["video_id"] for v in latest if same_title(v["title"], plan.meta["title"])]
    fields = {"same_title": same, "empty_records": [v["video_id"] for v in empty]}
    if same and not plan.again:
        say(f"UPLOAD REFUSED - the channel already has a video with this exact title ({', '.join(same)}). An earlier "
            "run whose answer got lost may have uploaded it, and YouTube's uploads list can lag a few minutes: look "
            "in YouTube Studio first. Nothing was uploaded. Pass --again to upload anyway.")
        return 1, {**fields, "error": "same title on the channel"}
    if same:
        say(f"WARNING: the channel already has this exact title ({', '.join(same)}); --again uploads anyway.")
    else:
        say(f"title: not among the channel's latest {len(latest)} uploads")
    return 0, fields


def do_upload(plan: Plan, profile: str, say: Out) -> tuple:
    """Network part of --upload; every local gate already passed. Returns (exit code, result dict)."""
    meta, login_cmd = plan.meta, login_command(profile)
    cred = load_upload_credentials(token_path(profile), login_cmd)
    session = new_session()
    try:
        ids, uploads = channel_info(session, cred.token)
    except (UploadError, OSError, ValueError) as e:
        say(f"UPLOAD REFUSED - could not read the token's channel: {describe_error(e)}. Nothing was uploaded.")
        return 1, {"uploaded": False, "error": describe_error(e)}
    if ids != [meta["channel_id"]]:
        say(f"UPLOAD REFUSED - WRONG CHANNEL: this token belongs to {', '.join(ids) or 'no channel'}, upload.json "
            f"says {meta['channel_id']}. Nothing was uploaded. Log in with the right Google account: {login_cmd}")
        return 1, {"uploaded": False, "error": "wrong channel", "token_channels": ids}
    say(f"channel: {meta['channel_id']} confirmed")
    code, fields = check_channel_uploads(session, cred.token, uploads, plan, say)
    if code:
        return code, {"uploaded": False, **fields}

    if plan.publish_at:
        _, problem = parse_publish_at(plan.publish_at, utcnow())
        if problem:
            say(f"UPLOAD REFUSED - {problem}. Nothing was uploaded. Set a later publish_at, get a new upload-checker "
                "review (upload.json changed) and run again.")
            return 1, {"uploaded": False, **fields, "error": "publish_at too close"}

    def guard() -> None:
        _, late = parse_publish_at(plan.publish_at, utcnow())
        if late:
            raise ScheduleTooClose(late)

    body = build_body(meta, plan.publish_at)
    size = plan.video.stat().st_size
    say(f"UPLOADING as PRIVATE: {rel(plan.video, plan.project)} ({size / 1e6:.1f} MB)"
        + (f", YouTube publishes it at {plan.publish_at}" if plan.publish_at else ""))
    shown = {"pct": -1}

    def progress(done: int, total: int) -> None:
        pct = int(done * 100 / total)
        if pct >= shown["pct"] + 10 or done == total:
            shown["pct"] = pct
            say(f"  {pct}%")

    try:
        answer = resumable_upload(session, cred.token, plan.video, body, progress=progress,
                                  allow_schedule=bool(plan.publish_at),
                                  before_last_chunk=guard if plan.publish_at else None)
    except ScheduleTooClose as e:
        say(f"UPLOAD STOPPED before the last chunk - {e}. The upload did not complete, so YouTube should not have "
            "created the video; if an empty one shows up in Studio, delete it. Set a later publish_at, get a new "
            "upload-checker review and run again.")
        return 1, {"uploaded": False, **fields, "error": "publish_at too close after the upload"}
    except (UploadError, OSError, ValueError) as e:
        say(f"UPLOAD FAILED: {describe_error(e)}\nNothing was logged. If the last chunk went through, the video may "
            "exist anyway: look in YouTube Studio before running the command again (the next run also refuses a "
            "title the channel already has).")
        return 1, {"uploaded": False, **fields, "error": describe_error(e)}
    video_id = answer.get("id") if isinstance(answer, dict) else None
    if not video_id:
        say(f"UPLOAD FAILED: the answer has no video id: {redact(str(answer))[:300]}")
        return 1, {"uploaded": False, **fields, "error": "no video id"}
    privacy = (answer.get("status") or {}).get("privacyStatus")
    # The id goes to the screen and the log before anything else, so no later failure can lead to a second upload.
    say(f"UPLOADED: {video_id} (privacy: {privacy or 'not in the answer'})")

    entry = {"video_id": video_id, "video": rel(plan.video, plan.project), "sha256": plan.video_sha,
             "uploaded_at": iso(utcnow()), "privacy": "private", "channel_id": meta["channel_id"],
             "title": meta["title"], "thumbnail": "pending" if plan.thumbnail else "none"}
    if plan.publish_at:
        entry.update(publish_at=plan.publish_at, schedule_confirmed=False)
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
        say(f"WARNING: qa/{UPLOAD_LOG} could not be written ({describe_error(e)}). Add this entry by hand before "
            "running again:\n" + json.dumps(entry, ensure_ascii=False))
        entries = None

    def save_log() -> None:
        if entries is not None:
            with contextlib.suppress(OSError):
                write_upload_log(log_path, entries)

    result = {"uploaded": True, "video_id": video_id, "privacy": privacy, **fields,
              "log_written": entries is not None, "studio_url": f"https://studio.youtube.com/video/{video_id}/edit"}
    if "alarm" in entry:
        say(f"ALARM: YouTube answered privacyStatus {privacy!r}, not private. Open YouTube Studio now and check it. "
            "The thumbnail and the schedule check were skipped.")
        return 1, result

    code = 0 if entries is not None else 1
    if plan.publish_at:
        try:
            _refresh_if_needed(cred)
        except Exception as e:  # noqa: BLE001 - the read below reports what it can
            say(f"WARNING: token refresh before the status check failed ({type(e).__name__})")
        state, status, error = confirm_schedule(session, cred.token, video_id, plan.publish_at)
        status = status or {}
        entry["schedule_confirmed"] = state == "confirmed"
        result.update(publish_at=plan.publish_at, schedule_confirmed=state == "confirmed",
                      read_back={"privacyStatus": status.get("privacyStatus"), "publishAt": status.get("publishAt")})
        if state == "not-private":
            entry["alarm"] = f"after the upload YouTube reports privacyStatus {status.get('privacyStatus')!r}"
            save_log()
            say(f"ALARM: YouTube reports {video_id} as {status.get('privacyStatus')!r}, not private and scheduled. "
                "Open YouTube Studio now and set it to private. A video that was published once cannot be "
                "scheduled again.")
            return 1, result
        if state == "confirmed":
            say(f"SCHEDULED: {video_id} is private; YouTube publishes it at {status.get('publishAt')} (read back "
                f"from YouTube; upload.json says {meta.get('publish_at')}).")
        else:
            say(f"WARNING: could not confirm the schedule after {READBACK_TRIES} reads (last answer: privacyStatus "
                f"{status.get('privacyStatus')!r}, publishAt {status.get('publishAt')!r}"
                + (f"; {error}" if error else "") + "). The video is private; check the schedule in YouTube Studio.")
            code = 1

    if plan.thumbnail:
        try:
            _refresh_if_needed(cred)
        except Exception as e:  # noqa: BLE001 - the thumbnail step is best effort
            say(f"WARNING: token refresh before the thumbnail failed ({type(e).__name__})")
        status_code, message = set_thumbnail(session, cred.token, video_id, plan.thumbnail)
        entry["thumbnail_http"] = status_code
        entry["thumbnail"] = "set" if status_code is not None and 200 <= status_code < 300 else "failed"
        result["thumbnail_http"] = status_code
        if entry["thumbnail"] == "set":
            say(f"thumbnail: set (HTTP {status_code})")
        else:
            say(f"WARNING: thumbnail not set (HTTP {status_code}){thumbnail_hint(status_code)}: {message[:160]}. The "
                "video is uploaded; add the thumbnail in YouTube Studio. A rerun does not retry it.")
    save_log()

    say(f"https://studio.youtube.com/video/{video_id}/edit")
    if plan.publish_at:
        say("Left for you in Studio: end screen, cards, audio tracks. YouTube publishes the video at publish_at; "
            "change or cancel that in Studio.")
    else:
        say("Left for you in Studio: end screen, cards, audio tracks, then publish or schedule.")
    if entries is None:
        say(f"The upload worked but qa/{UPLOAD_LOG} is missing this entry: add it before running again.")
    return code, result


def expected_channel(project: Path) -> Optional[str]:
    """upload.json's channel_id for --login / --check-token with a project; None when it is not set yet."""
    path = project / UPLOAD_JSON
    if not path.is_file():
        return None
    meta, error = read_upload_json(path)
    if error:
        raise EnvError(error)
    channel = meta.get("channel_id") if isinstance(meta, dict) else None
    if channel in (None, ""):
        return None
    if not isinstance(channel, str) or not _CHANNEL_ID.fullmatch(channel):
        raise EnvError(f"channel_id {channel!r} in {UPLOAD_JSON} is not a channel id (UC + 22 characters)")
    return channel


def finish_login(profile: str, cred, lifetime: Optional[int], say: Out, expected: Optional[str] = None,
                 session=None) -> int:
    """After the browser login: read which channel the new token belongs to BEFORE saving it. A login for another
    channel (Google asks which channel when the account has several, and a wrong pick raises no error) or for no
    channel is never written; the previous token stays as it was."""
    session = session or new_session()
    try:
        data = _get_json(session, f"{API}/channels", {"part": "id,snippet", "mine": "true"}, cred.token,
                         "channels.list")
    except (UploadError, OSError, ValueError) as e:
        raise EnvError(f"could not read which channel this login belongs to ({describe_error(e)}). The token was "
                       "NOT saved; run the login again.") from e
    items = data.get("items") or []
    ids = [str(item.get("id", "")) for item in items]
    for item in items:
        say(f"Channel: {(item.get('snippet') or {}).get('title', '?')} ({item.get('id')})")
    if not ids:
        say("LOGIN NOT SAVED - this Google account has no YouTube channel, or none was picked. Run the login again "
            "and pick the channel.")
        return 1
    if expected and ids != [expected]:
        say(f"LOGIN NOT SAVED - WRONG CHANNEL: this login belongs to {', '.join(ids)}, upload.json says {expected}. "
            "The previous token, if any, is unchanged. Run the login again and pick the right channel.")
        return 1
    path = token_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cred.to_json(), encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)
    now = utcnow()
    record = update_token_meta(meta_path_for(path), getattr(cred, "refresh_token", None), lifetime, now,
                               issued=True, channel_id=ids[0] if len(ids) == 1 else None)
    say(f"Saved the upload token for profile '{profile}' ({path.name}).")
    if expected:
        say(f"Channel {expected} matches upload.json.")
    elif len(ids) == 1:
        say(f"Put {ids[0]} in upload.json as channel_id.")
    say(f"Login: {describe_token(record, now)['detail']}.")
    if lifetime:
        say(f"Google limits this login to about {lifetime / 86400:.0f} days, as it does for OAuth apps in Testing "
            "mode. To stop logging in every week, publish the app (references/oauth-setup.md).")
    return 0


def do_login(profile: str, say: Out, expected: Optional[str] = None) -> int:
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
    if expected:
        say(f"Pick the channel {expected} in the browser; a login for another channel is not saved.")
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), scopes=UPLOAD_SCOPES)
    cred = flow.run_local_server(port=0)
    lifetime = reported_lifetime(getattr(getattr(flow, "oauth2session", None), "token", None))
    return finish_login(profile, cred, lifetime, say, expected)


def do_check_token(profile: str, say: Out, expected: Optional[str] = None) -> tuple:
    """--check-token: refresh the token in memory (proves the login is alive), read its channel, print its age and
    expiry. Never prints a token value. Returns (exit code, result dict)."""
    path, login_cmd = token_path(profile), login_command(profile)
    cred = load_upload_credentials(path, login_cmd)
    try:
        ids, _ = channel_info(new_session(), cred.token)
    except (UploadError, OSError, ValueError) as e:
        raise EnvError(f"the login is alive but its channel could not be read ({describe_error(e)})") from e
    record_path = meta_path_for(path)
    record = read_token_meta(record_path)
    if record and len(ids) == 1 and record.get("channel_id") != ids[0]:
        record["channel_id"] = ids[0]
        with contextlib.suppress(OSError):
            write_token_meta(record_path, record)
    info = describe_token(record, utcnow())
    match = None if not expected else ids == [expected]
    say(f"token: alive (profile '{profile}', refreshed in memory just now)")
    say(f"channel: {', '.join(ids) or 'none'}")
    say(f"login: {info['detail']}")
    code = 0
    if match is False:
        say(f"WRONG CHANNEL: upload.json says {expected}; --upload would refuse. Log in again with the project "
            f"folder and pick the right channel: {login_cmd}")
        code = 1
    elif not ids:
        say("WARNING: this login sees no YouTube channel.")
        code = 1
    elif info["status"] == "WARN":
        say(f"WARNING: log in again before uploading: {login_cmd}")
    result = {"token": "alive", "profile": profile, "channel_ids": ids, "channel_match": match,
              "status": "FAIL" if code else info["status"], "detail": info["detail"],
              "issued_at": info["issued_at"], "age_days": info["age_days"], "expires_at": info["expires_at"],
              "hours_left": info["hours_left"], "expiry": info["expiry"]}
    return code, result


def run(project: Path, video: Optional[Path], a, skip: Optional[str], say: Out) -> tuple:
    """Gate table, then the dry-run result or the upload. Returns (exit code, result dict)."""
    plan = build_plan(project, video, a.upload, skip, a.again, a.schedule)
    plan.checks.append(token_check(a.profile, plan.meta, utcnow()))
    lock = project / "qa" / LOCK_NAME
    info = None if a.upload else read_lock(lock)
    if info is not None:
        plan.checks.append(Check("lock", "WARN", f"qa/{LOCK_NAME} exists ({describe_lock(lock, info, utcnow())}): "
                                                 "another --upload is running, or a killed run left it; --upload "
                                                 f"refuses until it is {LOCK_STALE_HOURS} hours old"))
    print_plan(plan, say)
    result = plan_json(plan)
    if not a.upload:
        say("Checked only with --upload (network): the token's channel, and this title among the channel's latest "
            "uploads.")
        say("RESULT: every gate passes." if plan.ok else
            f"RESULT: {plan.failed} gate line(s) FAIL - --upload would be refused.")
        say("DRY RUN - nothing was uploaded. Add --upload to upload as PRIVATE.")
        return (0 if plan.ok else 1), result
    if not plan.ok:
        say(f"UPLOAD REFUSED - {plan.failed} gate line(s) FAIL (above). Nothing was uploaded.")
        return 1, result
    code, upload_result = do_upload(plan, a.profile, say)
    result.update(upload_result)
    return code, result


def main(argv: Optional[list] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
    ap = argparse.ArgumentParser(description="Gate and upload a finished video to YouTube as PRIVATE.",
                                 allow_abbrev=False)  # "--u" must never mean --upload
    ap.add_argument("project", nargs="?", type=Path, help="the video project folder (with upload.json, render/, qa/)")
    ap.add_argument("--video", help="the video file (default: the single or newest .mp4 in render/)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--upload", action="store_true", help="really upload, as private (default: dry run, no network)")
    mode.add_argument("--login", action="store_true",
                      help="open the browser once to authorize this profile; with a project, a login for another "
                           "channel than upload.json's channel_id is refused before it is saved")
    mode.add_argument("--check-token", action="store_true",
                      help="refresh the token in memory and print its channel, age and expiry (uploads nothing)")
    ap.add_argument("--profile", default="default", help="credential profile, one per channel (default: default)")
    ap.add_argument("--schedule", action="store_true",
                    help="use upload.json's publish_at: the video stays private and YouTube publishes it at that "
                         f"time (at least {SCHEDULE_MIN_MINUTES} minutes ahead)")
    ap.add_argument("--skip-review", metavar="REASON",
                    help="proceed without matching reviewer verdicts (WARN instead of FAIL); the reason is logged")
    ap.add_argument("--again", action="store_true",
                    help="allow a second upload of a video already in the log, or of a title the channel already has")
    ap.add_argument("--json", action="store_true", help="print a machine-readable result")
    a = ap.parse_args(argv)
    say = Out(a.json)

    def usage(message: str) -> int:
        print(f"ERROR: {message}", file=sys.stderr)
        return 2

    if not _PROFILE.fullmatch(a.profile):
        return usage("--profile may only use letters, digits, '.', '_' and '-'")
    try:
        project = None
        if a.project is not None:
            project = a.project.expanduser().resolve()
            if not project.is_dir():
                return usage(f"project folder not found: {a.project}")
        if a.login:
            return do_login(a.profile, say, expected_channel(project) if project else None)
        if a.check_token:
            code, result = do_check_token(a.profile, say, expected_channel(project) if project else None)
            if a.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return code
        if project is None:
            return usage("give the project folder (or --login / --check-token)")
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

        if not a.upload:
            code, result = run(project, video, a, skip, say)
        else:
            with upload_lock(project / "qa") as (held, note):
                if not held:
                    say(f"UPLOAD REFUSED - another upload of this project holds qa/{LOCK_NAME} ({note}). Nothing was "
                        f"uploaded. If no upload is running (the process was killed), look in YouTube Studio and in "
                        f"qa/{UPLOAD_LOG} first, then delete the lock file.")
                    code, result = 1, {"uploaded": False, "error": "locked"}
                else:
                    if note:
                        say(f"WARNING: {note}")
                    code, result = run(project, video, a, skip, say)
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
