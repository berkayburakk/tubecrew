"""Tests for skills/write-script/scripts/script_lint.py (synthetic scripts only)."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "write-script" / "scripts" / "script_lint.py"


def _load():
    spec = importlib.util.spec_from_file_location("script_lint", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sl = _load()

SAMPLE = """---
title: Example
---
# How Acme makes money

> Note for the editor: keep this under eight minutes.

<!-- draft comment that is
not spoken -->

**P01** Acme sells widgets to *every* hospital in town. [[hook-1]] [ON SCREEN: the Acme logo]
So how does Acme make money?

P02 The answer starts with one contract.

| P | change | pattern |
|---|---|---|
| P01 | cut a word | 3 |
"""


def _write(tmp_path, text, name="script.md"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_markdown_cues_and_directions_are_not_spoken():
    sents = sl.sentences_of(SAMPLE)
    texts = [s["text"] for s in sents]
    assert texts == ["Acme sells widgets to every hospital in town.", "So how does Acme make money?",
                     "The answer starts with one contract."]
    assert [s["loc"] for s in sents] == ["P01", "P01", "P02"]
    assert sents[1]["line"] == 12  # the second sentence starts on the wrapped line
    r = sl.lint(SAMPLE)
    assert r["words"] == 20 and r["sentences"] == 3


def test_location_falls_back_to_line_numbers():
    sents = sl.sentences_of("First line here.\n\nSecond paragraph here.")
    assert [s["loc"] for s in sents] == ["L1", "L3"]


def test_sentence_split_keeps_abbreviations_and_decimals():
    sents = [s for _, s in sl.split_sentences("Mr. Smith paid $12.5 million in the U.S. market. Then he left!")]
    assert sents == ["Mr. Smith paid $12.5 million in the U.S. market.", "Then he left!"]


def test_long_sentences_reported_and_gated(tmp_path):
    long = "Word " + " ".join(["word"] * 30) + "."
    text = f"P01 Short start here. {long}\n\nP02 " + " ".join(["fine"] * 30) + "."
    r = sl.lint(text)
    assert [(s["loc"], s["words"]) for s in r["long_sentences"]] == [("P01", 31)]  # exactly 30 is fine
    assert r["longest_sentence"]["words"] == 31
    path = _write(tmp_path, text)
    assert sl.main([str(path)]) == 0           # without --gate it only reports
    assert sl.main([str(path), "--gate"]) == 1


def test_repeated_openings():
    text = "P01 But it grew. But costs rose. But nobody noticed. Then it broke. The plan failed. The plan changed."
    runs = sl.lint(text)["repeated_openings"]
    assert [(r["opener"], r["count"]) for r in runs] == [("but", 3), ("the plan", 2)]


def test_ai_vocabulary_and_allow(tmp_path):
    text = ("P01 We delve into the landscape. It's worth noting this robust plan is a game-changer.\n\n"
            "P02 Dr. Foster met them. In today's world, that is crucial.")
    r = sl.lint(text)
    terms = [h["term"] for h in r["ai_vocabulary"]]
    assert terms == ["delve", "landscape", "it's worth noting", "robust", "game-changer", "in today's world",
                     "crucial"]  # "Foster" the surname is not a hit
    allowed = sl.lint(text, allow={"landscape", "crucial"})
    assert sum(not h["allowed"] for h in allowed["ai_vocabulary"]) == 5
    path = _write(tmp_path, "P01 The market landscape shifted.")
    assert sl.main([str(path), "--gate"]) == 1
    assert sl.main([str(path), "--gate", "--allow", "landscape"]) == 0


def test_setup_phrases_contrasts_and_other_patterns():
    text = ("P01 Here's the thing. Buckle up, because this bank isn't just a bank. It's a machine. "
            "But here's the catch: it was not cheap, but it worked. What if it fails? "
            "Let's break it down — slowly. Wow! Really. Yes. No way. That is a fragment run.")
    r = sl.lint(text)
    phrases = [h["phrase"] for h in r["setup_phrases"]]
    assert phrases == ["here's the thing", "buckle up", "here's the catch", "let's break it down"]
    assert len(r["contrasts"]) == 2
    assert len(r["what_if_openers"]) == 1
    assert len(r["dashes"]) == 1
    assert len(r["exclamations"]) == 1
    assert [f["count"] for f in r["fragment_runs"]] == [4]  # Wow! Really. Yes. No way.


def test_phrase_timing(tmp_path):
    early = "P01 How does Acme make money? It sells widgets."
    r = sl.lint(early, phrase="how does acme make money")
    assert r["phrase"]["found"] and r["phrase"]["word_index"] == 0 and r["phrase"]["within_limit"]
    filler = " ".join(["One two three four five."] * 20)  # 100 words in short sentences
    late = f"P01 {filler} How does Acme make money?"
    r = sl.lint(late, wpm=150, phrase="How does Acme make money")
    assert r["phrase"]["word_index"] == 100 and r["phrase"]["start_s"] == 40.0 and not r["phrase"]["within_limit"]
    path = _write(tmp_path, late)
    assert sl.main([str(path), "--wpm", "150", "--phrase", "how does acme make money", "--gate"]) == 1
    assert sl.main([str(path), "--wpm", "150", "--phrase", "missing phrase", "--gate"]) == 1
    assert sl.main([str(path), "--wpm", "300", "--phrase", "how does acme make money", "--gate"]) == 0


def test_runtime_opening_and_final_sentence():
    text = "P01 " + " ".join(f"w{i}" for i in range(155)) + ". Watch the next video next."
    r = sl.lint(text, wpm=155)
    assert r["words"] == 160 and abs(r["runtime_s"] - 160 / 155 * 60) < 0.1
    assert r["opening_5s"] == " ".join(f"w{i}" for i in range(12))  # 5 s at 155 wpm = 12 words
    assert r["final_sentence"] == "Watch the next video next."


def test_placeholder_counts_as_three_words():
    assert sl.word_count("Revenue was {revenue_2025} last year.") == 7


def test_gate_pass_json_and_out(tmp_path, capsys):
    path = _write(tmp_path, "P01 Acme sells widgets. It makes money on service contracts.")
    out = tmp_path / "qa" / "script-lint.json"
    assert sl.main([str(path), "--gate", "--json", "--out", str(out)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["gate"] == {"enabled": True, "passed": True, "reasons": []}
    assert json.loads(out.read_text(encoding="utf-8"))["words"] == 9


def test_human_output_mentions_guidance(tmp_path, capsys):
    path = _write(tmp_path, "P01 Acme sells widgets. Here's the thing. It works.")
    assert sl.main([str(path), "--gate"]) == 0
    text = capsys.readouterr().out
    assert "Counts guide the reviewer; the reviewer decides." in text
    assert "GATE: PASS" in text and 'First 5 seconds: "Acme sells widgets.' in text
    assert 'Final sentence: "It works."' in text


def test_usage_errors(tmp_path):
    assert sl.main([str(tmp_path / "missing.md")]) == 2
    path = _write(tmp_path, "P01 Fine.")
    assert sl.main([str(path), "--wpm", "0"]) == 2


def test_non_english_text_does_not_crash(tmp_path, capsys):
    # Norwegian: non-ASCII letters, and an ordinal ("6. kapittel") followed by a lowercase word
    text = ("# Overskrift\n\n**P01** Et selskap i Tromsø vokste i 2025. Selskapet brukte metoden som er beskrevet "
            "i 6. kapittel. Selskapet tjente mye!\n\nP02 Hvordan skjedde dette? Svaret er enkelt. Veldig enkelt. "
            "Virkelig.")
    r = sl.lint(text, phrase="hvordan skjedde dette")
    assert r["sentences"] == 7  # "6. kapittel" is an ordinal, not a sentence end
    assert r["ai_vocabulary"] == [] and r["setup_phrases"] == []
    assert [(x["opener"], x["count"]) for x in r["repeated_openings"]] == [("selskapet", 2)]
    assert r["phrase"]["found"] and r["phrase"]["within_limit"]
    assert len(r["fragment_runs"]) == 1
    path = _write(tmp_path, text)
    assert sl.main([str(path), "--gate", "--phrase", "hvordan skjedde dette"]) == 0
    assert "Tromsø" in capsys.readouterr().out
