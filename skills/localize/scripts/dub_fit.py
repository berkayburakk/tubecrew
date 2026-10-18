#!/usr/bin/env python3
"""dub_fit.py - does each dubbed paragraph fit the time the original narration gave it?

What it does
    Finds where every paragraph of the original narration starts (by aligning the source script with the
    narration's word-timed transcript), measures each dubbed paragraph's audio, and places the dub on the original
    timeline the way a dub track is assembled: a paragraph starts at its original anchor (up to 1 s early when it
    would not fit), may run up to 1 s past the next paragraph's anchor, keeps a 0.6 s gap, and is sped up only as
    much as needed. It reports the speed-up each paragraph needs, how far each start drifts from its anchor, and
    the total time that does not fit even at the speed cap.

Why
    A dub that slides away from the picture puts a number on screen while the narrator is talking about something
    else, and speech sped up past about 1.25x sounds rushed in any language. Both are cheap to measure before
    anyone mixes, and the fix is a shorter translation, not a faster voice.

How to run
    python dub_fit.py script.md qa/voice-check-transcript.json dub/es/voice --lang es
    python dub_fit.py script.md voice/narration.srt dub/de/voice/P01.wav dub/de/voice/P02.wav --lang de --json
    python dub_fit.py script.md qa/voice-check-transcript.json dub/ja/voice --lang ja --end 605.5 --no-trim

    script.md    the source-language script with paragraph ids at the start of a line (P01, **P01**)
    transcript   the original narration's transcript with timings: Whisper-style JSON with word timings (best;
                 voice_check.py saves one when it transcribes audio), SRT or WebVTT
    audio        a folder, or files, of dubbed paragraph audio; each file name holds its paragraph id
                 (P01.wav, p01-es.mp3, es_P01.m4a)

    --end is when the last paragraph must have finished (default: the end of the original narration). Pass a later
    time when the dub may run into a quiet outro. --source-language is the language of script.md (default en).
    The alignment reuses the check-voiceover skill's voice_check.py, so numbers written as digits in the script
    match numbers spoken as words in the transcript.

    Report: <script folder>/qa/dub-fit-<lang>.json unless --out is given. Paths in it are relative.

Thresholds
    FAIL  a paragraph needs more than 1.25x speed-up; a paragraph starts more than 4 s from its anchor;
          a script paragraph has no dubbed audio, or its audio is silent
    Leading and trailing silence (below -42 dB) is not counted when ffmpeg is on PATH (--no-trim counts it).

Exit codes
    0  every paragraph fits     1  at least one FAIL     2  usage or environment error

Standard library plus ffprobe (durations) and ffmpeg (silence trimming). WAV durations are read without ffprobe.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

TOOL = "dub_fit.py"
VERSION = "0.1.0"

SPEED_CAP = 1.25       # above this, sped-up speech sounds rushed; rewrite the translation shorter instead
DRIFT_S = 4.0          # a paragraph starting more than 4 s off its anchor no longer matches its pictures
EARLY_S = 1.0          # a paragraph may start up to 1 s before its anchor when it would not fit otherwise
LATE_S = 1.0           # and run up to 1 s past the next paragraph's anchor
GAP_S = 0.6            # pause kept between paragraphs, so the dub does not sound breathless
END_MARGIN_S = 0.3     # the last paragraph ends this long before --end
SILENCE_DB = -42       # quieter than this at the edges of a file is silence, not speech
SILENCE_MIN_S = 0.05   # shortest edge silence worth trimming
MIN_SPEECH_S = 0.05    # an audio file with less speech than this is silent (a failed generation)
MIN_MATCH_RATIO = 0.5  # below this share of matched script words, the transcript is not of this script
EPS = 1e-6

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
PARA_ID_RE = re.compile(r"^[Pp](\d+)\b[.:)]?\s*")
FILE_ID_RE = re.compile(r"(?:^|[^A-Za-z0-9])[Pp]0*(\d+)(?![0-9])")
VOICE_CHECK = Path(__file__).resolve().parents[2] / "check-voiceover" / "scripts" / "voice_check.py"

PASS, FAIL = "PASS", "FAIL"


class UsageError(Exception):
    """Raised for exit-code-2 problems."""


def load_voice_check():
    """Import the check-voiceover skill's voice_check.py by path (skill folders contain dashes)."""
    if not VOICE_CHECK.is_file():
        raise UsageError(f"needs the check-voiceover skill next to this one ({VOICE_CHECK.name} not found)")
    spec = importlib.util.spec_from_file_location("tubecrew_voice_check", VOICE_CHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------------------------------- script

def script_paragraphs(markdown: str, vc) -> tuple[list[dict], list[str]]:
    """Paragraphs that carry an id, in order: [{"id": "P01", "number": 1, "text": spoken text}], plus spoken lines
    that come before the first id (kept for the alignment, not reported)."""
    paras: list[dict] = []
    preamble: list[str] = []
    for line in vc.clean_script(markdown, keep_ids=True).split("\n"):
        m = PARA_ID_RE.match(line)
        if m:
            paras.append({"id": f"P{int(m.group(1)):02d}", "number": int(m.group(1)), "text": line[m.end():]})
        elif paras:
            paras[-1]["text"] = (paras[-1]["text"] + " " + line).strip()
        elif line.strip():
            preamble.append(line)
    seen = set()
    for p in paras:
        if p["number"] in seen:
            raise UsageError(f"paragraph id {p['id']} appears twice in the script")
        seen.add(p["number"])
    return paras, preamble


# ----------------------------------------------------------------------------------------------- transcript

def timed_tokens(pieces: list[dict], language: str, vc) -> tuple[list[str], list[tuple]]:
    """Transcript pieces -> (normalized tokens, [(start, end)]). Uses voice_check's own helper when it has one."""
    helper = getattr(vc, "heard_tokens", None)
    if helper is not None:
        return helper(pieces, language)
    tokens: list[str] = []
    times: list[tuple] = []
    for piece in pieces:
        toks = vc.normalize(piece["text"], language)
        start, end = piece.get("start"), piece.get("end")
        for k, tok in enumerate(toks):
            tokens.append(tok)
            if start is None:
                times.append((None, None))
            elif piece.get("word_level") or end is None or len(toks) == 1:
                times.append((start, end if end is not None else start))
            else:
                step = (end - start) / len(toks)
                times.append((start + step * k, start + step * (k + 1)))
    return tokens, times


def find_anchors(paras: list[dict], preamble: list[str], heard: list[str], times: list[tuple], language: str,
                 vc) -> tuple[list[float | None], float]:
    """Original start time of each paragraph (None when none of its words were matched) and the matched share."""
    script: list[str] = []
    for line in preamble:
        script += vc.normalize(line, language)
    ranges = []
    for p in paras:
        toks = vc.normalize(p["text"], language)
        ranges.append((len(script), len(script) + len(toks)))
        script += toks
    matcher = difflib.SequenceMatcher(None, script, heard, autojunk=False)
    opcodes = matcher.get_opcodes()
    matched = [False] * len(script)
    for tag, i1, i2, _, _ in opcodes:
        if tag == "equal":
            matched[i1:i2] = [True] * (i2 - i1)
    ratio = sum(matched) / len(script) if script else 0.0

    def heard_index(s0: int) -> int | None:
        """Heard token where script token s0 was spoken. A misheard first word (a replace block) still marks
        the start; skipped words (a delete block) map to the next word that was heard."""
        for tag, i1, i2, j1, j2 in opcodes:
            if tag == "insert" or not i1 <= s0 < i2:
                continue
            if tag == "equal":
                return j1 + (s0 - i1)
            if tag == "replace":
                return j1 + (s0 - i1) * (j2 - j1) // (i2 - i1)
            return j1 if j1 < len(heard) else None
        return None

    anchors: list[float | None] = []
    for s0, s1 in ranges:
        h = heard_index(s0) if any(matched[s0:s1]) else None   # a paragraph with no word heard is estimated
        anchors.append(times[h][0] if h is not None else None)
    return anchors, ratio


def fill_anchors(anchors: list[float | None], first_time: float, end: float) -> tuple[list[float], list[bool]]:
    """Estimate missing anchors evenly between their known neighbours; keep the anchors in order."""
    n = len(anchors)
    known = [i for i, a in enumerate(anchors) if a is not None]
    filled: list[float] = []
    estimated = []
    for i, a in enumerate(anchors):
        if a is not None:
            filled.append(a)
            estimated.append(False)
            continue
        before = max((k for k in known if k < i), default=None)
        after = min((k for k in known if k > i), default=None)
        t0, i0 = (anchors[before], before) if before is not None else (first_time, -1)
        t1, i1 = (anchors[after], after) if after is not None else (end, n)
        filled.append(t0 + (t1 - t0) * (i - i0) / (i1 - i0))
        estimated.append(True)
    for i in range(1, n):
        filled[i] = max(filled[i], filled[i - 1])
    return filled, estimated


# ----------------------------------------------------------------------------------------------- audio

def collect_audio(paths: list[Path]) -> dict[int, Path]:
    """Paragraph number -> audio file, from folders and files."""
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS)
        elif p.is_file():
            files.append(p)
        else:
            raise UsageError(f"audio not found: {p}")
    found: dict[int, Path] = {}
    for f in files:
        m = FILE_ID_RE.search(f.stem)
        if not m:
            raise UsageError(f"cannot tell which paragraph {f.name} is (name it P01.wav, p01-es.mp3, ...)")
        number = int(m.group(1))
        if number in found:
            raise UsageError(f"two audio files for P{number:02d}: {found[number].name} and {f.name}")
        found[number] = f
    return found


def duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        proc = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                               "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                              capture_output=True, text=True, errors="replace")
        try:
            return float(proc.stdout.strip().splitlines()[0])
        except (ValueError, IndexError):
            raise UsageError(f"ffprobe cannot read {path.name}: {proc.stderr.strip()[:200]}") from None
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError, OSError) as exc:
            raise UsageError(f"cannot read {path.name}: {exc}") from exc
    raise UsageError("ffprobe not found: install ffmpeg (https://ffmpeg.org), or use WAV files")


def parse_silence(stderr: str, total: float) -> tuple[float, float]:
    """ffmpeg silencedetect output -> (leading silence, trailing silence) in seconds."""
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", stderr)]
    if not starts:
        return 0.0, 0.0
    lead = 0.0
    if starts[0] <= 0.01:
        lead = ends[0] if ends else total
    trail = 0.0
    if len(starts) > len(ends) or (ends and ends[-1] >= total - 0.01):
        trail = max(0.0, total - starts[-1])
    if lead >= total - 0.01:
        return total, 0.0
    return lead, trail


def speech_length(path: Path, total: float, trim: bool) -> tuple[float, bool]:
    """Seconds of the file between its first and last sound; (total, False) when not trimmed."""
    ffmpeg = shutil.which("ffmpeg")
    if not trim or not ffmpeg:
        return total, False
    proc = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af",
                           f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_S}", "-f", "null", "-"],
                          capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise UsageError(f"ffmpeg cannot read {path.name}: {proc.stderr.strip()[-200:]}")
    lead, trail = parse_silence(proc.stderr, total)
    return max(0.0, total - lead - trail), True


# ----------------------------------------------------------------------------------------------- placement

def place(anchors: list[float], lengths: list[float], end: float) -> list[dict]:
    """Place each paragraph on the original timeline in order. Speed-up beyond the cap is reported, and the
    audio that still does not fit pushes the following paragraphs later (drift)."""
    out = []
    prev_end = None
    n = len(anchors)
    for i in range(n):
        limit = (anchors[i + 1] + LATE_S - GAP_S) if i < n - 1 else end - END_MARGIN_S
        floor = prev_end + GAP_S if prev_end is not None else 0.0
        start = max(floor, anchors[i])
        length = lengths[i]
        if start + length > limit:
            start = max(floor, anchors[i] - EARLY_S)
        room = limit - start
        needed = length / room if room > EPS else float("inf")
        played = length / min(max(1.0, needed), SPEED_CAP)
        overflow = max(0.0, played - max(room, 0.0))
        out.append({"start_s": start, "drift_s": start - anchors[i], "room_s": room, "speed": max(1.0, needed),
                    "overflow_s": overflow, "played_s": played})
        prev_end = start + played
    return out


def fit(paras: list[dict], anchors: list[float], estimated: list[bool], audio: dict[int, dict], end: float,
        narration_end: float) -> dict:
    """Judge every paragraph. audio maps paragraph number -> {"path", "duration_s", "speech_s"}."""
    n = len(paras)
    slots = [(anchors[i + 1] if i < n - 1 else narration_end) - anchors[i] for i in range(n)]
    lengths = []
    for i, p in enumerate(paras):
        a = audio.get(p["number"])
        # A missing paragraph is assumed to fill its own slot, so it neither hides nor causes later problems.
        lengths.append(a["speech_s"] if a else max(0.0, slots[i] - GAP_S))
    placed = place(anchors, lengths, end)
    rows = []
    for i, p in enumerate(paras):
        a = audio.get(p["number"])
        pl = placed[i]
        reasons = []
        if a is None:
            reasons.append("no dubbed audio")
        elif a["speech_s"] < MIN_SPEECH_S:
            reasons.append("audio is silent")
        if a is not None and pl["speed"] > SPEED_CAP + EPS:
            reasons.append(f"needs {pl['speed']:.2f}x (cap {SPEED_CAP:.2f}x)" if pl["speed"] != float("inf")
                           else "no room left")
        if abs(pl["drift_s"]) > DRIFT_S + EPS:
            reasons.append(f"starts {pl['drift_s']:+.1f} s from its anchor (limit {DRIFT_S:.0f} s)")
        shorten = None
        if a is not None and pl["overflow_s"] > EPS and a["speech_s"] > 0:
            shorten = min(100, math.ceil(100 * pl["overflow_s"] * SPEED_CAP / a["speech_s"] - 1e-9))
        speed = pl["speed"]
        rows.append({
            "id": p["id"],
            "anchor_s": round(anchors[i], 2),
            "anchor_estimated": estimated[i],
            "slot_s": round(slots[i], 2),
            "audio": a["path"] if a else None,
            "duration_s": round(a["duration_s"], 2) if a else None,
            "speech_s": round(a["speech_s"], 2) if a else None,
            "start_s": round(pl["start_s"], 2),
            "drift_s": round(pl["drift_s"], 2),
            "speed": round(speed, 3) if speed != float("inf") else None,
            "overflow_s": round(pl["overflow_s"], 2) if a else 0.0,
            "shorten_pct": shorten,
            "status": FAIL if reasons else PASS,
            "reasons": reasons,
        })
    voiced = [r for r in rows if r["speech_s"] is not None]
    dub_total = sum(r["speech_s"] for r in voiced)
    original_total = sum(rows[i]["slot_s"] for i in range(n) if rows[i]["speech_s"] is not None)
    speeds = [r["speed"] for r in voiced if r["speed"] is not None]
    summary = {
        "paragraphs": n,
        "failed": sum(r["status"] == FAIL for r in rows),
        "missing_audio": [r["id"] for r in rows if r["audio"] is None],
        "sped_up": sum(1 for s in speeds if s > 1.0 + EPS),
        "max_speed": round(max(speeds), 3) if speeds else None,
        "max_drift_s": round(max((abs(r["drift_s"]) for r in rows), default=0.0), 2),
        "overflow_s": round(sum(r["overflow_s"] for r in rows), 2),
        "dub_speech_s": round(dub_total, 2),
        "original_s": round(original_total, 2),
        "length_ratio": round(dub_total / original_total, 3) if original_total > 0 else None,
    }
    return {"paragraphs": rows, "summary": summary, "verdict": FAIL if summary["failed"] else PASS}


# ----------------------------------------------------------------------------------------------- output

def fmt_time(seconds: float) -> str:
    minutes, sec = divmod(max(0.0, seconds), 60.0)
    if round(sec, 1) >= 60.0:
        minutes, sec = minutes + 1, 0.0
    return f"{int(minutes)}:{sec:04.1f}"


def rel_path(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name


def human_lines(report: dict, report_rel: str) -> list[str]:
    s = report["summary"]
    lines = [f"Dub fit: {report['language']} ({s['paragraphs']} paragraphs, original narration ends at "
             f"{fmt_time(report['narration_end_s'])}, dub must end by {fmt_time(report['end_s'])})"]
    if not report["trimmed_silence"]:
        lines.append("  Edge silence counted (ffmpeg not found or --no-trim): results are a little pessimistic.")
    for r in report["paragraphs"]:
        anchor = fmt_time(r["anchor_s"]) + ("~" if r["anchor_estimated"] else " ")
        if r["audio"] is None:
            lines.append(f"  {r['id']}  anchor {anchor} slot {r['slot_s']:5.1f} s  FAIL no dubbed audio")
            continue
        speed = f"{r['speed']:.2f}x" if r["speed"] is not None else "  inf"
        line = (f"  {r['id']}  anchor {anchor} slot {r['slot_s']:5.1f} s  dub {r['speech_s']:5.1f} s  "
                f"start {r['drift_s']:+5.1f} s  speed {speed}  {r['status']}")
        if r["reasons"]:
            line += " " + "; ".join(r["reasons"])
        if r["shorten_pct"]:
            line += f"; rewrite about {r['shorten_pct']}% shorter"
        lines.append(line)
    max_speed = f"{s['max_speed']:.2f}x" if s["max_speed"] is not None else "n/a"
    ratio = (f", dub speech fills {s['length_ratio'] * 100:.0f}% of the original slots"
             if s["length_ratio"] else "")
    lines.append(f"Total: {s['failed']} FAIL, {s['sped_up']} sped up (max {max_speed}), max drift "
                 f"{s['max_drift_s']:.1f} s, overflow {s['overflow_s']:.1f} s{ratio}")
    if any(r["anchor_estimated"] for r in report["paragraphs"]):
        lines.append("  ~ anchor estimated: none of that paragraph's words were found in the transcript.")
    if report["unused_audio"]:
        lines.append("  Audio with no paragraph in the script: " + ", ".join(report["unused_audio"]))
    lines.append(f"Report: {report_rel}")
    lines.append(f"VERDICT: {report['verdict']}")
    return lines


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


# ----------------------------------------------------------------------------------------------- main

def run(args) -> tuple[dict, Path]:
    script_path = Path(args.script)
    transcript_path = Path(args.transcript)
    if not script_path.is_file():
        raise UsageError(f"script not found: {script_path}")
    if not transcript_path.is_file():
        raise UsageError(f"transcript not found: {transcript_path}")
    if transcript_path.suffix.lower() in AUDIO_EXTENSIONS:
        raise UsageError("pass the original narration's transcript, not its audio (voice_check.py saves a "
                         "transcript JSON when it transcribes audio)")
    if args.end is not None and args.end <= 0:
        raise UsageError("--end must be positive")
    vc = load_voice_check()
    try:
        markdown = script_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read {script_path}: {exc}") from exc
    paras, preamble = script_paragraphs(markdown, vc)
    if not paras:
        raise UsageError("the script has no paragraph ids: start each paragraph with P01, P02, ...")
    try:
        pieces = vc.load_transcript(transcript_path)
    except Exception as exc:  # noqa: BLE001 - voice_check raises its own usage error type
        raise UsageError(str(exc)) from exc
    heard, times = timed_tokens(pieces, args.source_language, vc)
    if not heard or any(t[0] is None for t in times):
        raise UsageError("the transcript has no timings: use Whisper JSON with word timings, SRT or WebVTT")
    raw, ratio = find_anchors(paras, preamble, heard, times, args.source_language, vc)
    if ratio < MIN_MATCH_RATIO:
        raise UsageError(f"the transcript does not match the script (only {ratio:.0%} of script words found); "
                         "check --source-language and the files")
    narration_end = max(t[1] if t[1] is not None else t[0] for t in times)
    end = args.end if args.end is not None else narration_end
    anchors, estimated = fill_anchors(raw, times[0][0], narration_end)
    if end <= anchors[-1]:
        raise UsageError(f"--end ({end:g} s) is before the last paragraph starts ({anchors[-1]:.1f} s)")

    base = script_path.parent
    files = collect_audio([Path(a) for a in args.audio])
    numbers = {p["number"] for p in paras}
    audio = {}
    trimmed_any = False
    for number, path in files.items():
        if number not in numbers:
            continue
        total = duration(path)
        speech, trimmed = speech_length(path, total, not args.no_trim)
        trimmed_any = trimmed_any or trimmed
        audio[number] = {"path": rel_path(path, base), "duration_s": total, "speech_s": speech}
    result = fit(paras, anchors, estimated, audio, end, narration_end)
    report = {
        "tool": TOOL,
        "version": VERSION,
        "created": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "language": args.lang,
        "source_language": args.source_language,
        "script": rel_path(script_path, base),
        "transcript": rel_path(transcript_path, base),
        "narration_end_s": round(narration_end, 2),
        "end_s": round(end, 2),
        "trimmed_silence": trimmed_any and not args.no_trim,
        "matched_words": round(ratio, 4),
        "thresholds": {"speed_cap": SPEED_CAP, "drift_s": DRIFT_S, "early_s": EARLY_S, "late_s": LATE_S,
                       "gap_s": GAP_S, "end_margin_s": END_MARGIN_S, "silence_db": SILENCE_DB},
        "unused_audio": sorted(rel_path(p, base) for n, p in files.items() if n not in numbers),
    }
    report.update(result)
    out = Path(args.out) if args.out else base / "qa" / f"dub-fit-{args.lang}.json"
    return report, out


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("script", help="the source-language script with paragraph ids (P01, P02, ...)")
    parser.add_argument("transcript", help="the original narration's transcript: Whisper JSON, SRT or VTT")
    parser.add_argument("audio", nargs="+", help="a folder or files of dubbed paragraph audio (P01.wav, ...)")
    parser.add_argument("--lang", required=True, help="language code of the dub, used in the report name")
    parser.add_argument("--source-language", default="en", help="language of script.md (default en)")
    parser.add_argument("--end", type=float, help="seconds by which the last paragraph must end "
                                                  "(default: end of the original narration)")
    parser.add_argument("--no-trim", action="store_true", help="count leading and trailing silence as speech")
    parser.add_argument("--out", help="report path (default <script folder>/qa/dub-fit-<lang>.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*", args.lang):
        print(f"error: --lang must be a language code such as es or pt-BR, not {args.lang!r}", file=sys.stderr)
        return 2
    try:
        report, out = run(args)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("\n".join(human_lines(report, rel_path(out, Path(args.script).parent))))
    return 1 if report["verdict"] == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
