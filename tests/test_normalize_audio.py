"""Tests for skills/check-render/scripts/normalize_audio.py.

Pure tests (filter strings, report parsing, the gate) run everywhere; the end-to-end tests build short synthetic
clips with ffmpeg's lavfi sources and skip when ffmpeg/ffprobe are not installed.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "check-render" / "scripts" / "normalize_audio.py"


def _load():
    spec = importlib.util.spec_from_file_location("normalize_audio", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


na = _load()
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")

APPLY_STDERR = """
Stream mapping:
  Stream #0:0 -> #0:0 (copy)
[Parsed_loudnorm_0 @ 0000020c5d7f2c80]
{
	"input_i" : "-29.62",
	"input_tp" : "0.21",
	"input_lra" : "0.00",
	"input_thresh" : "-39.62",
	"output_i" : "-14.48",
	"output_tp" : "-1.50",
	"output_lra" : "0.00",
	"output_thresh" : "-24.48",
	"normalization_type" : "dynamic",
	"target_offset" : "-0.02"
}
[out#0/wav @ 0000020c5d7e9a40] video:0KiB audio:1406KiB subtitle:0KiB other streams:0KiB
"""

MEASURED = {"input_i": "-19.80", "input_tp": "-3.10", "input_lra": "6.20", "input_thresh": "-30.10",
            "target_offset": "0.15"}


# ------------------------------------------------------------------------------------------- pure

def test_parse_loudnorm_json_finds_the_block_among_other_log_lines():
    block = na.parse_loudnorm_json(APPLY_STDERR)
    assert block["normalization_type"] == "dynamic" and block["input_i"] == "-29.62"
    assert na.parse_loudnorm_json("no json here") is None
    assert na.parse_loudnorm_json('{"input_i" : broken') is None


def test_loudnorm_filter_measure_and_apply():
    assert na.loudnorm_filter(-14, -1.5, 11) == "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json"
    apply = na.loudnorm_filter(-14, -1.5, 11, MEASURED)
    assert "measured_I=-19.80:measured_TP=-3.10:measured_LRA=6.20:measured_thresh=-30.10" in apply
    assert "offset=0.15:linear=true" in apply and apply.endswith(":print_format=json")


def test_default_output_never_equals_the_input():
    assert na.default_output(Path("render/raw.mp4")) == Path("render/raw-normalized.mp4")


def test_apply_command_copies_video_only_into_video_containers():
    mp4 = na.build_apply_cmd(Path("in.mp4"), Path("out.mp4"), 1, 0, "loudnorm=x")
    assert mp4[mp4.index("-map") + 1] == "0:0" and "copy" in mp4 and "+faststart" in mp4
    assert ["-map", "0:1", "-af", "loudnorm=x", "-c:a", "aac", "-b:a", "320k"] == mp4[mp4.index("0:1") - 1:][:8]
    wav = na.build_apply_cmd(Path("in.mp4"), Path("out.wav"), 1, 0, "loudnorm=x")
    assert "-vn" in wav and "copy" not in wav and "pcm_s24le" in wav and "+faststart" not in wav
    assert "-ar" in wav and wav[wav.index("-ar") + 1] == "48000"


def test_gate():
    assert na.gate({"i": -14.2, "tp": -1.4}, -14, -1.0) == []
    assert len(na.gate({"i": -15.0, "tp": -1.4}, -14, -1.0)) == 1
    assert "true peak" in na.gate({"i": -14.0, "tp": -0.6}, -14, -1.0)[0]
    assert len(na.gate({"i": float("-inf"), "tp": float("-inf")}, -14, -1.0)) == 1


def test_mode_warning():
    assert na.mode_warning("linear") is None
    assert "dynamic mode" in na.mode_warning("dynamic")
    assert "did not report" in na.mode_warning(None)


def test_usage_errors_need_no_ffmpeg(tmp_path, capsys):
    assert na.main([str(tmp_path / "missing.mp4")]) == 2
    src = tmp_path / "mix.mp4"
    src.write_bytes(b"x")
    assert na.main([str(src), "--out", str(src)]) == 2                 # would overwrite the input
    assert "overwrite the input" in capsys.readouterr().err
    assert na.main([str(src), "--out", str(tmp_path / "mix.xyz")]) == 2
    assert na.main([str(src), "--max-tp", "1"]) == 2


# ------------------------------------------------------------------------------------------- end to end (ffmpeg)

def run_ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True,
                   capture_output=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return {s["codec_type"]: s["codec_name"] for s in json.loads(out)["streams"]}


@needs_ffmpeg
def test_e2e_quiet_video_reaches_the_target_with_the_video_copied(tmp_path, capsys):
    project = tmp_path / "proj"
    src = project / "render" / "raw.mp4"
    src.parent.mkdir(parents=True)
    # a tone with a slow tremolo: loudnorm refuses linear mode when the measured loudness range is exactly 0
    run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:d=8", "-f", "lavfi",
               "-i", "aevalsrc='0.03*(1+0.6*sin(2*PI*0.25*t))*sin(2*PI*440*t)':s=48000:d=8",
               "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "128k", src)
    before = hashlib.sha256(src.read_bytes()).hexdigest()
    code = na.main([str(src), "--json"])
    report = json.loads(capsys.readouterr().out)
    out = project / "render" / "raw-normalized.mp4"
    assert code == 0 and report["verdict"] == "PASS"
    assert abs(report["after"]["i"] - -14.0) <= 0.5 and report["after"]["tp"] <= -1.0
    assert report["before"]["i"] < -25
    assert report["normalization"] == "linear" and report["warnings"] == []
    assert report["video"] == "copied" and probe(out) == {"video": "mpeg4", "audio": "aac"}
    assert hashlib.sha256(src.read_bytes()).hexdigest() == before            # the input is untouched
    assert report["input"] == "render/raw.mp4" and report["output"] == "render/raw-normalized.mp4"
    saved = json.loads((project / "qa" / "normalize-audio.json").read_text(encoding="utf-8"))
    assert saved["output_sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert not list((project / "render").glob(".*partial*"))


@needs_ffmpeg
def test_e2e_dynamic_fallback_is_reported(tmp_path, capsys):
    src = tmp_path / "peaky.wav"
    # a quiet tone with loud clicks: a linear gain to -14 LUFS would push the clicks far over the ceiling
    run_ffmpeg("-f", "lavfi", "-i", "aevalsrc='0.02*sin(2*PI*440*t)+if(lt(mod(t,1),0.002),0.9,0)':s=48000:d=6",
               src)
    code = na.main([str(src), "--out", str(tmp_path / "out.wav"), "--report", str(tmp_path / "r.json")])
    printed = capsys.readouterr().out
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["normalization"] == "dynamic"
    assert report["warnings"] and "dynamic mode" in printed
    assert report["video"] == "none" and probe(tmp_path / "out.wav") == {"audio": "pcm_s24le"}
    assert code in (0, 1) and (code == 1) == bool(report["problems"])


@needs_ffmpeg
def test_e2e_silent_audio_fails_and_missing_audio_is_a_usage_error(tmp_path, capsys):
    silent = tmp_path / "silent.wav"
    run_ffmpeg("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "3", silent)
    assert na.main([str(silent)]) == 1
    assert "silent" in capsys.readouterr().err
    assert not (tmp_path / "silent-normalized.wav").exists()
    mute = tmp_path / "mute.mp4"
    run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:d=2", "-c:v", "mpeg4", mute)
    assert na.main([str(mute)]) == 2
    assert "no audio stream" in capsys.readouterr().err
