#!/usr/bin/env python3
"""pick_takes.py - pick the most faithful take of each regenerated paragraph.

What it does
    For every paragraph that has candidate takes, reads the transcript saved next to each take (or transcribes
    the take with faster-whisper), aligns it word by word with that paragraph of the script and ranks the takes
    by fidelity. A take only counts when every number and every capitalised name of the paragraph is heard
    exactly, its pace is plausible, and its fidelity reaches --min-fidelity. Prints a table, writes
    qa/take-pick.json, and with --apply copies the winner of each paragraph into the folder the narration is
    assembled from.

Why
    Generated voices fail per paragraph, and not the same way every time: a repeated phrase, an invented
    sentence, a garbled name, a wrong number. Duration does not show it; a take can be the right length and
    still say the wrong thing. Picking by ear across a dozen takes is slow; a word-level score is exact.
    Regenerate only the failing paragraphs, with new seeds (a fixed seed repeats the same mistake); three
    rounds is normal.

How to run
    python pick_takes.py script.md --list                          numbered paragraphs (the folder numbers)
    python pick_takes.py script.md voice/candidates
    python pick_takes.py script.md voice/candidates --apply voice/paragraphs

    Layout: voice/candidates/<paragraph number>/<any name>.<wav|mp3|m4a|flac|ogg|opus|aac>
    The folder name is the 1-based paragraph number (3, 03, 003 and p003 all mean paragraph 3); paragraphs are
    the lines of the cleaned script, exactly as voice_check.py numbers them. A transcript next to a take
    (<name>.json, .srt, .vtt or .txt) is used as is; otherwise the take is transcribed with faster-whisper
    (`pip install faster-whisper`) and <name>.json is written next to it, with the audio's sha256 so an edited
    take is transcribed again.

    --apply DIR copies each usable winner to DIR/p<NNN>.<ext> (p003.wav). An existing different file for that
    paragraph is moved to DIR/replaced/ first, never deleted. Paragraphs without a usable take are not copied.

    Report: <script folder>/qa/take-pick.json unless --out is given. Paths in it are relative.

Rules for a usable take
    - every number of the paragraph is heard exactly, with its currency, scale word and percent sign
      ("$ 12.5 billion"), and every capitalised name is heard (a name at the start of a sentence counts when it
      is capitalised mid-sentence somewhere in the script); --waive TEXT accepts one you listened to
    - pace inside --wpm-fail-band (when the transcript has word timings)
    - fidelity (difflib ratio over voice_check's normalized words, autojunk off) at or above --min-fidelity
    Ties within 0.005 fidelity go to the take whose pace is closest to the middle of --wpm-band.

Exit codes
    0  every paragraph looked at has a usable take     1  at least one has none     2  usage or environment error

Standard library only; faster-whisper is optional and imported only when a take has no transcript.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import importlib.util
import json
import re
import shutil
import sys
import unicodedata
from pathlib import Path

TOOL = "pick_takes.py"
VERSION = "0.1.0"

MIN_FIDELITY = 0.95        # under this a take is worth regenerating; clean takes of a paragraph score 0.95-1.0
TIE_MARGIN = 0.005         # takes this close in fidelity are a tie (about half a word in a 100-word paragraph)
MAX_DIFFERENCES = 3        # largest differences listed per take
TRANSCRIPT_SUFFIXES = (".json", ".srt", ".vtt", ".txt")
REPLACED_DIR = "replaced"
_FOLDER_RE = re.compile(r"[pP]?0*(\d+)")
_SENTENCE_END_RE = re.compile("[.!?:;…][\"'”’)\\]]*$")
_EDGE_PUNCT = "\"'()[]{}<>.,;:!?*_“”‘’…–—-"
_PRONOUN_I = {"i", "im", "ill", "ive", "id"}


def _load_voice_check():
    """voice_check.py lives next to this file; the skill folder name has a dash, so import it by path."""
    path = Path(__file__).resolve().parent / "voice_check.py"
    spec = importlib.util.spec_from_file_location("tubecrew_voice_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vc = _load_voice_check()
UsageError = vc.UsageError
PASS, FAIL = vc.PASS, vc.FAIL


# ----------------------------------------------------------------------------------------------- what must be heard

def _is_currency(token: str) -> bool:
    return len(token) == 1 and unicodedata.category(token) == "Sc"


def required_numbers(tokens: list[str]) -> list[list[str]]:
    """Normalized paragraph tokens -> every number with its currency symbol, scale word and percent sign."""
    out: list[list[str]] = []
    for i, tok in enumerate(tokens):
        if not any(c.isdigit() for c in tok):
            continue
        seq = [tok]
        if i > 0 and _is_currency(tokens[i - 1]):
            seq.insert(0, tokens[i - 1])
        j = i + 1
        if j < len(tokens) and tokens[j] in vc._SCALE_WORDS:
            seq.append(tokens[j])
            j += 1
        if j < len(tokens) and tokens[j] in ("%", "‰"):
            seq.append(tokens[j])
        if seq not in out:
            out.append(seq)
    return out


def capitalised_words(text: str) -> list[tuple[str, bool]]:
    """Capitalised words of a text, each with whether it starts a sentence."""
    out = []
    sentence_start = True
    for raw in text.split():
        word = raw.strip(_EDGE_PUNCT)
        if word and word[0].isupper() and word.casefold().replace("'", "").replace("’", "") not in _PRONOUN_I:
            out.append((word, sentence_start))
        sentence_start = bool(_SENTENCE_END_RE.search(raw))
    return out


def known_names(paragraphs: list[str]) -> set[str]:
    """Words capitalised in the middle of a sentence anywhere in the script: those are names."""
    return {word for text in paragraphs for word, start in capitalised_words(text) if not start}


def required_items(text: str, language: str, names: set[str]) -> list[dict]:
    """The numbers and names a take of this paragraph must say: [{"text", "kind", "tokens"}]."""
    items: list[dict] = []
    seen: set[tuple] = set()
    for seq in required_numbers(vc.normalize(text, language)):
        if tuple(seq) not in seen:
            seen.add(tuple(seq))
            items.append({"text": " ".join(seq), "kind": "number", "tokens": seq})
    for word, start in capitalised_words(text):
        if start and word not in names:
            continue
        toks = [t for t in vc.normalize(word, language) if not any(c.isdigit() for c in t)]
        if toks and tuple(toks) not in seen:
            seen.add(tuple(toks))
            items.append({"text": word, "kind": "name", "tokens": toks})
    return items


def _contains(heard: list[str], seq: list[str]) -> bool:
    n = len(seq)
    return any(heard[i:i + n] == seq for i in range(len(heard) - n + 1))


def heard_item(heard: list[str], item: dict) -> bool:
    """A number must appear token for token. A name may also be split differently by the recognizer
    ("A.I." for "AI", "Open AI" for "OpenAI"), so its letters are compared without spaces too."""
    if _contains(heard, item["tokens"]):
        return True
    if item["kind"] != "name":
        return False
    compact = "".join(item["tokens"])
    for size in range(1, len(item["tokens"]) + 3):
        for i in range(len(heard) - size + 1):
            if "".join(heard[i:i + size]) == compact:
                return True
    return False


def _waived(item: dict, waivers: list[list[str]]) -> bool:
    compact = "".join(item["tokens"])
    return any(w == item["tokens"] or "".join(w) == compact for w in waivers)


# ----------------------------------------------------------------------------------------------- takes

def paragraph_folders(candidates: Path) -> tuple[dict[int, Path], list[str]]:
    """{paragraph number: folder} and the names of entries that are not paragraph folders."""
    found: dict[int, Path] = {}
    ignored: list[str] = []
    for entry in sorted(candidates.iterdir()):
        m = _FOLDER_RE.fullmatch(entry.name)
        if entry.is_dir() and m and int(m.group(1)) > 0:
            number = int(m.group(1))
            if number in found:
                raise UsageError(f"two folders for paragraph {number}: {found[number].name} and {entry.name}")
            found[number] = entry
        else:
            ignored.append(entry.name + ("/" if entry.is_dir() else ""))
    return found, ignored


def takes_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in vc.AUDIO_EXTENSIONS)


def transcript_of(take: Path, whisper_factory, language: str) -> tuple[list[dict], Path]:
    """Transcript pieces of a take and the transcript file. Uses a saved transcript when one is next to the
    take (unless it records a different audio hash); otherwise transcribes and saves <name>.json."""
    digest = None
    for suffix in TRANSCRIPT_SUFFIXES:
        path = take.with_suffix(suffix)
        if not path.is_file():
            continue
        if suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError) as exc:
                raise UsageError(f"transcript {path.name} is not valid JSON: {exc}") from exc
            recorded = data.get("audio_sha256") if isinstance(data, dict) else None
            if recorded:
                digest = digest or vc.sha256_file(take)
                if recorded != digest:
                    break  # the take changed after it was transcribed
            return vc.parse_json_transcript(data), path
        return vc.load_transcript(path), path
    try:
        data = whisper_factory().transcribe(take, language)
    except UsageError as exc:
        raise UsageError(f"{take.parent.name}/{take.name} has no transcript next to it, and {exc}") from exc
    data = {"audio_sha256": digest or vc.sha256_file(take), **data}
    path = take.with_suffix(".json")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return vc.parse_json_transcript(data), path


def differences(script: list[str], heard: list[str], limit: int = MAX_DIFFERENCES) -> list[str]:
    """The largest differences, as 'script "x" heard "y"'."""
    found = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, script, heard, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        expected = " ".join(script[i1:i2]) or "(nothing)"
        said = " ".join(heard[j1:j2]) or "(nothing)"
        found.append((i2 - i1 + j2 - j1, f'script "{vc.excerpt(expected, 50)}" heard "{vc.excerpt(said, 50)}"'))
    found.sort(key=lambda x: -x[0])
    return [text for _, text in found[:limit]]


def score_take(tokens: list[str], words: int, items: list[dict], pieces: list[dict], language: str,
               min_fidelity: float, warn_band, fail_band, waivers: list[list[str]]) -> dict:
    heard, times = vc.heard_tokens(pieces, language)
    fidelity = difflib.SequenceMatcher(None, tokens, heard, autojunk=False).ratio() if tokens else 0.0
    missing = [i["text"] for i in items if not heard_item(heard, i) and not _waived(i, waivers)]
    span = vc.speech_span(times) if vc.timing_kind(pieces) == "word" else None
    pace = vc.pace_verdict(words, span, warn_band, fail_band)
    reasons = []
    if missing:
        reasons.append("missing " + ", ".join(missing))
    if pace["status"] == FAIL:
        reasons.append(f"pace {pace['wpm']} wpm ({pace['pace']})")
    if fidelity < min_fidelity:
        reasons.append(f"fidelity below {min_fidelity:g}")
    return {"fidelity": round(fidelity, 4), "missing": missing, "wpm": pace["wpm"], "pace": pace["pace"],
            "usable": not reasons, "rejected": "; ".join(reasons) or None,
            "differences": differences(tokens, heard)}


def rank(takes: list[dict], warn_band) -> list[dict]:
    """Usable takes first, by fidelity; a tie goes to the pace closest to the middle of the warn band."""
    ordered = sorted(takes, key=lambda t: (not t["usable"], -t["fidelity"], t["take"]))
    usable = [t for t in ordered if t["usable"]]
    if len(usable) > 1 and warn_band:
        target = (warn_band[0] + warn_band[1]) / 2.0
        tied = [t for t in usable if usable[0]["fidelity"] - t["fidelity"] <= TIE_MARGIN]
        winner = min(tied, key=lambda t: (t["wpm"] is None, abs((t["wpm"] or 0) - target)))
        ordered.remove(winner)
        ordered.insert(0, winner)
    return ordered


# ----------------------------------------------------------------------------------------------- apply

def apply_winner(take: Path, number: int, dest: Path) -> dict:
    """Copy a winning take to dest/p<NNN><ext>, moving any other file for that paragraph to dest/replaced/."""
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"p{number:03d}{take.suffix.lower()}"
    olds = [p for p in sorted(dest.glob(f"p{number:03d}.*"))
            if p.is_file() and p.suffix.lower() in vc.AUDIO_EXTENSIONS]
    unchanged = target in olds and vc.sha256_file(target) == vc.sha256_file(take)
    replaced = []
    for old in olds:
        if unchanged and old == target:
            continue
        archive = dest / REPLACED_DIR
        archive.mkdir(exist_ok=True)
        k = 1
        while (archive / f"{old.stem}-{k}{old.suffix}").exists():
            k += 1
        moved = archive / f"{old.stem}-{k}{old.suffix}"
        old.replace(moved)
        replaced.append(moved)
    if not unchanged:
        shutil.copy2(take, target)
    return {"paragraph": number, "to": target, "replaced": replaced, "unchanged": unchanged}


# ----------------------------------------------------------------------------------------------- output

def human_lines(report: dict, report_rel: str) -> list[str]:
    rows = report["paragraphs"]
    lines = [f"pick_takes: {report['script']}, {len(rows)} paragraph(s), "
             f"{sum(len(r['takes']) for r in rows)} take(s), min fidelity {report['min_fidelity']:g}"]
    for row in rows:
        lines.append(f"p{row['paragraph']}  ({row['words']} words) \"{row['excerpt']}\"")
        if row["must_hear"]:
            lines.append("    must hear: " + ", ".join(row["must_hear"]))
        for k, take in enumerate(row["takes"]):
            mark = "BEST" if k == 0 and take["usable"] else ("    " if take["usable"] else "  no")
            speed = f"{take['wpm']} wpm" if take["wpm"] is not None else "-"
            lines.append(f"  {mark}  {Path(take['take']).name:<24} {take['fidelity']:.3f}  {speed:<8}  "
                         f"{take['rejected'] or ''}".rstrip())
            if not take["usable"] or k == 0:
                for diff in take["differences"][:2]:
                    lines.append(f"          {diff}")
        if row["status"] == FAIL:
            if row["takes"]:
                lines.append(f"  -> no usable take: regenerate paragraph {row['paragraph']} with new seeds")
            else:
                lines.append(f"  -> no takes in {row['folder']}")
    for item in report.get("applied") or []:
        note = "already in place" if item["unchanged"] else "copied"
        lines.append(f"Applied p{item['paragraph']}: {note} -> {item['to']}"
                     + (f" (old file moved to {', '.join(item['replaced'])})" if item["replaced"] else ""))
    failing = report["failing"]
    lines.append(f"VERDICT: {report['verdict']}"
                 + (f" (no usable take for paragraph {', '.join(str(n) for n in failing)})" if failing else ""))
    lines.append(f"Report: {report_rel}")
    return lines


def _number_list(text: str) -> list[int]:
    try:
        numbers = [int(part) for part in text.replace(" ", "").split(",") if part]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected paragraph numbers like 3,7 - got {text!r}") from exc
    if not numbers or min(numbers) < 1:
        raise argparse.ArgumentTypeError(f"expected paragraph numbers like 3,7 - got {text!r}")
    return numbers


def main(argv: list[str] | None = None) -> int:
    vc._utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Rank candidate takes of each paragraph by fidelity to the script and pick the "
                               "best one. Exit 0 every paragraph has a usable take, 1 not, 2 usage error.")
    parser.add_argument("script", help="the spoken script (Markdown or plain text)")
    parser.add_argument("candidates", nargs="?", help="folder with one sub-folder of takes per paragraph number")
    parser.add_argument("--list", action="store_true", help="print the numbered paragraphs and exit")
    parser.add_argument("--paragraphs", type=_number_list, metavar="N,N", help="only these paragraph numbers")
    parser.add_argument("--min-fidelity", type=float, default=MIN_FIDELITY,
                        help=f"lowest fidelity a usable take may have (default {MIN_FIDELITY:g})")
    parser.add_argument("--waive", action="append", default=[], metavar="TEXT",
                        help="a number or name you listened to and accept even though the transcript misses it "
                             "(repeatable; recorded in the report)")
    parser.add_argument("--wpm-band", type=vc._band_arg, default=vc.WPM_WARN_BAND, metavar="LO,HI",
                        help="its middle breaks fidelity ties (default %g,%g)" % vc.WPM_WARN_BAND)
    parser.add_argument("--wpm-fail-band", type=vc._band_arg, default=vc.WPM_FAIL_BAND, metavar="LO,HI",
                        help="a take paced outside this band is not usable (default %g,%g; 'off' disables)"
                             % vc.WPM_FAIL_BAND)
    parser.add_argument("--apply", metavar="DIR", help="copy each usable winner to DIR/p<NNN>.<ext>")
    parser.add_argument("--language", default="en", help="language code of the narration (default en)")
    parser.add_argument("--model", default="small", help="faster-whisper model for takes without a transcript")
    parser.add_argument("--beam-size", type=int, default=vc.WHISPER_BEAM_SIZE,
                        help=f"faster-whisper beam size (default {vc.WHISPER_BEAM_SIZE})")
    parser.add_argument("--out", help="report path (default <script folder>/qa/take-pick.json)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)

    script_path = Path(args.script)
    base = script_path.resolve().parent
    out_path = Path(args.out) if args.out else base / "qa" / "take-pick.json"
    try:
        if not script_path.is_file():
            raise UsageError(f"script not found: {args.script}")
        try:
            spoken = vc.clean_script(script_path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError as exc:
            raise UsageError(f"cannot read script: {exc}") from exc
        placeholders = vc.find_placeholders(spoken)
        if placeholders:
            raise UsageError(f"the script still has template placeholders ({', '.join(sorted(set(placeholders))[:5])});"
                             " compile the template to the final spoken text first")
        paragraphs = vc.paragraph_texts(spoken)
        if not paragraphs:
            raise UsageError("the script has no spoken text after removing headings, notes and directions")
        if args.list:
            listing = [{"paragraph": k + 1, "words": vc.word_count(t), "text": t} for k, t in enumerate(paragraphs)]
            if args.json:
                print(json.dumps({"script": vc.rel_path(script_path, base), "paragraphs": listing}, indent=2,
                                 ensure_ascii=False))
            else:
                for item in listing:
                    print(f"p{item['paragraph']:03d}  ({item['words']} words)  {item['text']}")
            return 0
        if not args.candidates:
            raise UsageError("give the candidates folder (or --list to see the paragraph numbers)")
        candidates = Path(args.candidates)
        if not candidates.is_dir():
            raise UsageError(f"candidates folder not found: {args.candidates}")
        folders, ignored = paragraph_folders(candidates)
        if args.paragraphs:
            missing_folders = [n for n in args.paragraphs if n not in folders]
            if missing_folders:
                raise UsageError("no candidate folder for paragraph " + ", ".join(map(str, missing_folders)))
            folders = {n: folders[n] for n in args.paragraphs}
        if not folders:
            raise UsageError(f"no paragraph folders in {args.candidates} (expected <number>/<take>.wav, e.g. 3/a.wav)")
        too_high = [n for n in folders if n > len(paragraphs)]
        if too_high:
            raise UsageError(f"the script has {len(paragraphs)} paragraphs; no paragraph "
                             + ", ".join(map(str, sorted(too_high))))
        waivers = [vc.normalize(w, args.language) for w in args.waive]
        names = known_names(paragraphs)
        whisper = []

        def whisper_factory():
            if not whisper:
                whisper.append(vc.Whisper(args.model, args.beam_size))
            return whisper[0]

        rows = []
        for number in sorted(folders):
            text = paragraphs[number - 1]
            tokens = vc.normalize(text, args.language)
            words = vc.word_count(text)
            items = required_items(text, args.language, names)
            takes = []
            for take in takes_in(folders[number]):
                pieces, transcript = transcript_of(take, whisper_factory, args.language)
                scored = score_take(tokens, words, items, pieces, args.language, args.min_fidelity,
                                    args.wpm_band, args.wpm_fail_band, waivers)
                takes.append({"take": vc.rel_path(take, base), "transcript": vc.rel_path(transcript, base),
                              "path": take, **scored})
            ordered = rank(takes, args.wpm_band)
            usable = bool(ordered) and ordered[0]["usable"]
            rows.append({"paragraph": number, "excerpt": vc.excerpt(text), "words": words,
                         "folder": vc.rel_path(folders[number], base),
                         "must_hear": [i["text"] for i in items],
                         "best": ordered[0]["take"] if usable else None,
                         "status": PASS if usable else FAIL, "takes": ordered})
    except UsageError as exc:
        print(f"pick_takes: error: {exc}", file=sys.stderr)
        return 2

    applied = []
    if args.apply:
        dest = Path(args.apply)
        try:
            for row in rows:
                if row["status"] == PASS:
                    done = apply_winner(row["takes"][0]["path"], row["paragraph"], dest)
                    applied.append({"paragraph": done["paragraph"], "to": vc.rel_path(done["to"], base),
                                    "replaced": [vc.rel_path(p, base) for p in done["replaced"]],
                                    "unchanged": done["unchanged"]})
        except OSError as exc:
            print(f"pick_takes: error: cannot apply: {exc}", file=sys.stderr)
            return 2
    for row in rows:
        for take in row["takes"]:
            take.pop("path", None)
    failing = [r["paragraph"] for r in rows if r["status"] == FAIL]
    report = {
        "tool": TOOL, "version": VERSION,
        "script": vc.rel_path(script_path, base), "script_sha256": vc.sha256_file(script_path),
        "candidates": vc.rel_path(candidates, base), "language": args.language,
        "measured_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "min_fidelity": args.min_fidelity,
        "wpm_fail_band": list(args.wpm_fail_band) if args.wpm_fail_band else None,
        "waived": args.waive, "verdict": FAIL if failing else PASS, "failing": failing,
        "ignored": ignored, "paragraphs": rows, "applied": applied,
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"pick_takes: error: cannot write report: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print("\n".join(human_lines(report, vc._display_path(out_path, base))))
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
