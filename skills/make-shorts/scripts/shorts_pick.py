#!/usr/bin/env python3
"""shorts_pick.py - propose Short cuts from a finished long video's script and word-timed transcript.

What it does
    Splits the spoken script into sentences, times every sentence from the narration's word timings, and scores
    every run of whole sentences that fits the length band (default 20-60 s) on signals code can measure:

    - the pocket rule: the first sentence works for a stranger with no context. A window that opens on a
      back-reference ("And ...", "So ...", "This is why ...", "As we saw ...") or on an intro ("In this video",
      "subscribe") does not pass;
    - a number or a named, concrete thing in the first sentence; "you/your" early; no date-first opening;
    - a question that the window itself answers (asked before the last sentence);
    - a short last sentence (the payoff) that loops back to the first one; no call to action at the end; the
      next sentence does not start the turn ("But ...") that the window would cut off;
    - one idea: the window stays inside one script section (headings); length near the 25-45 s middle.

    With --retention (the JSON from retention-by-scene's retention.py --json) it re-ranks by real data: a window
    that holds a rewatch bump goes up, one that starts in or holds one of the steepest drops goes down. A report
    marked "judged": false (too few views or too early) is noise and is not used.

    The best non-overlapping windows (default 5) go to a table and to <project>/qa/shorts-candidates.json, with
    start/end on word boundaries and sentence ends, plus cut_start/cut_end with a short breathing pad that never
    reaches into the neighbouring words. shorts_cut.py --candidate N reads that file.

Why
    A Short cut from a long video fails in the first two seconds when it starts mid-thought, and fails again at the
    end when it stops before the payoff. Both are visible in the text before anything is rendered. The creator
    still chooses: this script only puts the measurable candidates in front of them.

How to run
    python shorts_pick.py <video-project> [--script script.md] [--words qa/voice-check-transcript.json]
                          [--words-offset 0] [--retention qa/retention.json] [--min 20] [--max 60] [--count 5]
                          [--out PATH] [--json]

    --script defaults to <project>/script.md; without it the sentences come from the transcript's punctuation
    (less exact). --words defaults to <project>/qa/voice-check-transcript.json (check-voiceover saves it); any
    Whisper-style JSON with word timings works. --words-offset is where the narration starts inside the video, so
    the times in the report are video times.

Exit codes
    0  at least one candidate passes (the pocket rule holds and it fits the band)
    1  no window passes
    2  usage or environment error (missing file, transcript without word timings, script not compiled)

Standard library only. Imports the parsers of check-voiceover's voice_check.py and check-render's render_check.py
from this plugin by file path.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import importlib.util
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

TOOL = "shorts_pick.py"
VERSION = "0.1.0"

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
RENDER_CHECK = PLUGIN_ROOT / "skills" / "check-render" / "scripts" / "render_check.py"
VOICE_CHECK = PLUGIN_ROOT / "skills" / "check-voiceover" / "scripts" / "voice_check.py"

# ----------------------------------------------------------------------------------------------- thresholds
DEFAULT_MIN_S = 20.0           # shorter windows rarely hold setup, turn and payoff
DEFAULT_MAX_S = 60.0           # the classic Shorts length; --max up to 180 (YouTube's limit for square/vertical)
SHORTS_MAX_S = 180.0           # support.google.com/youtube/answer/15424877 (since 15 Oct 2024)
TARGET_BAND = (25.0, 45.0)     # a starting point, not a law: calibrate it on your own retention data
DEFAULT_COUNT = 5
OVERLAP_MAX = 0.2              # two candidates may share at most 20% of the shorter one
WORDS_MIN_MATCH = 0.5          # under half the script words found in the transcript = another script or audio
SENTENCE_WEAK_MATCH = 0.6      # a sentence with fewer exact word matches has approximate times
PAD_START_S = 0.12             # breathing room before the first word ...
PAD_END_S = 0.35               # ... and after the last; never past the middle of the gap to the next word
HOOK_WORDS = 15                # "early" = the first 15 words
FIRST_SENTENCE_MAX_WORDS = 20  # at speaking pace 2 s carry ~5 words; a very long first sentence delays the point
PAYOFF_MAX_WORDS = 12          # a payoff line is short and lands on something concrete
PAYOFF_LONG_WORDS = 22
LOOP_MIN_SHARED = 2            # content words the last sentence shares with the first = it loops back

POINTS = {
    "base": 50,
    "backref_start": -40,      # opens on a back-reference: fails the pocket rule
    "intro_start": -25,        # opens on an intro or "subscribe": fails the pocket rule
    "number_first": 20,
    "concrete_first": 8,       # no number, but a named thing (a capitalised word inside the sentence)
    "you_early": 5,
    "date_open": -10,
    "long_first": -5,
    "question_answered": 15,
    "section_cross": -15,
    "payoff_short": 10,
    "payoff_long": -5,
    "ends_on_question": -10,
    "loop": 10,
    "cta_end": -10,
    "open_end": -10,           # ends on ":" or "..." (mid-thought)
    "turn_after": -10,         # the next sentence starts with "But ..." (the payoff may come after the cut)
    "length": 15,              # full points inside TARGET_BAND, less outside
    "weak_timing": -5,
}
RETENTION_BUMP_MAX = 25
RETENTION_DROP_MAX = 25
DROP_OPEN_BEFORE_S = 3.0       # a drop starting 3 s before to 5 s after the window start = it opens inside a cliff
DROP_OPEN_AFTER_S = 5.0
DROP_END_GRACE_S = 2.0         # a drop after the payoff (last 2 s) is expected in the long video

_NUMBER_RE = re.compile(
    r"\d|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
    r"billion|trillion|percent|half|double|triple|dozen)\b", re.I)
_YOU_RE = re.compile(r"\b(?:you|your|yours|you're|you've|you'll)\b", re.I)
_DATE_OPEN_RE = re.compile(r"^(?:in|back in|by|since|around)\s+(?:the\s+)?(?:\d{4}|\d{2}s\b|nineteen|eighteen|"
                           r"seventeen|sixteen|twenty|two thousand)", re.I)
_INTRO_RE = re.compile(r"\b(?:subscribe|welcome (?:back )?to|in (?:this|today's) (?:video|episode)|"
                       r"today (?:we|i)(?:'re| are| am|'m| will|'ll)|hey (?:guys|everyone|everybody|there)|"
                       r"hi (?:guys|everyone|everybody)|what's up|let's (?:get started|dive in|begin)|"
                       r"before we (?:start|begin|get started))\b", re.I)
_CTA_RE = re.compile(r"\b(?:subscribe|hit the bell|like (?:this|the) video|smash (?:that|the) like|comment below|"
                     r"in the comments|link (?:is )?(?:in|below)|watch the full|full video)\b", re.I)
_BACKREF_FIRST = {
    "and", "but", "so", "or", "nor", "then", "also", "because", "which", "that", "that's", "thats", "this",
    "these", "those", "it", "it's", "its", "they", "they're", "them", "their", "he", "she", "his", "her", "him",
    "such", "both", "yet", "however", "therefore", "thus", "meanwhile", "instead", "otherwise", "plus", "again",
    "still", "anyway", "besides", "moreover", "furthermore", "likewise", "similarly", "hence", "consequently",
    "afterwards", "later", "next", "finally", "first", "second", "third", "lastly", "now",
}
_BACKREF_PHRASES = ("as i said", "as we saw", "as we've seen", "as you saw", "as mentioned", "like i said",
                    "like we said", "remember", "earlier", "at that point", "at the time", "back then", "by then",
                    "after that", "before that", "in that case", "the same", "the other", "the rest", "the latter",
                    "the former", "on top of that", "even so", "not only that")
_TURN_FIRST = {"but", "however", "yet", "except", "until", "instead", "unless", "still"}
_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for", "with", "by", "from", "is", "are",
    "was", "were", "be", "been", "it", "its", "this", "that", "these", "those", "as", "so", "if", "then", "than",
    "you", "your", "we", "our", "they", "their", "he", "she", "his", "her", "i", "my", "me", "do", "does", "did",
    "not", "no", "yes", "just", "what", "who", "how", "why", "when", "where", "which", "there", "here", "have",
    "has", "had", "will", "would", "can", "could", "into", "about", "one", "all", "more", "most", "very", "out",
}
_ABBREV = {"mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "inc", "ltd", "co", "corp", "jr", "sr", "no", "fig",
           "approx", "e.g", "i.e", "u.s", "u.k", "u.n", "a.m", "p.m"}
_CLOSERS = "\"')]}”’»"
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(\s|$)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


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
vc = _load(VOICE_CHECK, "tubecrew_voice_check")


# ----------------------------------------------------------------------------------------------- helpers

def fmt_time(seconds) -> str:
    if seconds is None:
        return "--:--.-"
    minutes, sec = divmod(max(0.0, float(seconds)), 60.0)
    if round(sec, 1) >= 60.0:
        minutes, sec = minutes + 1, 0.0
    return f"{int(minutes):02d}:{sec:04.1f}"


def rel_path(path: Path, base: Path) -> str:
    try:
        rel = os.path.relpath(str(Path(path).resolve()), str(Path(base).resolve()))
    except ValueError:
        return Path(path).name
    if os.path.isabs(rel) or rel == ".." or rel.startswith(".." + os.sep) or rel.startswith("../"):
        return Path(path).name
    return Path(rel).as_posix()


def plain(word: str) -> str:
    """Lower case, accents and edge punctuation dropped; inner apostrophes kept ("That's" -> "that's")."""
    word = unicodedata.normalize("NFKD", word.replace("’", "'"))
    word = "".join(c for c in word if not unicodedata.combining(c)).casefold()
    return word.strip(".,;:!?\"'()[]{}“”«»-–—…")


def content_words(sentence: str) -> set:
    return {w for w in (plain(t) for t in sentence.split()) if len(w) > 2 and w not in _STOP and w.isalnum()}


# ----------------------------------------------------------------------------------------------- sentences (pure)

def split_sentences(paragraph: str) -> list:
    """A paragraph -> its sentences. A sentence ends at . ! ? (or an ellipsis) when the next word starts with a
    capital, a digit, a quote or a currency sign; abbreviations (Mr., U.S.) and single initials do not end one."""
    words = paragraph.split()
    out, current = [], []
    for i, word in enumerate(words):
        current.append(word)
        core = word.rstrip(_CLOSERS)
        if not core.endswith((".", "!", "?", "…")):
            continue
        if i + 1 == len(words):
            break
        stem = core.rstrip(".!?…").casefold()
        if core.endswith(".") and not core.endswith("..") and (stem in _ABBREV or (len(stem) == 1 and stem.isalpha())):
            continue
        nxt = words[i + 1].lstrip("\"'(“‘«")
        if nxt[:1].isupper() or nxt[:1].isdigit() or nxt[:1] in "$€£":
            out.append(" ".join(current))
            current = []
    if current:
        out.append(" ".join(current))
    return out


def script_sections(markdown: str) -> list:
    """Markdown script -> [text of each section]; a heading starts a new section (fenced blocks are skipped)."""
    sections, current, fenced = [], [], False
    for line in markdown.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _FENCE_RE.match(line):
            fenced = not fenced
        if not fenced and _HEADING_RE.match(line):
            sections.append("\n".join(current))
            current = []
            continue
        current.append(line)
    sections.append("\n".join(current))
    return sections


def script_sentences(markdown: str) -> list:
    """Spoken script -> [{"text", "section", "paragraph"}]. Uses voice_check's cleaner, so the same words count
    as spoken here as in the voiceover check (notes, tables, [stage directions] and headings are dropped)."""
    out, paragraph_no = [], 0
    for section_no, text in enumerate(script_sections(markdown)):
        spoken = vc.clean_script(text)
        if vc.find_placeholders(spoken):
            raise UsageError("the script still contains {placeholder} tokens: compile it first")
        for para in vc.paragraph_texts(spoken):
            paragraph_no += 1
            for sentence in split_sentences(para):
                out.append({"text": sentence, "section": section_no, "paragraph": paragraph_no})
    return out


def transcript_sentences(words: list) -> list:
    """No script: sentences from the transcript's own punctuation (Whisper words carry it)."""
    out, current = [], []
    for i, w in enumerate(words):
        current.append(i)
        if w["w"].rstrip(_CLOSERS).endswith((".", "!", "?")):
            out.append(current)
            current = []
    if current:
        out.append(current)
    return [{"text": " ".join(words[i]["w"] for i in idx), "section": 0, "paragraph": 0,
             "first": idx[0], "last": idx[-1], "match": 1.0, "exact_edges": True} for idx in out]


def align_sentences(sentences: list, words: list) -> float:
    """Times each script sentence from the transcript: word-level diff (difflib), equal runs one to one, replaced
    runs proportionally. Writes first/last word indexes, the share of exactly matched words and whether both edge
    words matched exactly into each sentence. Returns the overall exact-match share."""
    tokens, owner = [], []
    for si, s in enumerate(sentences):
        for raw in s["text"].split():
            norm = rc.normalize_word(raw)
            if norm:
                tokens.append(norm)
                owner.append(si)
    spoken = [rc.normalize_word(w["w"]) for w in words]
    if not tokens or not spoken:
        return 0.0
    link = [None] * len(tokens)
    exact = [False] * len(tokens)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, tokens, spoken, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                link[i1 + k], exact[i1 + k] = j1 + k, True
        elif op == "replace":
            n, m = i2 - i1, j2 - j1
            for k in range(n):
                link[i1 + k] = j1 + max(0, -(-(k + 1) * m // n) - 1)
    by_sentence: dict = {}
    for ti, si in enumerate(owner):
        by_sentence.setdefault(si, []).append(ti)
    for si, s in enumerate(sentences):
        idx = by_sentence.get(si, [])
        linked = [link[t] for t in idx if link[t] is not None]
        if not linked:
            s.update(first=None, last=None, match=0.0, exact_edges=False)
            continue
        s.update(first=min(linked), last=max(linked), match=sum(exact[t] for t in idx) / len(idx),
                 exact_edges=exact[idx[0]] and exact[idx[-1]])
    return sum(exact) / len(tokens)


def time_sentences(sentences: list, words: list) -> list:
    """Adds start/end (first word start, last word end) and cut_start/cut_end (with the breathing pad, never past
    the middle of the gap to the neighbouring word). Untimed sentences keep None."""
    for s in sentences:
        if s.get("first") is None:
            s.update(start=None, end=None, cut_start=None, cut_end=None)
            continue
        first, last = s["first"], s["last"]
        start, end = words[first]["s"], words[last]["e"]
        cut_start = max(0.0, start - PAD_START_S)
        if first > 0:
            cut_start = max(cut_start, (words[first - 1]["e"] + start) / 2)
        cut_end = end + PAD_END_S
        if last + 1 < len(words):
            cut_end = min(cut_end, (end + words[last + 1]["s"]) / 2)
        s.update(start=round(start, 3), end=round(end, 3), cut_start=round(min(cut_start, start), 3),
                 cut_end=round(max(cut_end, end), 3))
    return sentences


# ----------------------------------------------------------------------------------------------- scoring (pure)

def _first_words(text: str, n: int) -> list:
    return [plain(t) for t in text.split()][:n]


def backref_start(sentence: str):
    """The back-reference a sentence opens with ("And", "This is why", "As we saw"), or None."""
    words = [w for w in _first_words(sentence, 6) if w]
    if not words:
        return None
    if words[0] in _BACKREF_FIRST:
        return words[0]
    head = " ".join(words)
    for phrase in _BACKREF_PHRASES:
        if head == phrase or head.startswith(phrase + " "):
            return phrase
    return None


def has_concrete_name(sentence: str) -> bool:
    """A capitalised word after the first one (a name, a company, a place): a concrete thing to picture."""
    for raw in sentence.split()[1:]:
        word = raw.strip(_CLOSERS + "\"'(“‘,.;:!?")
        if len(word) > 1 and word[0].isupper() and plain(word) not in {"i", "i'm", "i've", "i'll"}:
            return True
    return False


def length_points(duration: float) -> float:
    lo, hi = TARGET_BAND
    if lo <= duration <= hi:
        return float(POINTS["length"])
    distance = lo - duration if duration < lo else duration - hi
    return round(max(0.0, POINTS["length"] - 0.75 * distance), 1)


def score_window(sentences: list, i: int, j: int) -> dict:
    """Text signals for the window of sentences i..j (inclusive). Pure."""
    first, last = sentences[i], sentences[j]
    window = sentences[i:j + 1]
    duration = last["end"] - first["start"]
    signals = []

    def add(name: str, detail: str, points=None):
        signals.append({"name": name, "points": POINTS.get(name, 0) if points is None else points,
                        "detail": detail})

    ref = backref_start(first["text"])
    if ref:
        add("backref_start", f"opens on \"{ref}\": a stranger has no context for it")
    if _INTRO_RE.search(first["text"]):
        add("intro_start", "opens on an intro or a call to subscribe: the swipe decision is spent on nothing")
    head = " ".join(first["text"].split()[:FIRST_SENTENCE_MAX_WORDS])
    if _NUMBER_RE.search(head):
        add("number_first", "a number in the first sentence")
    elif has_concrete_name(first["text"]):
        add("concrete_first", "names a concrete thing in the first sentence")
    early = " ".join(" ".join(s["text"] for s in window).split()[:HOOK_WORDS])
    if _YOU_RE.search(early):
        add("you_early", f"\"you\" in the first {HOOK_WORDS} words")
    if _DATE_OPEN_RE.search(first["text"]):
        add("date_open", "opens on a date: put today's stake first, the history second")
    if len(first["text"].split()) > FIRST_SENTENCE_MAX_WORDS:
        add("long_first", f"first sentence has {len(first['text'].split())} words: the point comes late")
    asked = [s for s in window[:-1] if s["text"].rstrip(_CLOSERS).endswith("?")]
    if asked:
        add("question_answered", "asks a question and answers it inside the window")
    if len({s["section"] for s in window}) > 1:
        add("section_cross", "crosses a section heading: probably two ideas")
    last_words = len(last["text"].split())
    if last_words <= PAYOFF_MAX_WORDS:
        add("payoff_short", f"short last line ({last_words} words)")
    elif last_words > PAYOFF_LONG_WORDS:
        add("payoff_long", f"long last line ({last_words} words): the payoff does not land")
    tail = last["text"].rstrip(_CLOSERS)
    if tail.endswith("?"):
        add("ends_on_question", "ends on a question: its answer is outside the Short")
    elif tail.endswith((":", "...", "…", "-", "—")):
        add("open_end", "ends mid-thought")
    if j > i and len(content_words(first["text"]) & content_words(last["text"])) >= LOOP_MIN_SHARED:
        add("loop", "the last line echoes the first: the Short can loop")
    if _CTA_RE.search(last["text"]):
        add("cta_end", "ends on a call to action instead of the payoff")
    if j + 1 < len(sentences) and sentences[j + 1]["section"] == last["section"]:
        nxt = _first_words(sentences[j + 1]["text"], 1)
        if nxt and nxt[0] in _TURN_FIRST:
            add("turn_after", f"the next sentence starts with \"{nxt[0]}\": the turn may come after the cut")
    if any(s["match"] < SENTENCE_WEAK_MATCH for s in (first, last)) or not (first["exact_edges"] and
                                                                           last["exact_edges"]):
        add("weak_timing", "an edge word was not matched exactly in the transcript: check the cut by ear")
    add("length", f"{duration:.1f} s (middle band {TARGET_BAND[0]:g}-{TARGET_BAND[1]:g} s)",
        length_points(duration))
    score = POINTS["base"] + sum(s["points"] for s in signals)
    pocket = not ref and not _INTRO_RE.search(first["text"])
    return {"score": round(max(0.0, min(100.0, score)), 1), "signals": signals, "pocket": pocket,
            "duration": round(duration, 3)}


def windows(sentences: list, min_s: float, max_s: float) -> list:
    """Every run of whole, timed sentences i..j whose spoken length lies in [min_s, max_s]."""
    out = []
    for i, first in enumerate(sentences):
        if first.get("start") is None:
            continue
        for j in range(i, len(sentences)):
            last = sentences[j]
            if last.get("end") is None:
                continue
            duration = last["end"] - first["start"]
            if duration > max_s:
                break
            if duration >= min_s:
                out.append((i, j))
    return out


def retention_adjust(start: float, end: float, retention: dict | None) -> tuple:
    """(points, notes) from a retention.py report: rewatch bumps inside the window add, steep drops at its start or
    inside it subtract. None or an unjudged report -> (0, [])."""
    if not retention or retention.get("judged") is False or retention.get("data") is False:
        return 0.0, []
    bump_points, drop_points, notes = 0.0, 0.0, []
    for bump in retention.get("rewatch_bumps") or []:
        try:
            b0, b1, rise = float(bump["start"]), float(bump["end"]), float(bump.get("rise_pp") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if b1 > start and b0 < end:
            bump_points += 8 + 3 * rise
            notes.append(f"rewatch bump {fmt_time(b0)}-{fmt_time(b1)} (+{rise:.1f} pp)")
    for drop in retention.get("steepest_drops") or []:
        try:
            d0, d1, loss = float(drop["start"]), float(drop["end"]), float(drop.get("loss_pp") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if start - DROP_OPEN_BEFORE_S <= d0 <= start + DROP_OPEN_AFTER_S:
            drop_points += 20
            notes.append(f"opens inside a steep drop {fmt_time(d0)}-{fmt_time(d1)} (-{loss:.1f} pp)")
        elif start + DROP_OPEN_AFTER_S < d0 < end - DROP_END_GRACE_S:
            drop_points += 10
            notes.append(f"steep drop inside {fmt_time(d0)}-{fmt_time(d1)} (-{loss:.1f} pp)")
    return round(min(bump_points, RETENTION_BUMP_MAX) - min(drop_points, RETENTION_DROP_MAX), 1), notes


def overlap(a: tuple, b: tuple) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    shorter = min(a[1] - a[0], b[1] - b[0])
    return inter / shorter if shorter > 0 else 0.0


def slug(text: str, taken: set) -> str:
    """A file-safe name from the first content words of a sentence, unique within `taken`."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    parts = [p for p in re.findall(r"[a-z0-9]+", ascii_text) if p not in _STOP][:4] or ["short"]
    base = "-".join(parts)[:40].strip("-") or "short"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    taken.add(name)
    return name


def pick(sentences: list, min_s: float, max_s: float, count: int, retention: dict | None = None) -> dict:
    """Scores every window, re-ranks with retention, keeps the best non-overlapping ones. Pure."""
    scored = []
    for i, j in windows(sentences, min_s, max_s):
        item = score_window(sentences, i, j)
        first, last = sentences[i], sentences[j]
        adjust, notes = retention_adjust(first["cut_start"], last["cut_end"], retention)
        item.update(i=i, j=j, text_score=item["score"], retention_adjust=adjust, retention_notes=notes,
                    score=round(max(0.0, min(100.0, item["score"] + adjust)), 1))
        scored.append(item)
    scored.sort(key=lambda c: (-c["score"], sentences[c["i"]]["start"]))

    def choose(pool: list, limit: int) -> list:
        kept = []
        for c in pool:
            span = (sentences[c["i"]]["start"], sentences[c["j"]]["end"])
            if any(overlap(span, (sentences[k["i"]]["start"], sentences[k["j"]]["end"])) > OVERLAP_MAX for k in kept):
                continue
            kept.append(c)
            if len(kept) == limit:
                break
        return kept

    passing = choose([c for c in scored if c["pocket"]], count)
    near = [] if passing else choose([c for c in scored if not c["pocket"]], 3)
    taken: set = set()
    out = []
    for rank, c in enumerate(passing + near, 1):
        first, last = sentences[c["i"]], sentences[c["j"]]
        out.append({
            "rank": rank if c["pocket"] else None,
            "name": slug(first["text"], taken),
            "start": first["start"], "end": last["end"],
            "cut_start": first["cut_start"], "cut_end": last["cut_end"],
            "duration": round(last["cut_end"] - first["cut_start"], 3),
            "spoken_s": c["duration"],
            "sentences": [c["i"] + 1, c["j"] + 1],
            "first_line": first["text"], "last_line": last["text"],
            "text": " ".join(s["text"] for s in sentences[c["i"]:c["j"] + 1]),
            "score": c["score"], "text_score": c["text_score"], "retention_adjust": c["retention_adjust"],
            "retention_notes": c["retention_notes"], "pocket": c["pocket"], "signals": c["signals"],
        })
    return {"candidates": [c for c in out if c["pocket"]], "near_misses": [c for c in out if not c["pocket"]],
            "windows_scored": len(scored)}


# ----------------------------------------------------------------------------------------------- input

def load_retention(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise UsageError(f"cannot read the retention report {path.name}: {exc}") from exc
    if not isinstance(data, dict) or not ("steepest_drops" in data or "rewatch_bumps" in data or
                                          data.get("judged") is False or data.get("data") is False):
        raise UsageError(f"{path.name} is not a retention.py --json report (no steepest_drops or rewatch_bumps)")
    return data


def retention_summary(retention: dict | None) -> dict | None:
    if retention is None:
        return None
    if retention.get("data") is False:
        return {"used": False, "why": "no retention data yet"}
    if retention.get("judged") is False:
        return {"used": False, "why": "not judged (too early or too few engaged views): noise, not used"}
    return {"used": True, "video_id": retention.get("video_id"), "duration_s": retention.get("duration_s"),
            "rewatch_bumps": len(retention.get("rewatch_bumps") or []),
            "steepest_drops": len(retention.get("steepest_drops") or [])}


def build_sentences(script: Path | None, words: list) -> tuple:
    """(sentences, match ratio, source). Raises UsageError when the transcript does not belong to the script."""
    if script is None:
        sentences = transcript_sentences(words)
        return time_sentences(sentences, words), 1.0, "transcript"
    try:
        markdown = script.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read the script: {exc}") from exc
    sentences = script_sentences(markdown)
    if not sentences:
        raise UsageError(f"no spoken sentences in {script.name}")
    ratio = align_sentences(sentences, words)
    if ratio < WORDS_MIN_MATCH:
        raise UsageError(f"only {ratio:.0%} of the script's words are in the transcript: a different script or "
                         "audio version; pass the transcript of this narration")
    return time_sentences(sentences, words), ratio, "script"


# ----------------------------------------------------------------------------------------------- output

def signal_tags(c: dict) -> str:
    tags = []
    for s in c["signals"]:
        if s["name"] == "length" or not s["points"]:
            continue
        tags.append(("+" if s["points"] > 0 else "-") + s["name"])
    if c["retention_adjust"]:
        tags.append(f"retention {c['retention_adjust']:+g}")
    return " ".join(tags)


def excerpt(text: str, limit: int = 58) -> str:
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def human_lines(report: dict, report_rel: str) -> list:
    lines = [f"shorts_pick: {report['sentences']} sentences from the {report['sentences_from']} "
             f"(word match {report['match_ratio']:.0%}), {report['windows_scored']} windows of "
             f"{report['band']['min']:g}-{report['band']['max']:g} s scored"]
    ret = report.get("retention")
    if ret:
        lines.append("retention: " + ("used" if ret["used"] else f"not used ({ret['why']})"))
    rows = report["candidates"] or report["near_misses"]
    if not report["candidates"]:
        lines.append("No window passes the pocket rule. Closest (they open on a back-reference or an intro):")
    lines.append(f"{'#':<3} {'window':<15} {'len':>6} {'score':>5}  first line / signals")
    for c in rows:
        rank = str(c["rank"]) if c["rank"] else "-"
        lines.append(f"{rank:<3} {fmt_time(c['cut_start'])}-{fmt_time(c['cut_end'])} {c['duration']:5.1f}s "
                     f"{c['score']:5.1f}  {excerpt(c['first_line'])}")
        lines.append(f"{'':<36}{c['name']}: {signal_tags(c)}")
    lines.append(f"VERDICT: {report['verdict']} ({len(report['candidates'])} candidate(s))")
    lines.append(f"Report: {report_rel}")
    if report["candidates"]:
        lines.append("Next: show these to the creator; cut the approved ones with shorts_cut.py --candidate N")
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
        prog=TOOL, description="Propose Short cuts from a long video's script and word-timed transcript. "
                               "Exit 0 when at least one candidate passes, 1 when none does, 2 usage error.")
    parser.add_argument("project", help="the long video's project folder")
    parser.add_argument("--script", help="spoken script (default <project>/script.md; without one the sentences "
                                         "come from the transcript)")
    parser.add_argument("--words", help="word-timed transcript JSON (default <project>/qa/voice-check-transcript.json)")
    parser.add_argument("--words-offset", type=float, default=0.0,
                        help="where the narration starts inside the video, in seconds (default 0)")
    parser.add_argument("--retention", help="retention.py --json report of the long video, to re-rank by real data")
    parser.add_argument("--min", type=float, default=DEFAULT_MIN_S, dest="min_s",
                        help=f"shortest window in seconds (default {DEFAULT_MIN_S:g})")
    parser.add_argument("--max", type=float, default=DEFAULT_MAX_S, dest="max_s",
                        help=f"longest window in seconds (default {DEFAULT_MAX_S:g}, at most {SHORTS_MAX_S:g})")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"candidates to keep (default {DEFAULT_COUNT})")
    parser.add_argument("--out", help="report path (default <project>/qa/shorts-candidates.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    try:
        if rc is None or vc is None:
            raise UsageError("make-shorts needs the check-render and check-voiceover skills of this plugin "
                             "(render_check.py, voice_check.py) next to it")
        project = Path(args.project)
        if not project.is_dir():
            raise UsageError(f"project folder not found: {args.project}")
        if not 1.0 <= args.min_s < args.max_s <= SHORTS_MAX_S:
            raise UsageError(f"--min and --max must satisfy 1 <= min < max <= {SHORTS_MAX_S:g} (YouTube's Shorts "
                             "limit for square and vertical uploads)")
        if not 1 <= args.count <= 20:
            raise UsageError("--count must be between 1 and 20")
        if args.script:
            script = Path(args.script)
            if not script.is_file():
                raise UsageError(f"script not found: {args.script}")
        else:
            script = project / "script.md"
            script = script if script.is_file() else None
        words_path = Path(args.words) if args.words else project / "qa" / "voice-check-transcript.json"
        if not words_path.is_file():
            raise UsageError(f"word-timed transcript not found: {rel_path(words_path, project)} (run check-voiceover, "
                             "or pass --words)")
        try:
            words = rc.load_words(words_path, args.words_offset)
        except rc.UsageError as exc:
            raise UsageError(str(exc)) from exc
        retention = load_retention(Path(args.retention)) if args.retention else None
        sentences, ratio, source = build_sentences(script, words)
    except UsageError as exc:
        print(f"shorts_pick: error: {exc}", file=sys.stderr)
        return 2

    result = pick(sentences, args.min_s, args.max_s, args.count, retention)
    verdict = "PASS" if result["candidates"] else "FAIL"
    report = {
        "tool": TOOL, "version": VERSION,
        "created_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": project.resolve().name,
        "script": rel_path(script, project) if script else None,
        "words": rel_path(words_path, project), "words_offset": args.words_offset,
        "sentences_from": source, "sentences": len(sentences), "match_ratio": round(ratio, 3),
        "untimed_sentences": sum(1 for s in sentences if s.get("start") is None),
        "band": {"min": args.min_s, "max": args.max_s, "middle": list(TARGET_BAND)},
        "retention": retention_summary(retention),
        "windows_scored": result["windows_scored"],
        "verdict": verdict,
        "candidates": result["candidates"],
        "near_misses": result["near_misses"],
        "not_measured": ["whether the picture at the cut start makes sense on its own",
                         "whether the transcript misheard words in the window (read the text before captioning)"],
    }
    out_path = Path(args.out) if args.out else project / "qa" / "shorts-candidates.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"shorts_pick: error: cannot write report: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(report, rel_path(out_path, project))))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
