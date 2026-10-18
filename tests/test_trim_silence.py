"""Tests for skills/check-voiceover/scripts/trim_silence.py.

Planning tests are pure and run everywhere; the end-to-end tests build tiny tone-and-silence files with ffmpeg's
lavfi sources and skip when ffmpeg/ffprobe are not installed.
"""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "check-voiceover" / "scripts" / "trim_silence.py"


def _load():
    spec = importlib.util.spec_from_file_location("trim_silence", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ts = _load()
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


# ------------------------------------------------------------------------------------------- pure planning

def test_parse_silences_closes_a_trailing_pause_at_the_end():
    stderr = ("[silencedetect @ 0x1] silence_start: -0.002\n"
              "[silencedetect @ 0x1] silence_end: 0.4 | silence_duration: 0.402\n"
              "[silencedetect @ 0x1] silence_start: 2.5\n"
              "[silencedetect @ 0x1] silence_end: 3.7 | silence_duration: 1.2\n"
              "[silencedetect @ 0x1] silence_start: 5.1\n")
    assert ts.parse_silences(stderr, 6.0) == [(0.0, 0.4), (2.5, 3.7), (5.1, 6.0)]


def test_hard_ceiling_cuts_the_middle_and_leaves_short_pauses_alone():
    cuts = ts.plan_cuts([(1.0, 2.2), (4.0, 4.3), (6.0, 6.36)], ceiling=0.4)
    assert cuts == [(1.2, 2.0)]                     # 0.2 s kept on each side of the cut
    assert ts.new_length(1.2, 0.4) == 0.4 and ts.new_length(0.3, 0.4) == 0.3


def test_ratio_keeps_long_pauses_longer_than_short_ones():
    short, long_ = ts.new_length(0.5, 0.7, knee=0.2, ratio=0.35), ts.new_length(1.0, 0.7, knee=0.2, ratio=0.35)
    assert short == pytest.approx(0.305) and long_ == pytest.approx(0.48)
    assert ts.new_length(3.0, 0.7, knee=0.2, ratio=0.35) == 0.7      # the ceiling still caps
    assert ts.new_length(0.15, 0.7, knee=0.2, ratio=0.35) == 0.15    # under the knee: untouched


def test_frame_ranges_only_drop_whole_frames_inside_the_cut():
    # 1000 Hz, 10-sample frames: frame k covers [k/100, (k+1)/100) s.
    assert ts.frame_ranges([(0.105, 0.2)], 1000, frame=10) == [(11, 19)]
    assert ts.frame_ranges([(0.101, 0.109)], 1000, frame=10) == []
    assert ts.frame_ranges([(1.105, 1.2)], 1000, offset=1.0, frame=10) == [(11, 19)]


def test_filter_graph_splits_long_lists_over_several_selects():
    ranges = [(k * 100, k * 100 + 10) for k in range(ts.SELECT_TERMS_MAX + 5)]
    graph = ts.filter_graph(ranges, 48000)
    assert graph.startswith("asetnsamples=n=64:p=0,") and graph.endswith(",asetpts=N/SR/TB")
    assert graph.count("aselect=") == 2 and graph.count("between(t,") == len(ranges)


def test_pause_stats():
    stats = ts.pause_stats([(0.0, 0.5), (2.0, 3.0), (5.0, 5.4)], 10.0)
    assert stats == {"count": 3, "total": 1.9, "share": 0.19, "median": 0.5, "p90": 0.9, "max": 1.0}


# ------------------------------------------------------------------------------------------- end to end (ffmpeg)

def make_voice(path, layout, rate=24000, codec="pcm_s16le"):
    """layout: [("tone", seconds) | ("gap", seconds)] -> one mono file of tones and digital silence."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for kind, seconds in layout:
        source = (f"sine=frequency=440:sample_rate={rate}:duration={seconds}" if kind == "tone"
                  else f"anullsrc=r={rate}:cl=mono:d={seconds}")
        cmd += ["-f", "lavfi", "-i", source]
    inputs = "".join(f"[{k}:a]" for k in range(len(layout)))
    cmd += ["-filter_complex", f"{inputs}concat=n={len(layout)}:v=0:a=1", "-c:a", codec, str(path)]
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def duration_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], check=True, capture_output=True, text=True).stdout
    return float(out.strip())


LAYOUT = [("tone", 1.0), ("gap", 1.2), ("tone", 1.0), ("gap", 0.2), ("tone", 0.5), ("gap", 0.8), ("tone", 0.5)]


@pytest.fixture
def voice(tmp_path):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    return make_voice(tmp_path / "voice" / "narration take.wav", LAYOUT)


def run_json(capsys, *args):
    code = ts.main([str(a) for a in args] + ["--json"])
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out.strip() else None), captured.err


@needs_ffmpeg
def test_measure_reports_and_writes_nothing(voice, capsys):
    code, result, _ = run_json(capsys, voice, "--measure")
    assert code == 0 and result["measure_only"] is True and result["output"] is None
    assert result["pauses"]["count"] == 2                      # the 0.2 s breath is under the 0.30 s floor
    assert result["pauses"]["max"] == pytest.approx(1.2, abs=0.02)
    assert result["saved_seconds"] == pytest.approx((1.2 - 0.35) + (0.8 - 0.35), abs=0.01)
    assert result["cut_pauses"] == 2
    assert sorted(p.name for p in voice.parent.iterdir()) == ["narration take.wav"]


@needs_ffmpeg
def test_trim_caps_pauses_and_keeps_every_tone_whole(voice, capsys):
    code, result, _ = run_json(capsys, voice, "--ceiling", "0.4")
    out = voice.with_name("narration take-trimmed.wav")
    assert code == 0 and result["output"] == out.name and out.is_file()
    assert duration_of(out) == pytest.approx(5.2 - 0.8 - 0.4, abs=0.01)
    assert result["output_duration"] == pytest.approx(result["new_duration"], abs=0.01)
    _, after, _ = run_json(capsys, out, "--measure", "--ceiling", "0.4")
    assert after["pauses"]["count"] == 2 and after["pauses"]["max"] == pytest.approx(0.4, abs=0.01)
    assert after["cut_pauses"] == 0
    # speech is never stretched: tone time is unchanged, only silence went away
    speech_before = 5.2 - 1.2 - 0.2 - 0.8
    speech_after = after["duration"] - after["pauses"]["total"] - 0.2
    assert speech_after == pytest.approx(speech_before, abs=0.02)


@needs_ffmpeg
def test_ratio_and_named_output_in_another_format(voice, tmp_path, capsys):
    out = tmp_path / "out" / "tight.flac"
    code, result, _ = run_json(capsys, voice, out, "--ceiling", "0.7", "--ratio", "0.35")
    assert code == 0 and out.is_file()
    expected_keep = [0.2 + (1.2 - 0.2) * 0.35, 0.2 + (0.8 - 0.2) * 0.35]   # 0.55 and 0.41 s
    assert result["saved_seconds"] == pytest.approx((1.2 - expected_keep[0]) + (0.8 - expected_keep[1]), abs=0.01)


@needs_ffmpeg
def test_never_overwrites(voice, capsys):
    target = voice.with_name("taken.wav")
    target.write_bytes(b"keep me")
    code, _, err = run_json(capsys, voice, target)
    assert code == 2 and "never overwrites" in err and target.read_bytes() == b"keep me"
    assert run_json(capsys, voice, voice)[0] == 2


@needs_ffmpeg
def test_nothing_to_trim_writes_nothing(tmp_path, capsys):
    tight = make_voice(tmp_path / "tight.wav", [("tone", 1.0), ("gap", 0.3), ("tone", 1.0)])
    code = ts.main([str(tight)])
    assert code == 0 and "Nothing to trim" in capsys.readouterr().out
    assert not (tmp_path / "tight-trimmed.wav").exists()


def test_usage_errors(tmp_path, capsys, monkeypatch):
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"RIFF")
    assert ts.main([str(audio), "--ceiling", "0.2"]) == 2
    assert "clip its tail" in capsys.readouterr().err
    assert ts.main([str(audio), "--ratio", "1.5"]) == 2
    monkeypatch.setattr(ts.shutil, "which", lambda name: None)
    assert ts.main([str(audio)]) == 2
    assert "ffmpeg and ffprobe not found" in capsys.readouterr().err


@needs_ffmpeg
def test_missing_or_unreadable_input(tmp_path, capsys):
    assert ts.main([str(tmp_path / "none.wav")]) == 2
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"not audio at all")
    assert ts.main([str(bad), "--measure"]) == 2
    assert "trim_silence: error" in capsys.readouterr().err
