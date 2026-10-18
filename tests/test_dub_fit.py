"""Tests for skills/localize/scripts/dub_fit.py (synthetic scripts, transcripts and audio only).

Placement and parsing tests run everywhere. Audio files are tiny WAVs written with the standard library; the tests
that trim silence or read compressed audio need ffmpeg/ffprobe and skip when they are not installed.
"""
import importlib.util
import json
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "localize" / "scripts" / "dub_fit.py"


def _load():
    spec = importlib.util.spec_from_file_location("dub_fit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


df = _load()
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
RATE = 8000


def write_wav(path: Path, tone_s: float, lead_s: float = 0.0, trail_s: float = 0.0) -> Path:
    """Mono 16-bit WAV: silence, a 440 Hz tone, silence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    frames += b"\x00\x00" * int(lead_s * RATE)
    for i in range(int(tone_s * RATE)):
        frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / RATE)))
    frames += b"\x00\x00" * int(trail_s * RATE)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return path


SOURCE = """# How Acme lost the quarter

> Note: not spoken.

**P01** Acme sold widgets for ten years. [[cue-open]]

P02 Then revenue hit $12.5 billion in one quarter.
[ON SCREEN: revenue chart]

P03 Nobody saw the crash coming.
"""

# (word, start) per paragraph; every word lasts 0.4 s
SPOKEN = [
    ("Acme sold widgets for ten years.", 0.0),
    ("Then revenue hit twelve point five billion dollars in one quarter.", 4.0),
    ("No body saw the crash coming.", 10.0),   # "Nobody" misheard as two words
]


def transcript_json(spoken=SPOKEN, step=0.4) -> dict:
    segments = []
    for text, start in spoken:
        words = []
        t = start
        for w in text.split():
            words.append({"word": " " + w, "start": round(t, 3), "end": round(t + step, 3)})
            t += step
        segments.append({"start": start, "end": round(t, 3), "text": " " + text, "words": words})
    return {"segments": segments}


def make_project(tmp_path: Path, lengths: dict, spoken=SPOKEN) -> Path:
    """A project folder with script.md, voice/narration.json and dub/es/voice/P0N.wav of the given lengths."""
    project = tmp_path / "my video"
    project.mkdir()
    (project / "script.md").write_text(SOURCE, encoding="utf-8")
    (project / "voice").mkdir()
    (project / "voice" / "narration.json").write_text(json.dumps(transcript_json(spoken)), encoding="utf-8")
    for number, seconds in lengths.items():
        write_wav(project / "dub" / "es" / "voice" / f"P{number:02d}.wav", seconds)
    return project


def _args(project: Path, *extra):
    return [str(project / "script.md"), str(project / "voice" / "narration.json"),
            str(project / "dub" / "es" / "voice"), "--lang", "es", *extra]


# ------------------------------------------------------------------------------------------------ placement

def test_place_fits_without_speedup():
    rows = df.place([0.0, 10.0, 20.0], [8.0, 8.0, 8.0], 30.0)
    assert [r["start_s"] for r in rows] == [0.0, 10.0, 20.0]
    assert all(r["speed"] == 1.0 and r["overflow_s"] == 0.0 for r in rows)


def test_place_speeds_up_within_cap_and_starts_early():
    rows = df.place([0.0, 10.0], [12.0, 5.0], 20.0)
    # room for P01: next anchor + 1 s late - 0.6 s gap = 10.4 s
    assert rows[0]["speed"] == pytest.approx(12.0 / 10.4)
    assert rows[1]["start_s"] == pytest.approx(11.0) and rows[1]["drift_s"] == pytest.approx(1.0)
    rows = df.place([0.0, 5.0, 10.0], [3.0, 5.8, 3.0], 20.0)
    assert rows[1]["start_s"] == pytest.approx(4.0)      # would not fit at 5.0, so it starts 1 s early
    assert rows[1]["speed"] == 1.0


def test_place_over_cap_overflows_and_drifts():
    rows = df.place([0.0, 10.0], [14.0, 5.0], 20.0)
    assert rows[0]["speed"] == pytest.approx(14.0 / 10.4)
    assert rows[0]["overflow_s"] == pytest.approx(14.0 / 1.25 - 10.4)
    assert rows[1]["drift_s"] == pytest.approx(14.0 / 1.25 + 0.6 - 10.0)
    # every paragraph a little too long: the drift adds up past 4 s
    anchors = [i * 10.0 for i in range(8)]
    rows = df.place(anchors, [14.0] * 8, 80.0)
    assert max(r["drift_s"] for r in rows) > df.DRIFT_S


def test_parse_silence():
    stderr = ("[silencedetect @ 0x1] silence_start: -0.002\n[silencedetect @ 0x1] silence_end: 0.5 | "
              "silence_duration: 0.5\n[silencedetect @ 0x1] silence_start: 1.5\n")
    assert df.parse_silence(stderr, 2.0) == (0.5, 0.5)
    assert df.parse_silence("", 2.0) == (0.0, 0.0)
    assert df.parse_silence("silence_start: 0\nsilence_end: 2.0", 2.0) == (2.0, 0.0)   # all silent
    assert df.parse_silence("silence_start: 0.8\nsilence_end: 1.0", 2.0) == (0.0, 0.0)  # a pause inside


def test_audio_file_names_map_to_paragraphs(tmp_path):
    for name in ("P01.wav", "p02-es.wav", "es_P3.wav"):
        write_wav(tmp_path / name, 0.1)
    assert sorted(df.collect_audio([tmp_path])) == [1, 2, 3]
    write_wav(tmp_path / "P01-take2.wav", 0.1)
    with pytest.raises(df.UsageError, match="two audio files for P01"):
        df.collect_audio([tmp_path])


# ------------------------------------------------------------------------------------------------ anchors

def test_anchors_from_word_timed_transcript():
    vc = df.load_voice_check()
    paras, preamble = df.script_paragraphs(SOURCE, vc)
    assert [p["id"] for p in paras] == ["P01", "P02", "P03"] and preamble == []
    pieces = vc.parse_json_transcript(transcript_json())
    heard, times = df.timed_tokens(pieces, "en", vc)
    anchors, ratio = df.find_anchors(paras, preamble, heard, times, "en", vc)
    # digits in the script match number words in the transcript; the misheard first word still starts P03
    assert anchors == [0.0, 4.0, 10.0]
    assert ratio > 0.9


def test_missing_paragraph_anchor_is_estimated():
    filled, estimated = df.fill_anchors([0.0, None, 10.0, None], 0.0, 20.0)
    assert filled == [0.0, 5.0, 10.0, 15.0] and estimated == [False, True, False, True]


# ------------------------------------------------------------------------------------------------ end to end

def test_dub_that_fits_passes_and_writes_report(tmp_path, capsys):
    project = make_project(tmp_path, {1: 3.0, 2: 5.5, 3: 2.0})
    assert df.main(_args(project, "--no-trim")) == 0
    out = capsys.readouterr().out
    assert "VERDICT: PASS" in out and "Report: qa/dub-fit-es.json" in out
    report = json.loads((project / "qa" / "dub-fit-es.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS" and report["language"] == "es"
    assert [r["anchor_s"] for r in report["paragraphs"]] == [0.0, 4.0, 10.0]
    assert report["paragraphs"][0]["audio"] == "dub/es/voice/P01.wav"
    assert report["transcript"] == "voice/narration.json"
    assert report["narration_end_s"] == pytest.approx(12.4)
    assert report["summary"]["overflow_s"] == 0.0 and report["trimmed_silence"] is False


def test_too_long_and_missing_paragraphs_fail(tmp_path, capsys):
    # P02 starts early, right after P01 and its gap (3.6 s), and may end at 10.0 + 1 s late - 0.6 s gap:
    # 6.8 s of room for 9 s of speech needs 1.32x
    project = make_project(tmp_path, {1: 3.0, 2: 9.0})
    assert df.main(_args(project, "--no-trim", "--json")) == 1
    report = json.loads(capsys.readouterr().out)
    rows = {r["id"]: r for r in report["paragraphs"]}
    assert rows["P02"]["start_s"] == pytest.approx(3.6) and rows["P02"]["drift_s"] == pytest.approx(-0.4)
    assert rows["P02"]["status"] == "FAIL" and rows["P02"]["speed"] == pytest.approx(9.0 / 6.8, abs=0.001)
    assert rows["P02"]["overflow_s"] == pytest.approx(9.0 / 1.25 - 6.8, abs=0.01)
    assert rows["P02"]["shorten_pct"] == 6            # cut ~6% to fit at 1.25x
    assert rows["P03"]["reasons"][0] == "no dubbed audio"
    assert report["summary"]["missing_audio"] == ["P03"] and report["summary"]["failed"] == 2


def test_end_gives_the_last_paragraph_more_room(tmp_path):
    project = make_project(tmp_path, {1: 3.0, 2: 5.5, 3: 4.0})
    assert df.main(_args(project, "--no-trim")) == 1               # P03: 2.4 s narration, 4 s dub
    assert df.main(_args(project, "--no-trim", "--end", "15")) == 0


def test_unused_audio_is_listed(tmp_path, capsys):
    project = make_project(tmp_path, {1: 3.0, 2: 5.5, 3: 2.0, 9: 1.0})
    assert df.main(_args(project, "--no-trim")) == 0
    assert "Audio with no paragraph in the script: dub/es/voice/P09.wav" in capsys.readouterr().out


def test_wav_works_without_ffmpeg(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(df.shutil, "which", lambda name: None)
    project = make_project(tmp_path, {1: 3.0, 2: 5.5, 3: 2.0})
    assert df.main(_args(project)) == 0
    assert "Edge silence counted" in capsys.readouterr().out
    report = json.loads((project / "qa" / "dub-fit-es.json").read_text(encoding="utf-8"))
    assert report["trimmed_silence"] is False and report["paragraphs"][1]["duration_s"] == 5.5


@needs_ffmpeg
def test_edge_silence_is_trimmed_and_compressed_audio_is_read(tmp_path):
    project = make_project(tmp_path, {1: 3.0, 3: 2.0})
    # P02: a 7.4 s file with 1 s of silence on each side. Counted whole, it runs late and squeezes P03 past the
    # cap; with the edges trimmed, its 5.4 s of speech fits and P03 starts on time.
    write_wav(project / "dub" / "es" / "voice" / "P02.wav", 5.4, lead_s=1.0, trail_s=1.0)
    assert df.main(_args(project, "--no-trim")) == 1
    assert df.main(_args(project)) == 0
    report = json.loads((project / "qa" / "dub-fit-es.json").read_text(encoding="utf-8"))
    p02 = report["paragraphs"][1]
    assert p02["duration_s"] == pytest.approx(7.4, abs=0.05) and p02["speech_s"] == pytest.approx(5.4, abs=0.1)
    assert report["trimmed_silence"] is True
    mp3 = project / "dub" / "es" / "voice" / "P03.mp3"
    (project / "dub" / "es" / "voice" / "P03.wav").unlink()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                    str(mp3)], check=True)
    assert df.main(_args(project)) == 0


@needs_ffmpeg
def test_silent_audio_fails(tmp_path, capsys):
    project = make_project(tmp_path, {1: 3.0, 2: 5.0})
    write_wav(project / "dub" / "es" / "voice" / "P03.wav", 0.0, lead_s=2.0)
    assert df.main(_args(project, "--json")) == 1
    rows = json.loads(capsys.readouterr().out)["paragraphs"]
    assert rows[2]["reasons"] == ["audio is silent"]


# ------------------------------------------------------------------------------------------------ usage errors

def test_usage_errors(tmp_path):
    project = make_project(tmp_path, {1: 3.0, 2: 5.5, 3: 2.0})
    assert df.main(_args(project)[:-2]) == 2                                   # --lang is required
    assert df.main(_args(project, "--lang", "spanish!")) == 2
    assert df.main([str(project / "missing.md")] + _args(project)[1:]) == 2
    audio_as_transcript = [_args(project)[0], str(project / "dub" / "es" / "voice" / "P01.wav")] + _args(project)[2:]
    assert df.main(audio_as_transcript) == 2
    plain = project / "voice" / "narration.txt"
    plain.write_text("Acme sold widgets for ten years.", encoding="utf-8")
    assert df.main([_args(project)[0], str(plain)] + _args(project)[2:]) == 2   # no timings
    other = project / "voice" / "other.json"
    other.write_text(json.dumps(transcript_json([("Completely different words about the weather today.", 0.0)])),
                     encoding="utf-8")
    assert df.main([_args(project)[0], str(other)] + _args(project)[2:]) == 2   # not this script
    (project / "script.md").write_text("Acme sold widgets.\n", encoding="utf-8")
    assert df.main(_args(project)) == 2                                         # no paragraph ids
    (project / "script.md").write_text(SOURCE, encoding="utf-8")
    write_wav(project / "dub" / "es" / "voice" / "intro.wav", 0.5)
    assert df.main(_args(project)) == 2                                         # file without an id
