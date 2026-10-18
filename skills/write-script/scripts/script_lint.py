"""Mechanical counts for a spoken script: length, runtime, long sentences, AI vocabulary and other patterns.

Why: a listener cannot re-read a sentence, and a few words mark a narration as machine-written within seconds.
The countable part of that is cheap to measure, so a reviewer can spend their attention on meaning and taste.

Only spoken text is counted. Skipped: lines starting with `#` (headings) or `>` (notes), tables, HTML comments,
fenced code, front matter, `[[cue]]` anchors, bracketed directions like `[ON SCREEN: ...]` and markdown emphasis
markers. Paragraph ids at the start of a line (`P01 ...` or `**P01** ...`) are kept as locations; without them a
location is a line number (`L12`). A `{key}` placeholder counts as 3 words (a spoken number is rarely one word).

Reported: words, runtime at --wpm, sentences (average, longest), every sentence over 30 words, runs of
sentences that open with the same word, AI-vocabulary hits, setup phrases ("here's the thing"), "not X, but Y" /
"isn't just ... it's" contrasts, em/en dashes, "What if" openers, fragment runs (3+ sentences under 5 words),
exclamation marks, the words spoken in the first 5 seconds and the final sentence. With --phrase: the second at
which the phrase starts and whether that is within 30 s.

Run:
  python script_lint.py script.md
  python script_lint.py script.md --phrase "how does a credit card make money" --gate --out qa/script-lint.json
  python script_lint.py script.md --gate --allow leverage,landscape --json

--gate fails (exit 1) on any sentence over 30 words, any AI-vocabulary hit not in --allow, or a phrase that starts
after 30 s (or is missing). Pattern counts are guidance, never a gate. The English word lists find nothing in
other languages; the length, runtime, opening and phrase metrics still work there.

Exit codes: 0 pass (or no --gate), 1 gate failed, 2 usage error.
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
CONTRAST_RES = [
    re.compile(r"\bnot\s+(?:just|only|merely|simply)\b[^.?!;]{1,80}?\bbut\b", re.I),
    re.compile(r"\bnot\b[^.?!;]{1,60}?,\s*but\b", re.I),
    re.compile(r"\b(?:isn't|is not|wasn't|was not|aren't|are not|weren't|were not)\s+(?:just|only|merely|simply|"
               r"about)\b[^?!]{0,120}?\b(?:it's|it is|it was|they're|they are|that's|this is)\b", re.I),
    re.compile(r"\b(?:it's|it is|that's|this is|it was)\s+not\b[^?!]{0,80}?[,.;:—–-]\s*"
               r"(?:it's|it is|that's|this is|it was)\b", re.I),
]
ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "etc", "e.g", "i.e", "inc", "corp", "ltd",
                 "co", "no", "fig", "approx", "dept", "est", "u.s", "u.k", "a.m", "p.m"}
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
    target = [_norm_token(t) for t in tokens(phrase)]
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


def lint(text: str, wpm: float = 155.0, phrase: str | None = None, allow: set[str] | None = None) -> dict:
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
    }
    reasons = []
    if result["long_sentences"]:
        n = len(result["long_sentences"])
        reasons.append(f"{n} sentence{'s' if n != 1 else ''} over {LONG_SENTENCE} words")
    blocked = [h for h in vocab if not h["allowed"]]
    if blocked:
        reasons.append(f"{len(blocked)} AI-vocabulary hit{'s' if len(blocked) != 1 else ''}")
    ph = result["phrase"]
    if ph is not None and not ph["within_limit"]:
        reasons.append("phrase not found" if not ph["found"] else
                       f"phrase starts at ~{ph['start_s']:.0f} s (limit {PHRASE_LIMIT_S:.0f} s)")
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
    print(f"First {OPENING_S:.0f} seconds: \"{r['opening_5s']}\"")
    print(f"Final sentence: \"{r['final_sentence']}\"")
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
                    help="exit 1 on a sentence over 30 words, an AI-vocabulary hit or a late phrase")
    ap.add_argument("--allow", default="", help="AI-vocabulary terms to accept, comma-separated")
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
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"error: script not found: {path}", file=sys.stderr)
        return 2
    except (OSError, UnicodeDecodeError) as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    r = lint(text, args.wpm, args.phrase, set(args.allow.split(",")))
    r["file"] = str(path)
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
    return 1 if args.gate and r["gate_reasons"] else 0


if __name__ == "__main__":
    sys.exit(main())
