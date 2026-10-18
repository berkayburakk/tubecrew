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


def test_no_and_month_abbreviations_only_before_a_number():
    def split(t):
        return [s for _, s in sl.split_sentences(t)]
    assert split("Did it work? No. It failed.") == ["Did it work?", "No.", "It failed."]
    assert split("The answer is no. The bank said so.") == ["The answer is no.", "The bank said so."]
    assert split("He was No. 1 in sales. Then he quit.") == ["He was No. 1 in sales.", "Then he quit."]
    assert split("On Oct. 18 they met. On Sept. 2 they split.") == ["On Oct. 18 they met.", "On Sept. 2 they split."]
    assert split("I met Jan. She left.") == ["I met Jan.", "She left."]


def test_phrase_with_curly_apostrophe_is_found():
    r = sl.lint("P01 What's a credit score? It is a number.", phrase="what’s a credit score")
    assert r["phrase"]["found"] and r["phrase"]["within_limit"]


def test_bom_does_not_hide_front_matter(tmp_path, capsys):
    path = tmp_path / "script.md"
    path.write_bytes(b"\xef\xbb\xbf---\ntitle: Leverage landscape notes\n---\n# Heading\n\nP01 Acme sells widgets.\n")
    assert sl.main([str(path), "--gate", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["words"] == 3


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


# ------------------------------------------------------------------------------------------------ opening checks

def _codes(text, **kw):
    return [w["code"] for w in sl.lint(text, **kw)["opening"]["warnings"]]


def _filler(n):
    """n words in plain sentences of five words: no numbers, names or objects."""
    return " ".join(["Some words go right here."] * (n // 5))


GOOD_OPENING = "P01 Acme sold 400 trucks last May. Then the money ran out. " + _filler(60)


def test_good_opening_has_no_warnings():
    r = sl.lint(GOOD_OPENING)
    assert r["opening"]["warnings"] == []
    assert r["opening"]["triangle"] == {"proper_noun": True, "concrete_object": True, "number": True}
    assert r["opening"]["first_number_s"] == round(2 / 155 * 60, 1)


def test_already_know_phrases_within_17_seconds():
    early = f"P01 Acme sold 400 trucks. {_filler(15)} You’ve heard this story before. " + _filler(40)
    hits = [w for w in sl.lint(early)["opening"]["warnings"] if w["code"] == "opening_phrase"]
    assert len(hits) == 1 and hits[0]["family"] == "already know" and hits[0]["phrase"] == "You've heard"
    assert hits[0]["start_s"] == round(19 / 155 * 60, 1)   # curly apostrophe matched, ~7 s
    late = f"P01 Acme sold 400 trucks. {_filler(50)} You already know the rest."
    assert "opening_phrase" not in _codes(late)          # ~21 s: outside the window
    patience = "P01 Acme sold 400 trucks. Bear with me for a second. Welcome back to the story."
    families = [w["family"] for w in sl.lint(patience)["opening"]["warnings"] if w["code"] == "opening_phrase"]
    assert families == ["asks for patience", "greeting"]


def test_phrase_that_starts_inside_the_window_is_found():
    # words 0-43 start before 17 s at 155 wpm; the phrase starts at word 43 and ends after the window
    text = "P01 Acme sold 400 trucks. " + " ".join(["word"] * 39) + " we all know the rest."
    assert "opening_phrase" in _codes(text)
    text = "P01 Acme sold 400 trucks. " + " ".join(["word"] * 40) + " we all know the rest."
    assert "opening_phrase" not in _codes(text)


def test_imagine_opener():
    assert "imagine_opener" in _codes("P01 Imagine 400 trucks at Acme. " + _filler(40))
    assert "imagine_opener" not in _codes("P01 Acme imagined 400 trucks. " + _filler(40))


def test_concrete_number_in_first_12_seconds():
    assert "no_number" in _codes("P01 Acme had a strange year. " + _filler(40) + " It lost 5 trucks.")
    for opening in ("It lost three trucks.", "It lost {loss_2025} on trucks.", "Margins hit 40%."):
        assert "no_number" not in _codes(f"P01 {opening} " + _filler(40)), opening
    assert "no_number" in _codes("P01 Acme built GPT-5 trucks. " + _filler(40))   # a name, not a quantity
    msg = next(w["message"] for w in sl.lint("P01 Acme had a strange year. " + _filler(40) + " It lost 5 trucks.")
               ["opening"]["warnings"] if w["code"] == "no_number")
    assert "the first one comes at ~" in msg


def test_first_sentence_length():
    twelve = "P01 Acme sold 400 trucks to a farm in a small town nearby. " + _filler(40)
    thirteen = "P01 Acme sold 400 trucks to a farm in a very small town nearby. " + _filler(40)
    assert sl.lint(twelve)["opening"]["first_sentence_words"] == 12
    assert "long_first_sentence" not in _codes(twelve)
    assert "long_first_sentence" in _codes(thirteen)


def test_in_this_video_in_the_first_sentence():
    assert "this_video" in _codes("P01 In this video, Acme loses 400 trucks. " + _filler(40))
    # a very short first sentence is read together with the next one
    assert "this_video" in _codes("P01 Acme lost $5 billion. In this video we find out why. " + _filler(40))
    assert "this_video" not in _codes(GOOD_OPENING + " In this video we go further.")


def test_late_intro_marker(tmp_path):
    late = "P01 Acme sold 400 trucks last May. " + _filler(65) + "\n\n[INTRO: logo sting]\n\nP02 Next part."
    r = sl.lint(late)
    assert r["opening"]["intro"]["words_before"] == 71
    assert r["opening"]["intro"]["start_s"] == round(71 / 155 * 60, 1)
    assert "late_intro" in [w["code"] for w in r["opening"]["warnings"]]
    on_time = "P01 Acme sold 400 trucks last May. " + _filler(40) + " [[intro]] Then more."
    r = sl.lint(on_time)
    assert r["opening"]["intro"]["words_before"] == 46 and "late_intro" not in _codes(on_time)
    table_only = GOOD_OPENING + "\n\n| P | change |\n|---|---|\n| P02 | moved [INTRO] |\n"
    assert sl.lint(table_only)["opening"]["intro"] is None


def test_opening_triangle():
    r = sl.lint("P01 It was a strange year for everyone in town. " + _filler(40) + " Then came 2025.")
    tri = r["opening"]["triangle"]
    assert tri == {"proper_noun": False, "concrete_object": False, "number": False}
    msg = next(w["message"] for w in r["opening"]["warnings"] if w["code"] == "triangle")
    assert "no proper noun and no concrete object" in msg
    # a capital at the start of a sentence is not a proper noun; "I" is not either
    assert not sl.lint("P01 Trucks sold well. I sold 400.")["opening"]["triangle"]["proper_noun"]


def test_opening_warnings_never_gate(tmp_path, capsys):
    path = _write(tmp_path, "P01 Imagine a year that everyone knows about, in this video, with nothing in it at all.")
    assert sl.main([str(path), "--gate"]) == 0
    out = capsys.readouterr().out
    assert "Opening checks (no number spoken; warnings, not a gate): " in out and "WARN opens with" in out


# ---------------------------------------------------------------------------------------------- vague attributions

def test_vague_attributions_gate_and_named_sources(tmp_path):
    text = ("P01 Studies show that Acme's prices rose. Experts said it would last. "
            "Research has shown the same, according to the OECD. A 2023 Gallup poll shows it, and studies show it. "
            "Data from the Census Bureau shows it, as research shows.")
    hits = sl.lint(text)["vague_attributions"]
    assert [(h["term"], h["named_source"]) for h in hits] == [
        ("studies show", False), ("experts say", False), ("research shows", True), ("studies show", True),
        ("research shows", True)]
    r = sl.lint(text)
    assert "2 vague attributions without a source" in r["gate_reasons"]
    path = _write(tmp_path, "P01 Studies show it works.")
    assert sl.main([str(path)]) == 0
    assert sl.main([str(path), "--gate"]) == 1
    assert sl.main([str(path), "--gate", "--allow", "studies show"]) == 0
    assert sl.main([str(path), "--gate", "--allow", "Studies Show"]) == 0


def test_vague_attribution_inflections():
    text = "P01 Many argued for it. Some say no. It is widely regarded as safe. It's believed to work."
    assert [h["term"] for h in sl.lint(text)["vague_attributions"]] == [
        "many argue", "some say", "widely regarded", "it is believed"]
    assert sl.lint("P01 Some sayings are old. Researchers showed up.")["vague_attributions"] == []


# ---------------------------------------------------------------------------------------------- visual triggers

TRIGGER_SCRIPT = ("P01 Acme’s price tripled in 2024. [[cue-1]] The price kept rising.\n\n"
                  "P02 Then margins hit 40% and it's over. The $5 billion deal closed.")


def test_triggers_json(tmp_path, capsys):
    script = _write(tmp_path, TRIGGER_SCRIPT)
    good = tmp_path / "good.json"
    good.write_text(json.dumps([{"word": "price", "nth": 1, "paragraph": "P01"}, {"word": "40%"},
                                {"word": "its", "paragraph": "P2"}, {"word": "$5 billion"},
                                {"word": "Acme's", "paragraph": "P01"}, "tripled"]), encoding="utf-8")
    assert sl.main([str(script), "--triggers", str(good)]) == 0
    assert "TRIGGERS: PASS - 6 of 6 found" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"triggers": [{"word": "price", "nth": 2, "paragraph": "P01"},
                                            {"word": "price", "paragraph": "P02"}, {"word": "booms"}]}),
                   encoding="utf-8")
    assert sl.main([str(script), "--triggers", str(bad)]) == 1      # fails without --gate
    out = capsys.readouterr().out
    assert 'MISSING trigger P01 "price" #2 (found 2 times)' in out and "TRIGGERS: FAIL - 0 of 3 found" in out
    assert sl.main([str(script), "--triggers", str(bad), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["triggers"]["missing"][2] == {"word": "booms", "nth": 0, "paragraph": None, "found": 0}
    assert "3 of 3 visual triggers missing" in data["gate"]["reasons"]


def test_triggers_plain_lines(tmp_path):
    script = _write(tmp_path, TRIGGER_SCRIPT)
    plain = tmp_path / "triggers.txt"
    plain.write_text("# word triggers\nP01 price 1\ntripled\nP02 deal\n\n2024\n", encoding="utf-8")
    assert [t["paragraph"] for t in sl.load_triggers(plain)] == ["P01", None, "P02", None]
    assert sl.main([str(script), "--triggers", str(plain)]) == 0
    plain.write_text("P02 tripled\n", encoding="utf-8")
    assert sl.main([str(script), "--triggers", str(plain)]) == 1


def test_duplicate_cues_are_listed_with_lines_and_gated(tmp_path, capsys):
    text = "\n".join([
        "# Plan [[chart]]",                                       # L1 heading: not spoken
        "> [[chart]] note for the editor",                        # L2 note: not spoken
        "P01 Sales [[chart]]tripled in a year. [[map]]Ohio came first.",
        "It kept growing. [[ Chart ]]Then it stopped.",           # L4 same paragraph, case and spacing ignored
        "",
        "P02 The [[map]]plant closed. [[logo]]Acme moved on.",
        "<!-- [[logo]] hidden -->",
        "",
        "A last line with [[chart]] again.",                      # L9 no paragraph id
        "```", "[[logo]]", "```",
    ])
    dupes = sl.duplicate_cues(text)
    assert dupes == [{"cue": "chart", "count": 3, "lines": [3, 4, 9], "locs": ["P01", "P01", "L9"]},
                     {"cue": "map", "count": 2, "lines": [3, 6], "locs": ["P01", "P02"]}]
    assert sl.lint(text)["sentences"] == 7   # the cues are still not spoken words
    path = _write(tmp_path, text)
    assert sl.main([str(path)]) == 0         # guidance without --gate
    out = capsys.readouterr().out
    assert 'Duplicate [[cue]] anchors: 2 -> "chart" x3 at L3, L4, L9; "map" x2 at L3, L6' in out
    assert sl.main([str(path), "--gate", "--json"]) == 1
    assert "2 duplicate cue anchors" in json.loads(capsys.readouterr().out)["gate"]["reasons"]


def test_unique_cues_pass_the_gate(tmp_path, capsys):
    path = _write(tmp_path, "P01 Acme [[chart-1]]sells widgets. [[chart-2]]It makes money on service contracts.")
    assert sl.lint(path.read_text(encoding="utf-8"))["duplicate_cues"] == []
    assert sl.main([str(path), "--gate"]) == 0
    assert "Duplicate [[cue]] anchors: 0" in capsys.readouterr().out


def test_trigger_file_errors(tmp_path):
    script = _write(tmp_path, TRIGGER_SCRIPT)
    assert sl.main([str(script), "--triggers", str(tmp_path / "missing.json")]) == 2
    for content in ('[{"nth": 1}]', '[{"word": "price", "nth": -1}]', '{"triggers": 3}', "[not json",
                    '[{"word": "price", "paragraph": "intro"}]', "!!!\n"):
        f = tmp_path / "t.json"
        f.write_text(content, encoding="utf-8")
        assert sl.main([str(script), "--triggers", str(f)]) == 2, content
