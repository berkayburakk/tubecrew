"""Tests for skills/check-voiceover/scripts/voice_check.py (no audio, no faster-whisper needed)."""
import importlib.util
import json
import sys
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


def test_cli_missing_files(tmp_path, capsys):
    assert vc.main([str(tmp_path / "none.md"), str(tmp_path / "none.srt")]) == 2
    assert "not found" in capsys.readouterr().err
