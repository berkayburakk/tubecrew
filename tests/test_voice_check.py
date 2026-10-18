"""Tests for skills/check-voiceover/scripts/voice_check.py (no audio, no faster-whisper needed)."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "check-voiceover" / "scripts" / "voice_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("voice_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vc = _load()

SCRIPT_TEXT = ("Last year the company made 12.5 billion dollars in profit. Most of it came from chips that "
               "nobody outside the industry had heard of. Then a small rival in Ohio changed the plan.")


def check(script, heard, language="en"):
    return vc.compare(vc.normalize(script, language), vc.normalize(heard, language))


def reasons(result, status=None):
    return [i["reason"] for i in result["issues"] if status is None or i["status"] == status]


# ------------------------------------------------------------------------------------------- comparison

def test_identical_text_has_no_issues():
    result = check(SCRIPT_TEXT, SCRIPT_TEXT)
    assert result["verdict"] == "PASS"
    assert result["issues"] == []
    assert result["word_accuracy"] == 1.0


def test_punctuation_and_case_do_not_count():
    heard = SCRIPT_TEXT.upper().replace(". ", " -- ").replace(" in ", ", in ").rstrip(".") + "?!"
    assert check(SCRIPT_TEXT, heard)["issues"] == []


def test_skipped_four_word_phrase_fails():
    heard = SCRIPT_TEXT.replace(" nobody outside the industry", "")
    result = check(SCRIPT_TEXT, heard)
    assert result["verdict"] == "FAIL"
    assert reasons(result, "FAIL") == ["skipped"]
    assert result["issues"][0]["expected"] == "nobody outside the industry"
    assert result["word_accuracy"] < 1.0


def test_changed_number_fails():
    result = check(SCRIPT_TEXT, SCRIPT_TEXT.replace("12.5", "125"))
    assert result["verdict"] == "FAIL"
    issue = result["issues"][0]
    assert issue["reason"] == "number" and issue["expected"] == "12.5" and issue["heard"] == "125"
    assert "[12.5]" in issue["context"]


def test_single_word_mishearing_warns():
    result = check(SCRIPT_TEXT, SCRIPT_TEXT.replace("Ohio", "Idaho"))
    assert result["verdict"] == "PASS"
    assert reasons(result) == ["mismatch"]


def test_stray_repeat_warns():
    heard = SCRIPT_TEXT.replace("Then a small rival", "Then a small rival then a small rival")
    result = check(SCRIPT_TEXT, heard)
    assert result["verdict"] == "PASS"
    assert reasons(result, "WARN") == ["repeat"]


def test_numbers_normalize_consistently():
    assert vc.normalize("$1,000,000 and 12% growth") == ["$", "1000000", "and", "12", "%", "growth"]
    assert vc.normalize("twelve point five percent") == ["12.5", "%"]
    assert vc.normalize("one hundred and twenty five") == ["125"]
    assert vc.normalize("two thousand twenty four") == ["2024"]
    assert vc.normalize("12,5 Milliarden", "de") == ["12.5", "milliarden"]
    assert vc.normalize("1.000 Menschen", "de") == ["1000", "menschen"]
    assert check("It grew twelve percent.", "It grew 12%.")["issues"] == []
    assert vc.normalize("12.5 billion dollars") == vc.normalize("$12.5 billion") == ["$", "12.5", "billion"]
    assert vc.normalize("twelve dollars") == ["$", "12"]
    assert vc.normalize("the dollars") == ["the", "dollars"]


# ------------------------------------------------------------------------------------------- script cleaning

def test_markdown_cues_and_directions_are_stripped():
    md = """---
title: Example
---
# Chapter one

> note to self: check this number later

<!-- a comment
over two lines -->
[[hook]] The **first** sentence [ON SCREEN: big chart] is _spoken_.
[B-ROLL factory floor]
- A list item with a [link text](https://example.com).

```
code is not spoken
```
| table | row |
---
Last line.
"""
    spoken = vc.clean_script(md)
    assert spoken.split("\n") == ["The first sentence is spoken.", "A list item with a link text.", "Last line."]
    tokens = vc.normalize(spoken)
    assert tokens[:5] == ["the", "first", "sentence", "is", "spoken"]
    for gone in ("chapter", "note", "comment", "hook", "chart", "factory", "code", "table", "title"):
        assert gone not in tokens


def test_paragraph_ids_are_labels_not_speech():
    md = "**P01** The company sold three hundred units.\n\nP02. Then it stopped.\n\nP03: Prices fell.\n"
    assert vc.clean_script(md).split("\n") == ["The company sold three hundred units.", "Then it stopped.",
                                               "Prices fell."]
    assert vc.clean_script(md, keep_ids=True).split("\n")[0] == "P01 The company sold three hundred units."
    heard = vc.normalize("The company sold three hundred units. Then it stopped. Prices fell.")
    result = vc.compare(vc.normalize(vc.clean_script(md)), heard)
    assert result["matched_words"] == result["script_words"]


def test_placeholders_detected_after_cleaning():
    assert vc.find_placeholders(vc.clean_script("We made {revenue} last year.")) == ["{revenue}"]
    assert vc.find_placeholders(vc.clean_script("> {note}\nPlain text [ON SCREEN: {chart}].")) == []


# ------------------------------------------------------------------------------------------- transcripts

SRT = """1
00:00:01,000 --> 00:00:03,000
Last year the company

2
00:00:03,000 --> 00:00:05,500
made <i>12.5</i> billion dollars.
"""


def test_srt_parsing_and_times():
    pieces = vc.parse_cues(SRT)
    assert [p["text"] for p in pieces] == ["Last year the company", "made 12.5 billion dollars."]
    tokens, times = vc.heard_tokens(pieces)
    assert tokens == ["last", "year", "the", "company", "made", "$", "12.5", "billion"]
    assert times[0][0] == pytest.approx(1.0)
    assert times[4][0] == pytest.approx(3.0)
    assert times[3][1] == pytest.approx(3.0)


def test_vtt_parsing_drops_header_and_rolling_duplicates():
    vtt = ("WEBVTT\nKind: captions\n\nNOTE made by hand\n\n00:01.000 --> 00:02.000 align:start\nhello there\n\n"
           "00:02.000 --> 00:03.000\nhello there\n<00:00:02.500><c>general</c> kenobi\n")
    pieces = vc.parse_cues(vtt)
    assert [p["text"] for p in pieces] == ["hello there", "general kenobi"]
    assert pieces[0]["start"] == 1.0


def test_vtt_entities_are_decoded():
    # WebVTT escapes & < >; undecoded, "&gt;&gt;" and "AT&amp;T" showed up as stray words "gt" and "amp".
    vtt = "WEBVTT\n\n00:01.000 --> 00:03.000\n&gt;&gt; AT&amp;T grew\n"
    pieces = vc.parse_cues(vtt)
    assert vc.heard_tokens(pieces)[0] == vc.normalize("AT&T grew") == ["at", "t", "grew"]


def test_whisper_json_shapes():
    with_words = {"segments": [{"start": 0.0, "end": 2.0, "text": " Hello world",
                                "words": [{"word": " Hello", "start": 0.1, "end": 0.5},
                                          {"word": " world", "start": 0.6, "end": 1.0}]}]}
    tokens, times = vc.heard_tokens(vc.parse_json_transcript(with_words))
    assert tokens == ["hello", "world"] and times[1] == (0.6, 1.0)
    segment_list = [{"start": 0, "end": 4, "text": "one two three four"}]
    tokens, times = vc.heard_tokens(vc.parse_json_transcript(segment_list))
    assert tokens == ["1", "2", "3", "4"] and times[2][0] == pytest.approx(2.0)
    words = [{"word": "Hi", "start": 0, "end": 0.2}, {"word": "there", "start": 0.3, "end": 0.6}]
    assert vc.heard_tokens(vc.parse_json_transcript(words))[0] == ["hi", "there"]


def test_non_ascii_letters_survive():
    text = "Le chiffre d'affaires a augmenté; une journée tiède à Zürich."
    tokens = vc.normalize(text, "fr")
    assert tokens[:3] == ["le", "chiffre", "daffaires"]
    assert "augmenté" in tokens and "tiède" in tokens and "zürich" in tokens
    result = check(text, text.replace("tiède", "tiede"), "fr")
    assert reasons(result) == ["mismatch"]


def test_dotted_and_dotless_i_casefold_for_turkic_language_codes():
    dotted_capital, dotless_small = chr(0x130), chr(0x131)
    assert vc.normalize("I " + dotted_capital, "az") == [dotless_small, "i"]
    assert vc.normalize("I " + dotted_capital, "en") == ["i", "i"]


# ------------------------------------------------------------------------------------------- command line

def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_pass_and_relative_report(tmp_path, capsys):
    script = write(tmp_path / "script.md", "# Intro\n\nLast year the company made 12.5 billion dollars.\n")
    srt = write(tmp_path / "voice" / "narration.srt", SRT)
    code = vc.main([str(script), str(srt)])
    out = capsys.readouterr().out
    assert code == 0 and "VERDICT: PASS" in out
    report_path = tmp_path / "qa" / "voice-check.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS" and report["word_accuracy"] == 1.0
    assert report["script"] == "script.md" and report["transcript"] == "voice/narration.srt"
    assert report["timestamps"] == "segment"
    assert str(tmp_path) not in report_path.read_text(encoding="utf-8")


def test_cli_fail_with_json_output(tmp_path, capsys):
    script = write(tmp_path / "script.md", "Last year the company made 12.5 billion dollars.\n")
    transcript = write(tmp_path / "voice" / "t.json", json.dumps(
        {"segments": [{"start": 0, "end": 3, "text": "Last year the company made 125 billion dollars."}]}))
    code = vc.main([str(script), str(transcript), "--json", "--out", str(tmp_path / "out" / "vc.json")])
    printed = json.loads(capsys.readouterr().out)
    assert code == 1 and printed["verdict"] == "FAIL"
    assert printed["counts"]["FAIL"] == 1
    assert printed["issues"][0]["reason"] == "number" and "start" in printed["issues"][0]
    assert (tmp_path / "out" / "vc.json").is_file()


def test_cli_placeholder_is_usage_error(tmp_path, capsys):
    script = write(tmp_path / "script.md", "We made {revenue} last year.\n")
    srt = write(tmp_path / "t.srt", SRT)
    assert vc.main([str(script), str(srt)]) == 2
    assert "placeholder" in capsys.readouterr().err
    assert not (tmp_path / "qa").exists()


def test_cli_audio_without_faster_whisper_is_usage_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    script = write(tmp_path / "script.md", "Hello world.\n")
    audio = tmp_path / "voice" / "narration.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"RIFF0000WAVE")
    assert vc.main([str(script), str(audio)]) == 2
    assert "pip install faster-whisper" in capsys.readouterr().err


def test_cli_audio_passes_base_language_code_to_whisper(tmp_path, capsys, monkeypatch):
    # "--language en-US" works for normalizing but faster-whisper only accepts "en".
    seen = {}

    class FakeModel:
        def __init__(self, name, device, compute_type):
            pass

        def transcribe(self, path, language=None, word_timestamps=False):
            seen["language"] = language
            segment = types.SimpleNamespace(start=0.0, end=1.0, text=" Hello world.", words=[])
            return iter([segment]), types.SimpleNamespace(language=language)

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    script = write(tmp_path / "script.md", "Hello world.\n")
    audio = tmp_path / "voice" / "narration.wav"
    audio.parent.mkdir()
    audio.write_bytes(b"RIFF0000WAVE")
    assert vc.main([str(script), str(audio), "--language", "en-US"]) == 0
    assert seen["language"] == "en"
    assert (tmp_path / "qa" / "voice-check-transcript.json").is_file()
    assert "VERDICT: PASS" in capsys.readouterr().out


def test_cli_missing_files(tmp_path, capsys):
    assert vc.main([str(tmp_path / "none.md"), str(tmp_path / "none.srt")]) == 2
    assert "not found" in capsys.readouterr().err


def test_rel_path_outside_project_keeps_only_the_file_name(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    assert vc.rel_path(tmp_path / "other" / "take.srt", project) == "take.srt"


# ------------------------------------------------------------------------------------------- paragraphs and pace

P1 = "Last year the company made 12.5 billion dollars in profit."
P2 = "Most of it came from chips that nobody outside the industry had heard of."
P3 = "Then a small rival in Ohio changed the plan for everyone."
THREE = f"# Story\n\n{P1}\n\n{P2}\n\n{P3}\n"


def spoken_words(text, start, wpm):
    """Word-level transcript items for `text` read at `wpm` from `start`; returns (words, end time)."""
    step = 60.0 / wpm
    words = []
    t = start
    for w in text.split():
        words.append({"word": " " + w, "start": round(t, 3), "end": round(t + step * 0.9, 3)})
        t += step
    return words, t


def whisper_json(*parts, gap=0.6):
    """parts: (text, wpm) read one after another with `gap` seconds between them."""
    segments, t = [], 0.5
    for text, wpm in parts:
        words, end = spoken_words(text, t, wpm)
        segments.append({"start": words[0]["start"], "end": words[-1]["end"], "text": " " + text, "words": words})
        t = end + gap
    return {"segments": segments}


def run_json(tmp_path, capsys, transcript, *extra, script=THREE):
    s = write(tmp_path / "script.md", script)
    t = write(tmp_path / "voice" / "narration.json", json.dumps(transcript))
    code = vc.main([str(s), str(t), "--json", *extra])
    return code, json.loads(capsys.readouterr().out)


def test_paragraph_rows_with_word_timings(tmp_path, capsys):
    code, report = run_json(tmp_path, capsys, whisper_json((P1, 180), (P2, 180), (P3, 180)))
    assert code == 0 and report["verdict"] == "PASS" and report["timestamps"] == "word"
    rows = report["paragraphs"]
    assert [r["paragraph"] for r in rows] == [1, 2, 3]
    assert [r["words"] for r in rows] == [10, 14, 11]
    assert all(r["status"] == "PASS" and r["pace"] == "ok" and r["fidelity"] == 1.0 for r in rows)
    assert 175 <= rows[1]["wpm"] <= 190
    assert rows[0]["start"] == 0.5 and rows[1]["start"] > rows[0]["end"]
    assert report["pace"]["measured"] is True and report["pace"]["warn_band"] == [150.0, 215.0]
    assert report["failing_paragraphs"] == []


def test_repeat_loop_fails_the_paragraph_it_repeats(tmp_path, capsys):
    # The model read paragraph 2 three times: the words are all there, only the pace gives it away.
    looped = P2 + " " + P2 + " " + P2
    code, report = run_json(tmp_path, capsys, whisper_json((P1, 180), (looped, 180), (P3, 180)))
    assert code == 1 and report["verdict"] == "FAIL"
    rows = report["paragraphs"]
    assert rows[1]["status"] == "FAIL" and rows[1]["pace"] == "slow" and rows[1]["wpm"] < 100
    assert rows[0]["status"] == "PASS" and rows[2]["status"] == "PASS"
    assert report["failing_paragraphs"] == [2]
    pace = [i for i in report["issues"] if i["kind"] == "pace"]
    assert len(pace) == 1 and pace[0]["reason"] == "slow" and pace[0]["paragraph"] == 2
    assert pace[0]["expected"] == "100-300 wpm" and "start" in pace[0]
    assert all(i["paragraph"] == 2 for i in report["issues"])
    assert report["counts"]["FAIL"] >= 1


def test_pace_bands_warn_then_fail(tmp_path, capsys):
    code, report = run_json(tmp_path, capsys, whisper_json((P1, 180), (P2, 240), (P3, 180)))
    assert code == 0 and report["paragraphs"][1]["pace"] == "fast"
    assert report["paragraphs"][1]["status"] == "WARN" and report["counts"]["WARN"] == 1
    code, report = run_json(tmp_path, capsys, whisper_json((P1, 180), (P2, 240), (P3, 180)), "--wpm-fail-band",
                            "100,230")
    assert code == 1 and report["failing_paragraphs"] == [2]
    code, report = run_json(tmp_path, capsys, whisper_json((P1, 180), (P2, 240), (P3, 180)), "--wpm-band", "off",
                            "--wpm-fail-band", "off")
    assert code == 0 and report["paragraphs"][1]["pace"] == "ok" and report["pace"]["warn_band"] is None


def test_human_voice_band(tmp_path, capsys):
    slow_human = whisper_json((P1, 140), (P2, 140), (P3, 140))
    assert run_json(tmp_path, capsys, slow_human)[1]["counts"]["WARN"] == 3
    code, report = run_json(tmp_path, capsys, slow_human, "--wpm-band", "130,190")
    assert code == 0 and report["counts"]["WARN"] == 0


def test_short_paragraph_gets_no_pace_verdict(tmp_path, capsys):
    script = f"{P1}\n\nSo what happened?\n"
    code, report = run_json(tmp_path, capsys, whisper_json((P1, 180), ("So what happened?", 40)), script=script)
    assert code == 0
    short = report["paragraphs"][1]
    assert short["words"] == 3 and short["wpm"] is not None and short["pace"] is None


def test_segment_timings_report_paragraphs_without_pace(tmp_path, capsys):
    srt = ("1\n00:00:00,500 --> 00:00:04,000\n" + P1 + "\n\n2\n00:00:04,500 --> 00:00:30,000\n" + P2 + "\n\n"
           "3\n00:00:30,500 --> 00:00:34,000\n" + P3 + "\n")
    s = write(tmp_path / "script.md", THREE)
    t = write(tmp_path / "voice" / "narration.srt", srt)
    assert vc.main([str(s), str(t)]) == 0   # 14 words in 25 s would be "slow", but cue times are not word times
    out = capsys.readouterr().out
    assert "pace not measured" in out
    report = json.loads((tmp_path / "qa" / "voice-check.json").read_text(encoding="utf-8"))
    assert report["timestamps"] == "segment" and report["pace"]["measured"] is False
    assert all(r["wpm"] is None and r["start"] is not None for r in report["paragraphs"])


def test_issues_carry_their_paragraph(tmp_path, capsys):
    transcript = whisper_json((P1, 180), (P2, 180), (P3.replace("Ohio", "Idaho"), 180))
    code, report = run_json(tmp_path, capsys, transcript)
    assert code == 0
    assert [(i["reason"], i["paragraph"]) for i in report["issues"]] == [("mismatch", 3)]
    assert report["paragraphs"][2]["status"] == "WARN" and report["paragraphs"][2]["fidelity"] < 1.0


def test_human_output_lists_flagged_paragraphs(tmp_path, capsys):
    s = write(tmp_path / "script.md", THREE)
    looped = whisper_json((P1, 180), (P2 + " " + P2 + " " + P2, 180), (P3, 180))
    t = write(tmp_path / "voice" / "narration.json", json.dumps(looped))
    assert vc.main([str(s), str(t)]) == 1
    out = capsys.readouterr().out
    assert "Paragraphs: 3" in out and "slow" in out
    assert "Regenerate or re-record paragraphs: 2" in out and "VERDICT: FAIL" in out


def test_bad_band_is_a_usage_error(tmp_path, capsys):
    s = write(tmp_path / "script.md", THREE)
    t = write(tmp_path / "t.srt", SRT)
    with pytest.raises(SystemExit) as stop:
        vc.main([str(s), str(t), "--wpm-band", "fast"])
    assert stop.value.code == 2
    assert vc.parse_band("150, 215") == (150.0, 215.0) and vc.parse_band("off") is None
    with pytest.raises(ValueError):
        vc.parse_band("215,150")


def test_paragraph_texts_and_word_count():
    spoken = vc.clean_script(THREE)
    assert vc.paragraph_texts(spoken) == [P1, P2, P3]
    assert vc.word_count("It cost $12.5 billion - really.") == 5


def test_transcribe_turns_off_context_conditioning(tmp_path, monkeypatch):
    seen = []

    class FakeModel:
        def __init__(self, name, device, compute_type):
            seen.append(("load", device))

        def transcribe(self, path, **options):
            seen.append(options)
            word = types.SimpleNamespace(word=" Hello", start=0.0, end=0.4)
            segment = types.SimpleNamespace(start=0.0, end=0.5, text=" Hello", words=[word])
            return iter([segment]), types.SimpleNamespace(language="en")

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    whisper = vc.Whisper("small", beam_size=7)
    first = whisper.transcribe(tmp_path / "a.wav", "en-GB")
    whisper.transcribe(tmp_path / "b.wav", "en")
    assert [s for s in seen if isinstance(s, tuple)] == [("load", "auto")]   # loaded once, reused
    options = seen[1]
    assert options["condition_on_previous_text"] is False and options["beam_size"] == 7
    assert options["word_timestamps"] is True and options["language"] == "en"
    assert first["decode"] == {"beam_size": 7, "condition_on_previous_text": False}
    assert first["segments"][0]["words"][0] == {"word": " Hello", "start": 0.0, "end": 0.4}
    assert vc.transcribe(tmp_path / "c.wav", "en", "small")["decode"]["beam_size"] == vc.WHISPER_BEAM_SIZE
