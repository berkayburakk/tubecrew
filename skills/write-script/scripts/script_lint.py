"""Mechanical counts for a spoken script: length, runtime, long sentences, AI vocabulary and other patterns.

Why: a listener cannot re-read a sentence, and a few words mark a narration as machine-written within seconds.
The countable part of that is cheap to measure, so a reviewer can spend their attention on meaning and taste.

Only spoken text is counted. Skipped: lines starting with `#` (headings) or `>` (notes), tables, HTML comments,
fenced code, front matter, `[[cue]]` anchors, bracketed directions like `[ON SCREEN: ...]` and markdown emphasis
markers. Paragraph ids at the start of a line (`P01 ...` or `**P01** ...`) are kept as locations; without them a
location is a line number (`L12`). A `{key}` placeholder counts as 3 words (a spoken number is rarely one word).

Reported: words, runtime at --wpm, sentences (average, longest), every sentence over 30 words, runs of
sentences that open with the same word, AI-vocabulary hits, vague attributions ("studies show", "experts agree")
in sentences that name no source, setup phrases ("here's the thing"), "not X, but Y" / "isn't just ... it's"
contrasts, em/en dashes, "What if" openers, fragment runs (3+ sentences under 5 words), exclamation marks, the
words spoken in the first 5 seconds and the final sentence. With --phrase: the second at which the phrase starts
and whether that is within 30 s.

Opening checks (warnings, never a gate; seconds are estimated from word counts at --wpm):
  - "you already know" / "bear with me" / greeting phrases anywhere in the first ~17 s, or an "Imagine" opener
  - no concrete number (digit, %, number word or {placeholder}) in the first ~12 s
  - a first sentence over 12 words
  - "in this video" in the first sentence (read together with the next one when it is very short)
  - an intro or brand marker (`[INTRO]`, `[BRAND: ...]`, `[[intro]]`) that lands after ~25 s (aim for ~20 s)
  - the opening triangle: a proper noun and a concrete object (with the number above) in the first ~12 s

--triggers FILE checks that the words on-screen animations are keyed to still exist after a rewrite. FILE is a
JSON list of {"word", "nth", "paragraph"} (nth counts from 0, paragraph is optional) or plain lines
"[P05] word [nth]". Words are compared lower-cased with punctuation other than $ % - removed. A missing trigger
would not break a render; the animation would silently slide to the start of its paragraph.

Run:
  python script_lint.py script.md
  python script_lint.py script.md --phrase "how does a credit card make money" --gate --out qa/script-lint.json
  python script_lint.py script.md --gate --allow leverage,landscape --json
  python script_lint.py script.md --triggers visuals/triggers.json

--gate fails (exit 1) on any sentence over 30 words, any AI-vocabulary hit or unsourced vague attribution not in
--allow, or a phrase that starts after 30 s (or is missing). A missing trigger exits 1 even without --gate. Other
pattern counts and the opening checks are guidance, never a gate. The English word lists find nothing in other
languages; the length, runtime, number, first-sentence, intro and phrase metrics still work there.

Exit codes: 0 pass (or no --gate), 1 gate failed or a trigger is missing, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - older or redirected streams
        pass

LONG_SENTENCE = 30
PHRASE_LIMIT_S = 30.0
OPENING_S = 5.0
FRAGMENT_WORDS = 5   # a fragment has fewer words than this
FRAGMENT_RUN = 3
PLACEHOLDER_WEIGHT = 3
GUIDE_LINE = "Counts guide the reviewer; the reviewer decides."

# Opening checks: warnings only. Retention curves show the first half-minute is where most viewers decide.
KNOWN_WINDOW_S = 17.0      # viewers leave between ~6 and ~17 s when told they already know the story
NUMBER_WINDOW_S = 12.0     # a number, a name and an object in the first ~12 s give the viewer something to hold
FIRST_SENTENCE_MAX = 12    # about 5 s at 155 wpm: the first sentence fits the 5-second promise
FIRST_BEAT_WORDS = 8       # "in this video" is looked for in sentence 1, plus sentence 2 if sentence 1 is shorter
INTRO_TARGET_S = 20.0      # an intro or brand sting belongs after the hook, at about 20 s
INTRO_LATE_S = 25.0        # warn only past 25 s, so ordinary pacing differences do not trigger it

# (term, regex, case_sensitive). Bare forms only where an inflection is ordinary English ("elevated levels").
AI_VOCAB = [
    ("delve", r"delv(?:e|es|ed|ing)", False),
    ("tapestry", r"tapestr(?:y|ies)", False),
    ("testament", r"testaments?", False),
    ("underscore", r"underscor(?:e|es|ed|ing)", False),
    ("pivotal", r"pivotal", False),
    ("landscape", r"landscapes?", False),
    ("realm", r"realms?", False),
    ("seamless", r"seamless(?:ly)?", False),
    ("robust", r"robust(?:ly|ness)?", False),
    ("leverage", r"leverag(?:e|es|ed|ing)", False),
    ("crucial", r"crucial(?:ly)?", False),
    ("vibrant", r"vibrant(?:ly)?", False),
    ("intricate", r"intricate(?:ly)?", False),
    ("meticulous", r"meticulous(?:ly)?", False),
    ("embark", r"embark(?:s|ed|ing)?", False),
    ("foster", r"foster(?:s|ed|ing)?", True),  # lower case only: "Foster" is often a surname
    ("elevate", r"elevat(?:e|es|ing)", False),
    ("unlock", r"unlock(?:s|ed|ing)?", False),
    ("game-changer", r"game[- ]?changers?|game[- ]changing", False),
    ("multifaceted", r"multi-?faceted", False),
    ("ever-evolving", r"ever[- ]evolving", False),
    ("cutting-edge", r"cutting[- ]edge", False),
    ("paradigm shift", r"paradigm shifts?", False),
    ("showcase", r"showcas(?:e|es|ed|ing)", False),
    ("in today's world", r"in today's (?:world|fast-paced world|digital age|digital world)", False),
    ("at the end of the day", r"at the end of the day", False),
    ("it's worth noting", r"it(?:'s| is) worth noting", False),
]
SETUP_PHRASES = [
    ("here's the thing", r"here's the thing"),
    ("here's the twist", r"here's the twist"),
    ("here's the catch", r"(?:but )?here's the catch"),
    ("here's where it gets interesting", r"here's where it gets (?:interesting|wild|crazy)"),
    ("let's dive in", r"let's dive (?:in|into|deep)"),
    ("buckle up", r"buckle up"),
    ("let's break it down", r"let's break (?:it|this|that) down"),
    ("let's take a closer look", r"let's take a (?:closer )?look"),
    ("picture this", r"picture this"),
    ("make no mistake", r"make no mistake"),
    ("the truth is", r"the truth is"),
]
# Unnamed authority. Gated like AI vocabulary, because it reads as invented evidence; a sentence that names its
# source in the same breath ("studies show, according to the OECD, ...") is not counted.
VAGUE_ATTRIBUTIONS = [
    ("experts agree", r"experts (?:agree|agreed|agreeing)"),
    ("experts say", r"experts (?:say|said|saying)"),
    ("studies show", r"studies (?:show|showed|shown|showing|have shown|suggest|suggested)"),
    ("research shows", r"research (?:shows|showed|shown|showing|has shown|suggests|suggested)"),
    ("many argue", r"many (?:argue|argued|arguing|believe|believed)"),
    ("some say", r"some (?:say|said|saying|argue|argued|believe|believed)"),
    ("widely regarded", r"widely (?:regarded|considered|believed|seen)"),
    ("it is believed", r"it(?: is|'s) (?:believed|said|thought|widely known)"),
    ("scientists believe", r"scientists (?:believe|believed|believing|agree|agreed|say|said)"),
]
# Words that, next to a proper noun, mark a named source: "a Gallup poll", "data from the Census Bureau".
SOURCE_NOUNS = {"study", "studies", "survey", "surveys", "report", "reports", "paper", "papers", "poll", "polls",
                "analysis", "data", "review", "research", "filing", "filings", "researchers", "economists",
                "scientists", "analysts", "census", "institute", "university", "journal"}
SOURCE_PREPS = {"by", "from", "per", "at"}
# Opening phrases that cost viewers (family, phrases). Curly apostrophes are flattened before matching.
OPENING_PHRASES = [
    ("already know", ["you already know", "you already knew", "you have known", "you've heard", "you have heard",
                      "you've probably heard", "you probably know", "we all know", "we've all heard",
                      "everyone knows", "everybody knows"]),
    ("asks for patience", ["bear with me", "stay with me", "before we start", "before we begin",
                           "before we get started"]),
    ("greeting", ["welcome back", "welcome to the channel", "welcome to my channel", "hey guys", "hi guys",
                  "hey everyone", "hi everyone", "hello everyone", "don't forget to subscribe"]),
]
OPENING_ADVICE = {
    "already know": "it tells the viewer there is nothing new here",
    "asks for patience": "it asks for patience instead of earning it",
    "greeting": "a greeting or channel pitch delays the promise",
}
THIS_VIDEO_RE = re.compile(r"\b(?:in|of) (?:this|today's) (?:video|episode)\b|\btoday's video\b|"
                           r"\bthis video (?:is|will|shows)\b", re.I)
NUMBER_WORDS_RE = re.compile(r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|"
                             r"fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?|billions?|"
                             r"trillions?|half|dozens?|percent)\b", re.I)
NAME_WITH_DIGIT_RE = re.compile(r"\b[A-Za-z]+-\d+\b")   # GPT-5, F-35: names, not quantities
# Concrete, picturable things (a proxy: it cannot see the screen, only whether the words name an object).
CONCRETE_OBJECT_RE = re.compile(
    r"\b(?:coins?|pennies|penny|cents?|dollars?|bills?|banknotes?|gold|silver|copper|bars?|ingots?|vaults?|"
    r"safes?|wallets?|purses?|cards?|receipts?|che(?:que|ck)s?|tickets?|stamps?|shares?|certificates?|bonds?|"
    r"contracts?|letters?|ledgers?|books?|maps?|films?|cameras?|houses?|homes?|farms?|tractors?|fields?|"
    r"factor(?:y|ies)|warehouses?|stores?|shops?|ships?|boats?|trains?|trucks?|planes?|cars?|phones?|laptops?|"
    r"computers?|chips?|screens?|keys?|doors?|counters?|windows?|tables?|cups?|bread|loa(?:f|ves)|eggs?|"
    r"bottles?|jars?|boxes?|bags?|envelopes?|papers?|dice|lottery|rings?|shoes?)\b", re.I)
INTRO_MARK_RE = re.compile(r"\[\[?\s*(?:intro|brand(?:ing)?|title card|logo sting)\b[^\]\n]*\]\]?", re.I)
FIRST_PERSON_CAPS = {"i", "i'm", "i've", "i'll", "i'd"}
CONTRAST_RES = [
    re.compile(r"\bnot\s+(?:just|only|merely|simply)\b[^.?!;]{1,80}?\bbut\b", re.I),
    re.compile(r"\bnot\b[^.?!;]{1,60}?,\s*but\b", re.I),
    re.compile(r"\b(?:isn't|is not|wasn't|was not|aren't|are not|weren't|were not)\s+(?:just|only|merely|simply|"
               r"about)\b[^?!]{0,120}?\b(?:it's|it is|it was|they're|they are|that's|this is)\b", re.I),
    re.compile(r"\b(?:it's|it is|that's|this is|it was)\s+not\b[^?!]{0,80}?[,.;:—–-]\s*"
               r"(?:it's|it is|that's|this is|it was)\b", re.I),
]
ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "etc", "e.g", "i.e", "inc", "corp", "ltd",
                 "co", "fig", "approx", "dept", "est", "u.s", "u.k", "a.m", "p.m"}
# Abbreviations only before a number: "No. 1", "Oct. 18"; "The answer is no. Then..." still ends a sentence.
DIGIT_ABBREVIATIONS = {"no", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
ARTICLES = {"the", "a", "an"}
PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_][\w.\-]*\}")
PARA_ID_RE = re.compile(r"^\s*(?:\*\*|__)?(P\d+)(?:\*\*|__)?[.:)]?(?:\s+|$)")


# ---------------------------------------------------------------------------------------------------------------
# Reading the spoken text
# ---------------------------------------------------------------------------------------------------------------

def _clean(s: str) -> str:
    s = re.sub(r"\[\[[^\]]*\]\]", " ", s)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\*\*|__|~~|`", "", s)
    s = re.sub(r"(?<!\w)[*_]|[*_](?!\w)", "", s)
    s = s.replace("’", "'").replace("‘", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", s).strip()


def paragraphs(text: str) -> list[dict]:
    """Spoken paragraphs: {"id": "P01" | None, "pieces": [(line number, cleaned text), ...]}."""
    out: list[dict] = []
    current = None
    in_comment = in_fence = in_front = False

    def close():
        nonlocal current
        if current and current["pieces"]:
            out.append(current)
        current = None

    for i, raw in enumerate(text.splitlines(), start=1):
        if i == 1 and raw.strip() == "---":
            in_front = True
            continue
        if in_front:
            if raw.strip() in ("---", "..."):
                in_front = False
            continue
        if raw.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            close()
            continue
        if in_fence:
            continue
        kept, rest = "", raw
        while rest:
            if in_comment:
                end = rest.find("-->")
                rest, in_comment = ("", True) if end < 0 else (rest[end + 3:], False)
                continue
            start = rest.find("<!--")
            if start < 0:
                kept, rest = kept + rest, ""
            else:
                kept, rest, in_comment = kept + rest[:start] + " ", rest[start + 4:], True
        s = kept.strip()
        if not s or s.startswith(("#", ">", "|")) or re.fullmatch(r"[-*_=\s]{3,}", s):
            close()
            continue
        m = PARA_ID_RE.match(s)
        if m:
            close()
            s = s[m.end():]
        cleaned = _clean(s)
        if current is None:
            current = {"id": m.group(1) if m else None, "pieces": []}
        if cleaned:
            current["pieces"].append((i, cleaned))
    close()
    return out


def split_sentences(text: str) -> list[tuple[int, str]]:
    """(start offset, sentence). A period before a lower-case word or after an abbreviation does not end one."""
    out: list[tuple[int, str]] = []
    n = len(text)
    start = 0
    while start < n and text[start].isspace():
        start += 1
    i = start
    while i < n:
        ch = text[i]
        if ch in ".?!…":
            j = i + 1
            while j < n and text[j] in ".?!…":
                j += 1
            while j < n and text[j] in "\"'”’)»":
                j += 1
            if j >= n or text[j].isspace():
                k = j
                while k < n and text[k].isspace():
                    k += 1
                period_like = text[i:j].rstrip("\"'”’)»")[-1:] in (".", "…")
                word = re.findall(r"\S+$", text[start:i])
                word = word[0].lower().lstrip("(\"'“") if word else ""
                abbreviation = text[i:j] == "." and (word.rstrip(".") in ABBREVIATIONS
                                                     or (word.rstrip(".") in DIGIT_ABBREVIATIONS
                                                         and k < n and text[k].isdigit())
                                                     or re.fullmatch(r"(?:[a-z]\.)+[a-z]", word) is not None)
                lower_next = k < n and text[k].islower()
                if not abbreviation and not (period_like and lower_next):
                    sentence = text[start:j].strip()
                    if sentence:
                        out.append((start, sentence))
                    start = i = k
                    continue
            i = j
            continue
        i += 1
    tail = text[start:].strip()
    if tail:
        out.append((start, tail))
    return out


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\S+", text) if re.search(r"\w", t)]


def token_weight(tok: str) -> int:
    return PLACEHOLDER_WEIGHT if PLACEHOLDER_RE.search(tok) else 1


def word_count(text: str) -> int:
    return sum(token_weight(t) for t in tokens(text))


def _norm_token(tok: str) -> str:
    return re.sub(r"^[^\w{]+|[^\w}]+$", "", tok.lower())


def sentences_of(text: str) -> list[dict]:
    """Every spoken sentence with its location, line and word count."""
    out = []
    for p in paragraphs(text):
        joined, offsets = "", []
        for line, piece in p["pieces"]:
            if joined:
                joined += " "
            offsets.append((len(joined), line))
            joined += piece
        for start, s in split_sentences(joined):
            line = next((ln for off, ln in reversed(offsets) if off <= start), offsets[0][1])
            out.append({"loc": p["id"] or f"L{line}", "line": line, "text": s, "words": word_count(s),
                        "paragraph": p["id"]})
    return out


# ---------------------------------------------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------------------------------------------

def _first_words(s: str, n: int = 8) -> str:
    t = s.split()
    return " ".join(t[:n]) + (" ..." if len(t) > n else "")


def _opener(s: str) -> str:
    words = [_norm_token(t) for t in tokens(s)]
    words = [w for w in words if w]
    if not words:
        return ""
    if words[0] in ARTICLES and len(words) > 1:
        return " ".join(words[:2])
    return words[0]


def find_vocab(sentences: list[dict], allow: set[str]) -> list[dict]:
    hits = []
    for idx, s in enumerate(sentences):
        for term, pattern, case_sensitive in AI_VOCAB:
            flags = 0 if case_sensitive else re.I
            for m in re.finditer(r"\b(?:" + pattern + r")\b", s["text"], flags):
                allowed = term.lower() in allow or m.group(0).lower() in allow
                hits.append({"term": term, "match": m.group(0), "loc": s["loc"], "line": s["line"],
                             "allowed": allowed, "_order": (idx, m.start())})
    hits.sort(key=lambda h: h.pop("_order"))
    return hits


def find_phrases(sentences: list[dict], table) -> list[dict]:
    hits = []
    for s in sentences:
        for name, pattern in table:
            for m in re.finditer(r"\b" + pattern + r"\b", s["text"], re.I):
                hits.append({"phrase": name, "match": m.group(0), "loc": s["loc"], "line": s["line"]})
    return hits


def find_contrasts(text_by_para: list[tuple[str, int, str]]) -> list[dict]:
    hits = []
    for loc, line, text in text_by_para:
        spans = []
        for rx in CONTRAST_RES:
            spans.extend((m.start(), m.end(), m.group(0)) for m in rx.finditer(text))
        spans.sort()
        last_end = -1
        for a, b, g in spans:
            if a < last_end:
                continue
            last_end = b
            hits.append({"loc": loc, "line": line, "match": _first_words(g, 10)})
    return hits


def repeated_openings(sentences: list[dict]) -> list[dict]:
    runs, i = [], 0
    while i < len(sentences):
        key = _opener(sentences[i]["text"])
        j = i + 1
        while key and j < len(sentences) and _opener(sentences[j]["text"]) == key:
            j += 1
        if key and j - i >= 2:
            runs.append({"opener": key, "count": j - i, "loc": sentences[i]["loc"], "line": sentences[i]["line"]})
        i = j
    return runs


def fragment_runs(sentences: list[dict]) -> list[dict]:
    runs, i = [], 0
    while i < len(sentences):
        j = i
        while j < len(sentences) and sentences[j]["words"] < FRAGMENT_WORDS:
            j += 1
        if j - i >= FRAGMENT_RUN:
            runs.append({"count": j - i, "loc": sentences[i]["loc"], "line": sentences[i]["line"],
                         "sentences": [s["text"] for s in sentences[i:j]]})
        i = max(j, i + 1)
    return runs


def phrase_timing(sentences: list[dict], phrase: str, wpm: float) -> dict:
    stream = [(_norm_token(t), token_weight(t)) for s in sentences for t in tokens(s["text"])]
    target = [_norm_token(t) for t in tokens(phrase.replace("’", "'").replace("‘", "'"))]  # as _clean does
    target = [t for t in target if t]
    result = {"phrase": phrase, "found": False, "word_index": None, "start_s": None, "within_limit": False}
    if not target:
        return result
    words = [w for w, _ in stream]
    cum = 0
    for idx in range(len(stream)):
        if words[idx:idx + len(target)] == target:
            start_s = cum / wpm * 60.0
            result.update(found=True, word_index=cum, start_s=round(start_s, 1),
                          within_limit=start_s <= PHRASE_LIMIT_S)
            return result
        cum += stream[idx][1]
    return result


def opening_words(sentences: list[dict], wpm: float, seconds: float = OPENING_S) -> str:
    budget = int(seconds * wpm / 60.0)
    picked, used = [], 0
    for s in sentences:
        for t in tokens(s["text"]):
            if used >= budget:
                return " ".join(picked)
            picked.append(t)
            used += token_weight(t)
    return " ".join(picked)


# ---------------------------------------------------------------------------------------------------------------
# Vague attributions
# ---------------------------------------------------------------------------------------------------------------

def _is_proper(tok: str) -> bool:
    word = tok.strip(".,;:!?\"'“”‘’()[]—–-…")
    if not word or not word[0].isalpha() or not word[0].isupper():
        return False
    return word.lower() not in FIRST_PERSON_CAPS


def names_source(sentence: str) -> bool:
    """True when the sentence says where the claim comes from: "according to ...", or a proper noun next to a
    source word ("a Gallup poll", "a 2023 paper in Nature") or after by/from/per/at ("data from the OECD").
    A name that is the sentence's first word is not recognized (every sentence starts with a capital)."""
    if re.search(r"\baccording to\b", sentence, re.I):
        return True
    toks = tokens(sentence)
    norm = [_norm_token(t) for t in toks]
    for i in range(1, len(toks)):
        if not _is_proper(toks[i]):
            continue
        if SOURCE_NOUNS & set(norm[max(0, i - 2):i] + norm[i + 1:i + 3]):
            return True
        prev = norm[i - 1]
        if prev in SOURCE_PREPS or (prev in ARTICLES and i >= 2 and norm[i - 2] in SOURCE_PREPS):
            return True
    return False


def find_vague(sentences: list[dict], allow: set[str]) -> list[dict]:
    hits = []
    for idx, s in enumerate(sentences):
        named = None
        for term, pattern in VAGUE_ATTRIBUTIONS:
            for m in re.finditer(r"\b(?:" + pattern + r")\b", s["text"], re.I):
                if named is None:
                    named = names_source(s["text"])
                allowed = term in allow or m.group(0).lower() in allow
                hits.append({"term": term, "match": m.group(0), "loc": s["loc"], "line": s["line"],
                             "named_source": named, "allowed": allowed, "_order": (idx, m.start())})
    hits.sort(key=lambda h: h.pop("_order"))
    return hits


# ---------------------------------------------------------------------------------------------------------------
# Opening checks (warnings only)
# ---------------------------------------------------------------------------------------------------------------

def _timed_stream(sentences: list[dict], wpm: float) -> list[dict]:
    """Every spoken token with the estimated second it starts at."""
    out, cum = [], 0
    for si, s in enumerate(sentences):
        for k, t in enumerate(tokens(s["text"])):
            out.append({"tok": t, "sent": si, "first": k == 0, "s": cum / wpm * 60.0, "loc": s["loc"]})
            cum += token_weight(t)
    return out


def _is_number_token(tok: str) -> bool:
    if PLACEHOLDER_RE.search(tok):
        return True  # placeholders are filled from facts.json, nearly always with a number
    return bool(re.search(r"\d|%", NAME_WITH_DIGIT_RE.sub(" ", tok))) or bool(NUMBER_WORDS_RE.search(tok))


def _opening_phrases(stream: list[dict], seconds: float) -> list[dict]:
    """Opening phrases that start within `seconds` (a phrase may end a few words later)."""
    inside = sum(1 for w in stream if w["s"] < seconds)
    pool = stream[:inside + 6]
    joined, starts = "", []
    for w in pool:
        if joined:
            joined += " "
        starts.append(len(joined))
        joined += w["tok"]
    hits, seen = [], set()
    for family, phrases in OPENING_PHRASES:
        for phrase in phrases:
            for m in re.finditer(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", joined, re.I):
                idx = max(i for i, st in enumerate(starts) if st <= m.start())
                if idx >= inside or idx in seen:
                    continue
                seen.add(idx)
                hits.append({"family": family, "phrase": m.group(0), "start_s": round(pool[idx]["s"], 1),
                             "loc": pool[idx]["loc"], "_idx": idx})
    hits.sort(key=lambda h: h.pop("_idx"))
    return hits


def intro_position(text: str, wpm: float) -> dict | None:
    """Where the first intro/brand marker sits, in spoken words and estimated seconds. Table and note lines are
    skipped, so a change table that mentions [INTRO] does not count."""
    offset = 0
    for line in text.splitlines(keepends=True):
        if not line.lstrip().startswith(("|", ">")):
            m = INTRO_MARK_RE.search(line)
            if m:
                words = sum(s["words"] for s in sentences_of(text[:offset + m.start()]))
                return {"marker": m.group(0), "words_before": words, "start_s": round(words / wpm * 60.0, 1)}
        offset += len(line)
    return None


def opening_checks(text: str, sentences: list[dict], wpm: float) -> dict:
    stream = _timed_stream(sentences, wpm)
    warnings = []

    def warn(code: str, message: str, **extra) -> None:
        warnings.append(dict({"code": code, "message": message}, **extra))

    for h in _opening_phrases(stream, KNOWN_WINDOW_S):
        warn("opening_phrase", f"\"{h['phrase']}\" at ~{h['start_s']:.0f} s ({h['loc']}): "
             f"{OPENING_ADVICE[h['family']]}; keep it out of the first ~{KNOWN_WINDOW_S:.0f} s",
             family=h["family"], phrase=h["phrase"], loc=h["loc"], start_s=h["start_s"])
    if stream and _norm_token(stream[0]["tok"]) == "imagine":
        warn("imagine_opener", "opens with \"Imagine\": open on the real thing instead", loc=stream[0]["loc"])

    window = [w for w in stream if w["s"] < NUMBER_WINDOW_S]
    first_number = next((w for w in stream if _is_number_token(w["tok"])), None)
    first_number_s = round(first_number["s"], 1) if first_number else None
    has_number = first_number is not None and first_number["s"] < NUMBER_WINDOW_S
    if stream and not has_number:
        later = f"; the first one comes at ~{first_number_s:.0f} s" if first_number else ""
        warn("no_number", f"no concrete number in the first ~{NUMBER_WINDOW_S:.0f} s{later}")

    first_words = sentences[0]["words"] if sentences else 0
    if first_words > FIRST_SENTENCE_MAX:
        warn("long_first_sentence", f"first sentence has {first_words} words (aim for {FIRST_SENTENCE_MAX} or fewer)",
             loc=sentences[0]["loc"])

    beat, beat_words = [], 0
    for s in sentences:
        beat.append(s["text"])
        beat_words += s["words"]
        if beat_words >= FIRST_BEAT_WORDS:
            break
    m = THIS_VIDEO_RE.search(" ".join(beat))
    if m:
        warn("this_video", f"\"{m.group(0)}\" in the first sentence: show the result first, describe the video later",
             loc=sentences[0]["loc"])

    intro = intro_position(text, wpm)
    if intro and intro["start_s"] > INTRO_LATE_S:
        warn("late_intro", f"intro marker {intro['marker']} at ~{intro['start_s']:.0f} s "
             f"(aim for ~{INTRO_TARGET_S:.0f} s)", start_s=intro["start_s"])

    triangle = {
        "proper_noun": any(not w["first"] and _is_proper(w["tok"]) for w in window),
        "concrete_object": bool(CONCRETE_OBJECT_RE.search(" ".join(w["tok"] for w in window))),
        "number": has_number,
    }
    missing = [name.replace("_", " ") for name in ("proper_noun", "concrete_object") if not triangle[name]]
    if window and missing:  # the number leg is the no_number warning above
        warn("triangle", f"opening triangle incomplete in the first ~{NUMBER_WINDOW_S:.0f} s: no "
             + " and no ".join(missing))

    return {"first_sentence_words": first_words, "first_number_s": first_number_s, "triangle": triangle,
            "intro": intro, "warnings": warnings}


# ---------------------------------------------------------------------------------------------------------------
# Visual triggers
# ---------------------------------------------------------------------------------------------------------------

def trigger_norm(word: str) -> str:
    """Lower case, keep letters, digits, $, % and -; drop everything else (apostrophes too: "it's" -> "its")."""
    return re.sub(r"[^\w$%-]", "", word.replace("’", "'").casefold()).replace("_", "")


def _para_number(pid: str) -> int:
    return int(pid.strip().upper().lstrip("P"))


def load_triggers(path: Path) -> list[dict]:
    """Read a trigger file (JSON list or plain lines). Raises ValueError with a readable message."""
    text = path.read_text(encoding="utf-8-sig")
    items = []
    if text.strip()[:1] in "[{":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("triggers")
        if not isinstance(data, list):
            raise ValueError('expected a JSON list of triggers (or {"triggers": [...]})')
        for n, item in enumerate(data, 1):
            if isinstance(item, str):
                item = {"word": item}
            if not isinstance(item, dict) or not str(item.get("word") or "").strip():
                raise ValueError(f'trigger {n}: needs a "word"')
            nth = item.get("nth", 0)
            if isinstance(nth, bool) or not isinstance(nth, int) or nth < 0:
                raise ValueError(f'trigger {n}: "nth" must be a whole number from 0')
            para = item.get("paragraph")
            if para is not None and not re.fullmatch(r"[Pp]?\d+", str(para).strip()):
                raise ValueError(f'trigger {n}: "paragraph" must look like P05')
            items.append({"word": str(item["word"]).strip(), "nth": nth,
                          "paragraph": f"P{_para_number(str(para)):02d}" if para is not None else None, "entry": n})
    else:
        for n, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            para = None
            if len(parts) > 1 and re.fullmatch(r"[Pp]\d+", parts[0]):
                para = f"P{_para_number(parts.pop(0)):02d}"
            nth = 0
            if len(parts) > 1 and parts[-1].isdigit():
                nth = int(parts.pop())
            items.append({"word": " ".join(parts), "nth": nth, "paragraph": para, "entry": n})
    for it in items:
        if not any(trigger_norm(w) for w in it["word"].split()):
            raise ValueError(f"trigger {it['entry']}: \"{it['word']}\" has no letters or digits")
    return items


def check_triggers(text: str, triggers: list[dict]) -> dict:
    """Each trigger's word (or words) must occur more than `nth` times in its paragraph (or the whole script)."""
    whole: list[str] = []
    by_para: dict[int, list[str]] = {}
    for p in paragraphs(text):
        toks = [x for x in (trigger_norm(t) for _, piece in p["pieces"] for t in piece.split()) if x]
        whole += toks
        if p["id"]:
            by_para.setdefault(_para_number(p["id"]), []).extend(toks)
    missing = []
    for t in triggers:
        target = [x for x in (trigger_norm(w) for w in t["word"].split()) if x]
        pool = whole if t["paragraph"] is None else by_para.get(_para_number(t["paragraph"]), [])
        found = sum(1 for i in range(len(pool) - len(target) + 1) if pool[i:i + len(target)] == target)
        if found <= t["nth"]:
            missing.append({"word": t["word"], "nth": t["nth"], "paragraph": t["paragraph"], "found": found})
    return {"total": len(triggers), "missing": missing, "passed": not missing}


def lint(text: str, wpm: float = 155.0, phrase: str | None = None, allow: set[str] | None = None,
         triggers: list[dict] | None = None) -> dict:
    allow = {a.strip().lower() for a in (allow or set()) if a.strip()}
    sents = sentences_of(text)
    words = sum(s["words"] for s in sents)
    longest = max(sents, key=lambda s: s["words"], default=None)
    paras = []
    for p in paragraphs(text):
        joined = " ".join(piece for _, piece in p["pieces"])
        first_line = p["pieces"][0][0]
        paras.append((p["id"] or f"L{first_line}", first_line, joined))
    dashes = []
    for s in sents:
        for m in re.finditer(r"[—–]|\s--\s", s["text"]):
            dashes.append({"loc": s["loc"], "line": s["line"], "char": m.group(0).strip()})
    exclamations = [{"loc": s["loc"], "line": s["line"]} for s in sents for _ in range(s["text"].count("!"))]
    vocab = find_vocab(sents, allow)
    vague = find_vague(sents, allow)
    result = {
        "words": words,
        "wpm": wpm,
        "runtime_s": round(words / wpm * 60.0, 1) if wpm else None,
        "sentences": len(sents),
        "avg_sentence_words": round(words / len(sents), 1) if sents else 0.0,
        "longest_sentence": ({"words": longest["words"], "loc": longest["loc"], "line": longest["line"],
                              "start": _first_words(longest["text"])} if longest else None),
        "long_sentences": [{"loc": s["loc"], "line": s["line"], "words": s["words"], "start": _first_words(s["text"])}
                           for s in sents if s["words"] > LONG_SENTENCE],
        "repeated_openings": repeated_openings(sents),
        "ai_vocabulary": vocab,
        "vague_attributions": vague,
        "setup_phrases": find_phrases(sents, SETUP_PHRASES),
        "contrasts": find_contrasts(paras),
        "dashes": dashes,
        "what_if_openers": [{"loc": s["loc"], "line": s["line"]} for s in sents
                            if re.match(r"\W*what if\b", s["text"], re.I)],
        "fragment_runs": fragment_runs(sents),
        "exclamations": exclamations,
        "opening_5s": opening_words(sents, wpm),
        "final_sentence": sents[-1]["text"] if sents else "",
        "phrase": phrase_timing(sents, phrase, wpm) if phrase else None,
        "opening": opening_checks(text, sents, wpm),
        "triggers": check_triggers(text, triggers) if triggers is not None else None,
    }
    reasons = []
    if result["long_sentences"]:
        n = len(result["long_sentences"])
        reasons.append(f"{n} sentence{'s' if n != 1 else ''} over {LONG_SENTENCE} words")
    blocked = [h for h in vocab if not h["allowed"]]
    if blocked:
        reasons.append(f"{len(blocked)} AI-vocabulary hit{'s' if len(blocked) != 1 else ''}")
    unsourced = [h for h in vague if not h["allowed"] and not h["named_source"]]
    if unsourced:
        reasons.append(f"{len(unsourced)} vague attribution{'s' if len(unsourced) != 1 else ''} without a source")
    ph = result["phrase"]
    if ph is not None and not ph["within_limit"]:
        reasons.append("phrase not found" if not ph["found"] else
                       f"phrase starts at ~{ph['start_s']:.0f} s (limit {PHRASE_LIMIT_S:.0f} s)")
    tr = result["triggers"]
    if tr is not None and not tr["passed"]:
        reasons.append(f"{len(tr['missing'])} of {tr['total']} visual triggers missing")
    result["gate_reasons"] = reasons
    return result


# ---------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------

def _mmss(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _locs(items: list[dict], key: str | None = None, limit: int = 10) -> str:
    parts = []
    for h in items[:limit]:
        parts.append(f"{h['loc']} \"{h[key]}\"" if key else h["loc"])
    more = f", +{len(items) - limit} more" if len(items) > limit else ""
    return ", ".join(parts) + more


def print_human(path: str, r: dict, gate: bool) -> None:
    print(path)
    print(f"  Words {r['words']:,}, runtime ~{_mmss(r['runtime_s'] or 0)} at {r['wpm']:g} wpm")
    if r["longest_sentence"]:
        ls = r["longest_sentence"]
        print(f"  Sentences {r['sentences']}, average {r['avg_sentence_words']} words, longest {ls['words']} "
              f"({ls['loc']})")
    else:
        print("  No spoken text found.")
    print(f"  Over {LONG_SENTENCE} words: {len(r['long_sentences'])}")
    for s in r["long_sentences"]:
        print(f"    {s['loc']} ({s['words']}): {s['start']}")
    print(f"  Same opening in a row: {len(r['repeated_openings'])}"
          + (" -> " + ", ".join(f"{x['loc']} \"{x['opener']}\" x{x['count']}" for x in r["repeated_openings"][:10])
             if r["repeated_openings"] else ""))
    vocab = r["ai_vocabulary"]
    shown = [dict(h, match=h["match"] + (" (allowed)" if h["allowed"] else "")) for h in vocab]
    print(f"  AI vocabulary: {len(vocab)}" + (" -> " + _locs(shown, "match") if vocab else ""))
    vague = [dict(h, match=h["match"] + (" (allowed)" if h["allowed"] else ""))
             for h in r["vague_attributions"] if not h["named_source"]]
    named = len(r["vague_attributions"]) - len(vague)
    print(f"  Vague attributions without a source: {len(vague)}" + (" -> " + _locs(vague, "match") if vague else "")
          + (f" ({named} with a named source not counted)" if named else ""))
    print(f"  Setup phrases: {len(r['setup_phrases'])}"
          + (" -> " + _locs(r["setup_phrases"], "match") if r["setup_phrases"] else ""))
    print(f"  Not-X-but-Y contrasts: {len(r['contrasts'])}"
          + (" -> " + _locs(r["contrasts"], "match", 5) if r["contrasts"] else ""))
    print(f"  Em/en dashes: {len(r['dashes'])}" + (" -> " + _locs(r["dashes"]) if r["dashes"] else ""))
    print(f"  \"What if\" openers: {len(r['what_if_openers'])}"
          + (" -> " + _locs(r["what_if_openers"]) if r["what_if_openers"] else ""))
    print(f"  Fragment runs ({FRAGMENT_RUN}+ sentences under {FRAGMENT_WORDS} words): {len(r['fragment_runs'])}"
          + (" -> " + ", ".join(f"{x['loc']} ({x['count']})" for x in r["fragment_runs"][:10])
             if r["fragment_runs"] else ""))
    print(f"  Exclamation marks: {len(r['exclamations'])}"
          + (" -> " + _locs(r["exclamations"]) if r["exclamations"] else ""))
    ph = r["phrase"]
    if ph is not None:
        if not ph["found"]:
            print(f"  Phrase \"{ph['phrase']}\": not found in the spoken text")
        else:
            verdict = "within" if ph["within_limit"] else "AFTER"
            print(f"  Phrase \"{ph['phrase']}\": starts at word {ph['word_index']}, ~{ph['start_s']:.0f} s "
                  f"({verdict} {PHRASE_LIMIT_S:.0f} s)")
    op = r["opening"]
    first_number = (f"first number at ~{op['first_number_s']:.0f} s" if op["first_number_s"] is not None
                    else "no number spoken")
    print(f"Opening checks ({first_number}; warnings, not a gate): {len(op['warnings'])}")
    for w in op["warnings"]:
        print(f"  WARN {w['message']}")
    print(f"First {OPENING_S:.0f} seconds: \"{r['opening_5s']}\"")
    print(f"Final sentence: \"{r['final_sentence']}\"")
    tr = r["triggers"]
    if tr is not None:
        for t in tr["missing"]:
            where = t["paragraph"] or "script"
            print(f"  MISSING trigger {where} \"{t['word']}\" #{t['nth']} (found {t['found']} time"
                  f"{'s' if t['found'] != 1 else ''})")
        print(f"TRIGGERS: {'PASS' if tr['passed'] else 'FAIL'} - {tr['total'] - len(tr['missing'])} of "
              f"{tr['total']} found")
    print(GUIDE_LINE)
    if gate:
        if r["gate_reasons"]:
            print("GATE: FAIL - " + "; ".join(r["gate_reasons"]))
        else:
            print("GATE: PASS")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("script", help="path to the script (markdown)")
    ap.add_argument("--wpm", type=float, default=155.0, help="speaking rate in words per minute (default 155)")
    ap.add_argument("--phrase", help="search phrase that should be spoken within the first 30 seconds")
    ap.add_argument("--gate", action="store_true",
                    help="exit 1 on a sentence over 30 words, an AI-vocabulary hit, an unsourced vague attribution "
                         "or a late phrase")
    ap.add_argument("--allow", default="",
                    help="AI-vocabulary or vague-attribution terms to accept, comma-separated")
    ap.add_argument("--triggers", help="words on-screen animations are keyed to (JSON or plain lines); "
                                       "exit 1 if one is missing")
    ap.add_argument("--json", action="store_true", help="print a machine-readable result")
    ap.add_argument("--out", help="also write the JSON result to this file")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    if args.wpm <= 0:
        print("error: --wpm must be positive", file=sys.stderr)
        return 2
    path = Path(args.script)
    try:
        text = path.read_text(encoding="utf-8-sig")  # -sig: Windows editors may add a BOM
    except FileNotFoundError:
        print(f"error: script not found: {path}", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    triggers = None
    if args.triggers:
        try:
            triggers = load_triggers(Path(args.triggers))
        except FileNotFoundError:
            print(f"error: trigger file not found: {args.triggers}", file=sys.stderr)
            return 2
        except (OSError, UnicodeDecodeError, ValueError) as exc:  # json.JSONDecodeError is a ValueError
            print(f"error: cannot read trigger file {args.triggers}: {exc}", file=sys.stderr)
            return 2
    r = lint(text, args.wpm, args.phrase, set(args.allow.split(",")), triggers)
    r["file"] = str(path)
    if r["triggers"] is not None:
        r["triggers"]["file"] = str(args.triggers)
    r["gate"] = {"enabled": args.gate, "passed": not r["gate_reasons"], "reasons": r["gate_reasons"]}
    r["note"] = GUIDE_LINE
    if args.out:
        try:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"error: cannot write {args.out}: {exc}", file=sys.stderr)
            return 2
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print_human(str(path), r, args.gate)
    triggers_failed = r["triggers"] is not None and not r["triggers"]["passed"]
    return 1 if (args.gate and r["gate_reasons"]) or triggers_failed else 0


if __name__ == "__main__":
    sys.exit(main())
