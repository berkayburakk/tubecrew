#!/usr/bin/env python3
"""voice_check.py - did the voiceover say the script?

What it does
    Compares what was actually said (a transcript, or the audio transcribed with faster-whisper) with the
    spoken script, word by word. It lists skipped phrases, changed numbers, stray repeats and misheard words
    with the transcript time, and reports word accuracy (matched script words / script words).

Why
    Text-to-speech skips words, reads "12.5" as "125" and repeats phrases; a recorded take drifts from the
    script. Listening for that across ten minutes is slow and unreliable; an aligned diff is fast and exact.

How to run
    python voice_check.py script.md voice/narration.srt
    python voice_check.py script.md voice/narration.wav --language en --model small

    Transcript formats: SRT, WebVTT, Whisper-style JSON ({"segments": [{"start", "end", "text",
    "words": [{"word", "start", "end"}]}]}, a plain list of segments, or a list of words), or plain text.
    Audio (wav, mp3, m4a, flac, ogg, opus, aac) is transcribed with faster-whisper when it is installed
    (`pip install faster-whisper`); the transcript is saved next to the report so a re-run can reuse it.

    The script is cleaned before comparing: headings, `>` note lines, HTML comments, code blocks, tables,
    `[[cue]]` anchors, bracketed stage directions such as [ON SCREEN: ...] and emphasis markers are dropped.
    A script that still contains {placeholder} tokens must be compiled first (exit 2).

    Report: <script folder>/qa/voice-check.json unless --out is given. Paths in it are relative.

Severity
    FAIL  any difference that involves a number; a skipped run of 3 or more script words
    WARN  an inserted run of 3+ words that repeats nearby script text (stray repeat); any other difference
          (often the recognizer mishearing a name)

Exit codes
    0  no FAIL     1  at least one FAIL     2  usage or environment error

Standard library only; faster-whisper is optional and imported only when audio is given.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

TOOL = "voice_check.py"
VERSION = "0.1.0"

SKIP_FAIL_WORDS = 3        # a run of 3+ missing script words is a skipped phrase, not recognizer noise
REPEAT_MIN_WORDS = 3       # an extra run this long that matches nearby script text is a stray repeat
REPEAT_WINDOW_WORDS = 40   # how far around the insertion point to look for the repeated text
CONTEXT_WORDS = 5          # script words shown on each side of a difference
HUMAN_MAX_ISSUES = 60      # issues printed in human output (all of them go to the report)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".aac"}
# Languages that write 1.000,5 instead of 1,000.5 (only used to read thousands separators consistently).
DECIMAL_COMMA_LANGS = {"tr", "az", "de", "fr", "es", "it", "pt", "nl", "ru", "uk", "pl", "cs", "sk", "ro", "hu",
                       "el", "bg", "hr", "sr", "sl", "da", "nb", "no", "sv", "fi", "id", "vi"}

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


class UsageError(Exception):
    """Raised for exit-code-2 problems."""


# ----------------------------------------------------------------------------------------------- helpers

def fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "--:--.-"
    seconds = max(0.0, float(seconds))
    minutes, sec = divmod(seconds, 60.0)
    if round(sec, 1) >= 60.0:
        minutes, sec = minutes + 1, 0.0
    return f"{int(minutes):02d}:{sec:04.1f}"


def rel_path(path: Path, base: Path) -> str:
    """Path relative to base in POSIX form; never absolute (falls back to the file name)."""
    try:
        rel = os.path.relpath(str(Path(path).resolve()), str(Path(base).resolve()))
    except ValueError:
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


def _lang(language: str | None) -> str:
    return (language or "").lower().split("-")[0].split("_")[0]


# ----------------------------------------------------------------------------------------------- script cleaning

_FRONT_MATTER_RE = re.compile(r"\A---\n.*?\n---[ \t]*(\n|\Z)", re.S)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", re.S | re.M)
_HEADING_RE = re.compile(r"^#{1,6}(\s|$)")
_RULE_RE = re.compile(r"^([-*_=])(\s*\1){2,}$")
_LIST_RE = re.compile(r"^(?:[-*+]|\d{1,3}[.)])\s+")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]\[]+)\]\([^)]*\)")
_CUE_RE = re.compile(r"\[\[[^\]]*\]\]")
_BRACKET_RE = re.compile(r"\[[^\]\n]*\]")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_EMPHASIS_RE = re.compile(r"\*+|~~|`+|(?<!\w)_+(?=\w)|(?<=\w)_+(?!\w)")
_PLACEHOLDER_RE = re.compile(r"\{[^{}\n]*\}")


def clean_script(markdown: str) -> str:
    """Markdown script -> only the words meant to be spoken, one paragraph per line."""
    text = markdown.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    text = _FRONT_MATTER_RE.sub("", text)
    text = _COMMENT_RE.sub(" ", text)
    text = _FENCE_RE.sub(" ", text)
    kept = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or _HEADING_RE.match(line) or line.startswith(">") or line.startswith("|"):
            continue
        if _RULE_RE.match(line):
            continue
        line = _LIST_RE.sub("", line)
        line = _IMAGE_RE.sub(" ", line)
        line = _LINK_RE.sub(r"\1", line)
        line = _CUE_RE.sub(" ", line)
        line = _BRACKET_RE.sub(" ", line)
        line = _HTML_TAG_RE.sub(" ", line)
        line = _EMPHASIS_RE.sub("", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            kept.append(line)
    return "\n".join(kept)


def find_placeholders(text: str) -> list[str]:
    return _PLACEHOLDER_RE.findall(text)


# ----------------------------------------------------------------------------------------------- normalizing

_APOSTROPHES = "'\u2019\u02bc\u2018`\u00b4"
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: (i + 2) * 10 for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_DIGIT_WORDS = {w: str(i) for w, i in _UNITS.items() if i < 10}
_PERCENT_WORDS = {"percent": "%"}
# Recognizers write "12.5 billion dollars" as "$12.5 billion"; both sides are folded to "$ 12.5 billion".
_CURRENCY_WORDS = {"dollar": "$", "dollars": "$", "euro": "€", "euros": "€", "pounds": "£",
                   "yen": "¥"}
_SCALE_WORDS = {"thousand", "million", "millions", "billion", "billions", "trillion", "trillions"}


def normalize_number(token: str, language: str = "en") -> str:
    """'1,000' -> '1000', '12,5' (decimal comma) -> '12.5'; the same rule on both sides keeps them comparable."""
    if "," not in token and "." not in token:
        return token
    decimal_comma = _lang(language) in DECIMAL_COMMA_LANGS
    thousands, decimal = (".", ",") if decimal_comma else (",", ".")
    if "," in token and "." in token:
        decimal = "." if token.rfind(".") > token.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        return token.replace(thousands, "").replace(decimal, ".")
    sep = "," if "," in token else "."
    parts = token.split(sep)
    if len(parts) > 2:
        if all(len(p) == 3 for p in parts[1:]):
            return "".join(parts)
        return token.replace(",", ".")
    if sep == thousands and len(parts[1]) == 3:
        return "".join(parts)
    return parts[0] + "." + parts[1]


def raw_tokens(text: str, language: str = "en") -> list[str]:
    """Text -> casefolded word tokens. Punctuation is dropped, apostrophes are joined, digits keep their
    decimal point, currency symbols and % become their own tokens. Non-ASCII letters are kept."""
    text = unicodedata.normalize("NFKC", text)
    if _lang(language) in ("tr", "az"):
        text = text.replace("I", "\u0131").replace("\u0130", "i")
    for ch in _APOSTROPHES:
        text = text.replace(ch, "")
    tokens: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        word = "".join(buf)
        buf.clear()
        if any(c.isdigit() for c in word) and all(c.isdigit() or c in ".," for c in word):
            tokens.append(normalize_number(word, language))
        else:
            tokens.append(unicodedata.normalize("NFC", word.casefold().replace("i\u0307", "i")))

    n = len(text)
    for i, ch in enumerate(text):
        cat = unicodedata.category(ch)
        if ch.isalnum() or cat.startswith("M"):
            buf.append(ch)
        elif ch in ".," and buf and buf[-1].isdigit() and i + 1 < n and text[i + 1].isdigit():
            buf.append(ch)
        elif cat == "Sc" or ch in "%\u2030":
            flush()
            tokens.append(ch)
        else:
            flush()
    flush()
    return tokens


def _english_number(tokens: list[str], i: int) -> tuple[str, int] | None:
    """Parse English number words starting at tokens[i] ('twenty five' -> '25', 'twelve point five' ->
    '12.5'). Returns (digits, tokens consumed) or None. million/billion stay words on both sides."""
    total, current, last, j, n = 0, 0, None, i, len(tokens)
    seen = False
    while j < n:
        w = tokens[j]
        if w in _UNITS:
            value = _UNITS[w]
            if last == "unit" or (last == "tens" and (value >= 10 or value == 0)):
                break
            current += value
            last = "unit"
        elif w in _TENS:
            if last in ("unit", "tens"):
                break
            current += _TENS[w]
            last = "tens"
        elif w == "hundred":
            if last in ("hundred",):
                break
            current = (current or 1) * 100
            last = "hundred"
        elif w == "thousand":
            if last in ("thousand",):
                break
            total += (current or 1) * 1000
            current = 0
            last = "thousand"
        elif (w == "and" and seen and last in ("hundred", "thousand") and j + 1 < n
              and (tokens[j + 1] in _UNITS or tokens[j + 1] in _TENS)):
            j += 1
            continue
        else:
            break
        seen = True
        j += 1
    if not seen:
        return None
    value = str(total + current)
    if j + 1 < n and tokens[j] == "point" and tokens[j + 1] in _DIGIT_WORDS:
        k, digits = j + 1, ""
        while k < n and tokens[k] in _DIGIT_WORDS:
            digits += _DIGIT_WORDS[tokens[k]]
            k += 1
        return value + "." + digits, k - i
    return value, j - i


def merge_tokens(tokens: list[str], language: str = "en") -> list[tuple[str, int]]:
    """Fold multi-token forms into one comparable token: English number words to digits, 'percent' to '%'.
    Returns (token, index of the first source token)."""
    out: list[tuple[str, int]] = []
    english = _lang(language) == "en"
    i, n = 0, len(tokens)
    while i < n:
        w = tokens[i]
        if english and (w in _UNITS or w in _TENS or w in ("hundred", "thousand")):
            parsed = _english_number(tokens, i)
            if parsed:
                out.append((parsed[0], i))
                i += parsed[1]
                continue
        if w in _PERCENT_WORDS:
            out.append((_PERCENT_WORDS[w], i))
            i += 1
            continue
        if w == "per" and i + 1 < n and tokens[i + 1] == "cent":
            out.append(("%", i))
            i += 2
            continue
        out.append((w, i))
        i += 1
    return _currency_first(out)


def _currency_first(items: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """'12.5 billion dollars' -> '$ 12.5 billion' (a currency word right after a number, optionally with a
    scale word in between, becomes a symbol in front of the number)."""
    result: list[tuple[str, int]] = []
    for tok, idx in items:
        symbol = _CURRENCY_WORDS.get(tok)
        if symbol:
            pos = None
            if result and any(c.isdigit() for c in result[-1][0]):
                pos = len(result) - 1
            elif len(result) >= 2 and result[-1][0] in _SCALE_WORDS and any(c.isdigit() for c in result[-2][0]):
                pos = len(result) - 2
            if pos is not None:
                if not (pos > 0 and result[pos - 1][0] == symbol):
                    result.insert(pos, (symbol, result[pos][1]))
                continue
        result.append((tok, idx))
    return result


def normalize(text: str, language: str = "en") -> list[str]:
    """Text -> the token list used for alignment."""
    return [t for t, _ in merge_tokens(raw_tokens(text, language), language)]


# ----------------------------------------------------------------------------------------------- transcripts

_STAMP = r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[,.](\d{1,3})"   # SRT 00:00:01,000 or VTT 00:01.000
_TIME_RE = re.compile(_STAMP + r"\s*-->\s*" + _STAMP)
_CUE_TAG_RE = re.compile(r"<[^>]*>|\{\\[^}]*\}")


def _secs(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000.0


def parse_cues(text: str) -> list[dict]:
    """SRT or WebVTT text -> [{"text", "start", "end"}]. VTT NOTE/STYLE blocks and inline tags are dropped.
    In WebVTT a line identical to the previous cue's line (rolling auto-captions) is not repeated; SRT keeps
    every line, because a repeated line there can be a real stray repeat."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    rolling = text.lstrip().startswith("WEBVTT")
    segments: list[dict] = []
    previous = None
    for block in re.split(r"\n\s*\n", text):
        rows = [r for r in block.split("\n") if r.strip()]
        if not rows or rows[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        for i, row in enumerate(rows):
            m = _TIME_RE.search(row)
            if not m:
                continue
            g = m.groups()
            lines = []
            for raw in rows[i + 1:]:
                line = html.unescape(_CUE_TAG_RE.sub("", raw)).strip()   # WebVTT escapes &amp; &lt; &gt;
                if line and not (rolling and line == previous):
                    lines.append(line)
                    previous = line
            if lines:
                segments.append({"text": " ".join(lines), "start": _secs(*g[:4]), "end": _secs(*g[4:]),
                                 "word_level": False})
            break
    return segments


def _float_or_none(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _word_items(items: list) -> list[dict]:
    out = []
    for w in items:
        if not isinstance(w, dict):
            continue
        text = w.get("word", w.get("text", ""))
        if text:
            out.append({"text": str(text), "start": _float_or_none(w.get("start")),
                        "end": _float_or_none(w.get("end")), "word_level": True})
    return out


def parse_json_transcript(data) -> list[dict]:
    """Whisper-style JSON -> [{"text", "start", "end", "word_level"}] (word-level when words are present)."""
    if isinstance(data, dict):
        if isinstance(data.get("segments"), list):
            items = data["segments"]
        elif isinstance(data.get("transcription"), list):  # whisper.cpp: offsets in milliseconds
            items = [{"text": s.get("text", ""),
                      "start": (s.get("offsets") or {}).get("from", 0) / 1000.0,
                      "end": (s.get("offsets") or {}).get("to", 0) / 1000.0} for s in data["transcription"]]
        elif isinstance(data.get("words"), list):
            return _word_items(data["words"])
        elif isinstance(data.get("text"), str):
            return [{"text": data["text"], "start": None, "end": None, "word_level": False}]
        else:
            raise UsageError("JSON transcript has no 'segments', 'words' or 'text'")
    elif isinstance(data, list):
        items = data
        if items and all(isinstance(x, dict) and "word" in x and "text" not in x for x in items):
            return _word_items(items)
    else:
        raise UsageError("JSON transcript must be an object or a list")
    out: list[dict] = []
    for seg in items:
        if not isinstance(seg, dict):
            continue
        words = seg.get("words")
        if isinstance(words, list) and words:
            out += _word_items(words)
        elif seg.get("text"):
            out.append({"text": str(seg["text"]), "start": _float_or_none(seg.get("start")),
                        "end": _float_or_none(seg.get("end")), "word_level": False})
    return out


def load_transcript(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        raise UsageError(f"cannot read transcript: {exc}") from exc
    ext = path.suffix.lower()
    stripped = text.lstrip()
    if ext == ".json" or (ext not in (".srt", ".vtt", ".txt") and stripped[:1] in "{["):
        try:
            return parse_json_transcript(json.loads(text))
        except json.JSONDecodeError as exc:
            raise UsageError(f"transcript is not valid JSON: {exc}") from exc
    if ext in (".srt", ".vtt") or stripped.startswith("WEBVTT") or "-->" in text:
        return parse_cues(text)
    return [{"text": text, "start": None, "end": None, "word_level": False}]


def heard_tokens(pieces: list[dict], language: str = "en") -> tuple[list[str], list[tuple]]:
    """Transcript pieces -> (tokens, [(start, end) per token]). Segment-level times are spread evenly over
    the segment's tokens (approximate); word-level times are exact."""
    raw: list[str] = []
    times: list[tuple] = []
    for piece in pieces:
        toks = raw_tokens(piece["text"], language)
        start, end = piece.get("start"), piece.get("end")
        for k, tok in enumerate(toks):
            raw.append(tok)
            if start is None:
                times.append((None, None))
            elif piece.get("word_level") or end is None or len(toks) == 1:
                times.append((start, end if end is not None else start))
            else:
                step = (end - start) / len(toks)
                times.append((start + step * k, start + step * (k + 1)))
    merged = merge_tokens(raw, language)
    tokens = [t for t, _ in merged]
    tok_times = [times[i] for _, i in merged]
    return tokens, tok_times


def transcribe(audio: Path, language: str | None, model_name: str) -> dict:
    """Transcribe audio with faster-whisper (word timestamps on). Raises UsageError when unavailable."""
    try:
        from faster_whisper import WhisperModel  # optional dependency, imported only here
    except ImportError as exc:
        raise UsageError("transcribing audio needs faster-whisper: pip install faster-whisper "
                         "(or pass an existing transcript: SRT, VTT or Whisper JSON)") from exc

    def run(device: str, compute_type: str) -> dict:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
        segments, info = model.transcribe(str(audio), language=_lang(language) or None, word_timestamps=True)
        out = []
        for seg in segments:
            words = [{"word": w.word, "start": round(w.start, 3), "end": round(w.end, 3)}
                     for w in (seg.words or [])]
            out.append({"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text, "words": words})
        return {"language": getattr(info, "language", language), "model": model_name, "segments": out}

    try:
        return run("auto", "default")
    except Exception as first:  # noqa: BLE001 - GPU runtimes fail in many ways; fall back to CPU once
        try:
            return run("cpu", "int8")
        except Exception as exc:  # noqa: BLE001
            raise UsageError(f"faster-whisper could not transcribe {audio.name}: {exc or first}") from exc


# ----------------------------------------------------------------------------------------------- alignment

def _is_number(token: str) -> bool:
    return any(c.isdigit() for c in token)


def _repeats_nearby(extra: list[str], script: list[str], at: int) -> bool:
    lo, hi = max(0, at - REPEAT_WINDOW_WORDS), min(len(script), at + REPEAT_WINDOW_WORDS)
    window = script[lo:hi]
    if not window:
        return False
    match = difflib.SequenceMatcher(None, extra, window, autojunk=False).find_longest_match(
        0, len(extra), 0, len(window))
    return match.size >= max(REPEAT_MIN_WORDS, int(0.6 * len(extra) + 0.5))


def compare(script: list[str], heard: list[str], times: list[tuple] | None = None) -> dict:
    """Align script tokens with heard tokens. Returns counts, word accuracy and the list of issues."""
    times = times or [(None, None)] * len(heard)
    matcher = difflib.SequenceMatcher(None, script, heard, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    issues = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        expected, said = script[i1:i2], heard[j1:j2]
        missing = len(expected) - len(said)
        extra = len(said) - len(expected)
        if any(_is_number(t) for t in expected + said):
            status, reason = FAIL, "number"
        elif missing >= SKIP_FAIL_WORDS:
            status, reason = FAIL, "skipped"
        elif extra >= REPEAT_MIN_WORDS and _repeats_nearby(said, script, i1):
            status, reason = WARN, "repeat"
        elif tag == "delete":
            status, reason = WARN, "missing"
        elif tag == "insert":
            status, reason = WARN, "extra"
        else:
            status, reason = WARN, "mismatch"
        if j2 > j1:
            start, end = times[j1][0], times[j2 - 1][1]
        elif j1 > 0:
            start = end = times[j1 - 1][1]
        elif heard:
            start = end = times[0][0]
        else:
            start = end = None
        before = " ".join(script[max(0, i1 - CONTEXT_WORDS):i1])
        after = " ".join(script[i2:i2 + CONTEXT_WORDS])
        issue = {"status": status, "reason": reason, "kind": tag, "expected": " ".join(expected),
                 "heard": " ".join(said), "context": f"{before} [{' '.join(expected) or '^'}] {after}".strip(),
                 "script_word": i1}
        if start is not None:
            issue["start"] = round(start, 2)
            issue["end"] = round(end if end is not None else start, 2)
        issues.append(issue)
    accuracy = matched / len(script) if script else 0.0
    return {"script_words": len(script), "heard_words": len(heard), "matched_words": matched,
            "word_accuracy": round(accuracy, 4), "issues": issues,
            "verdict": FAIL if any(i["status"] == FAIL for i in issues) else PASS}


# ----------------------------------------------------------------------------------------------- main

def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _display_path(path: Path, base: Path) -> str:
    shown = rel_path(path, base)
    if not shown.startswith(".."):
        return shown
    try:
        cwd_rel = os.path.relpath(str(path.resolve()), os.getcwd())
        return Path(cwd_rel).as_posix() if not os.path.isabs(cwd_rel) else path.name
    except ValueError:
        return path.name


def human_lines(report: dict, report_rel: str) -> list[str]:
    source = report.get("audio") or report.get("transcript")
    lines = [f"voice_check: {report['script']} vs {source}",
             f"Word accuracy: {report['word_accuracy'] * 100:.1f}% ({report['matched_words']}/"
             f"{report['script_words']} script words matched, {report['heard_words']} heard)"]
    issues = report["issues"]
    for issue in issues[:HUMAN_MAX_ISSUES]:
        when = fmt_time(issue.get("start")) if "start" in issue else "--:--.-"
        lines.append(f"{issue['status']:<4}  {when}  {issue['reason']:<8}  expected \"{issue['expected']}\"  "
                     f"heard \"{issue['heard']}\"  | {issue['context']}")
    if len(issues) > HUMAN_MAX_ISSUES:
        lines.append(f"... {len(issues) - HUMAN_MAX_ISSUES} more in the report")
    if not issues:
        lines.append("No differences.")
    lines.append(f"VERDICT: {report['verdict']}")
    lines.append(f"Report: {report_rel}")
    return lines


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Compare a voiceover (transcript or audio) with its script. "
                               "Exit 0 pass, 1 fail, 2 usage error.")
    parser.add_argument("script", help="the spoken script (Markdown or plain text)")
    parser.add_argument("transcript", help="SRT, VTT, Whisper JSON or plain-text transcript, or an audio file")
    parser.add_argument("--language", default="en", help="language code of the narration (default en)")
    parser.add_argument("--model", default="small", help="faster-whisper model for audio input (default small)")
    parser.add_argument("--out", help="report path (default <script folder>/qa/voice-check.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    script_path, source_path = Path(args.script), Path(args.transcript)
    base = script_path.resolve().parent
    out_path = Path(args.out) if args.out else base / "qa" / "voice-check.json"
    report: dict = {"tool": TOOL, "version": VERSION}
    try:
        if not script_path.is_file():
            raise UsageError(f"script not found: {args.script}")
        if not source_path.is_file():
            raise UsageError(f"transcript or audio not found: {args.transcript}")
        try:
            spoken = clean_script(script_path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError as exc:
            raise UsageError(f"cannot read script: {exc}") from exc
        placeholders = find_placeholders(spoken)
        if placeholders:
            shown = ", ".join(sorted(set(placeholders))[:5])
            raise UsageError(f"the script still has template placeholders ({shown}); compile the template to "
                             "the final spoken text first")
        script_tokens = normalize(spoken, args.language)
        if not script_tokens:
            raise UsageError("the script has no spoken text after removing headings, notes and directions")
        report["script"] = rel_path(script_path, base)
        report["script_sha256"] = sha256_file(script_path)

        if source_path.suffix.lower() in AUDIO_EXTENSIONS:
            data = transcribe(source_path, args.language, args.model)
            transcript_path = out_path.parent / (out_path.stem + "-transcript.json")
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            pieces = parse_json_transcript(data)
            report["audio"] = rel_path(source_path, base)
            report["audio_sha256"] = sha256_file(source_path)
            report["model"] = args.model
            report["transcript"] = rel_path(transcript_path, base)
        else:
            pieces = load_transcript(source_path)
            report["transcript"] = rel_path(source_path, base)
            report["transcript_sha256"] = sha256_file(source_path)
        heard, times = heard_tokens(pieces, args.language)
        if not heard:
            raise UsageError("no words found in the transcript (empty file or unsupported format?)")
    except UsageError as exc:
        print(f"voice_check: error: {exc}", file=sys.stderr)
        return 2

    result = compare(script_tokens, heard, times)
    if all(p.get("start") is None for p in pieces):
        timestamps = "none"
    elif all(p.get("word_level") for p in pieces):
        timestamps = "word"
    else:
        timestamps = "segment"
    report.update({
        "language": args.language,
        "timestamps": timestamps,
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "verdict": result["verdict"],
        "word_accuracy": result["word_accuracy"],
        "script_words": result["script_words"],
        "heard_words": result["heard_words"],
        "matched_words": result["matched_words"],
        "counts": {FAIL: sum(1 for i in result["issues"] if i["status"] == FAIL),
                   WARN: sum(1 for i in result["issues"] if i["status"] == WARN)},
        "issues": result["issues"],
    })
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"voice_check: error: cannot write report: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(report, _display_path(out_path, base))))
    return 1 if result["verdict"] == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
