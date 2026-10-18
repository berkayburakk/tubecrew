"""Tests for skills/check-render/scripts/render_check.py.

Pure parser and threshold tests run everywhere; the end-to-end tests build tiny synthetic clips with ffmpeg's
lavfi sources and skip when ffmpeg/ffprobe are not installed.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "check-render" / "scripts" / "render_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("render_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rc = _load()
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


# ------------------------------------------------------------------------------------------- canned ffmpeg output

BLACK_STDERR = """
[Parsed_blackdetect_2 @ 000001ebc2b74640] black_start:4 black_end:6.2 black_duration:2.2
[Parsed_blackdetect_2 @ 000001ebc2b74640] black_start:58.5 black_end:60 black_duration:1.5
"""

FREEZE_STDERR = """
[Parsed_freezedetect_3 @ 0x1] lavfi.freezedetect.freeze_start: 0
[Parsed_freezedetect_3 @ 0x1] lavfi.freezedetect.freeze_duration: 5
[Parsed_freezedetect_3 @ 0x1] lavfi.freezedetect.freeze_end: 5
[Parsed_freezedetect_3 @ 0x1] lavfi.freezedetect.freeze_start: 50.5
"""

SILENCE_STDERR = """
[Parsed_silencedetect_0 @ 0x2] silence_start: 10.5
[Parsed_silencedetect_0 @ 0x2] silence_end: 12.8 | silence_duration: 2.3
[Parsed_silencedetect_0 @ 0x2] silence_start: 57.014082
"""

EBUR128_STDERR = """
[Parsed_ebur128_1 @ 000001ebc2610e40] t: 9.9 TARGET:-23 LUFS M: -14.8 S: -14.8 I: -14.9 LUFS LRA: 0.0 LU
[Parsed_ebur128_1 @ 000001ebc2610e40] Summary:

  Integrated loudness:
    I:         -14.2 LUFS
    Threshold: -24.7 LUFS

  Loudness range:
    LRA:         3.1 LU
    Threshold: -34.8 LUFS
    LRA low:   -16.8 LUFS
    LRA high:  -13.7 LUFS

  True peak:
    Peak:       -0.4 dBFS
"""

LOUDNORM_STDERR = """
[Parsed_loudnorm_0 @ 0x3]
{
	"input_i" : "-18.52",
	"input_tp" : "-3.10",
	"input_lra" : "5.20",
	"input_thresh" : "-28.90",
	"output_i" : "-24.00",
	"normalization_type" : "dynamic",
	"target_offset" : "0.00"
}
"""


def test_parse_blackdetect():
    assert rc.parse_blackdetect(BLACK_STDERR) == [[4.0, 6.2], [58.5, 60.0]]


def test_parse_freezedetect_closes_open_freeze_at_duration():
    assert rc.parse_freezedetect(FREEZE_STDERR, 60.0) == [[0.0, 5.0], [50.5, 60.0]]


def test_parse_silencedetect_closes_open_silence_at_duration():
    assert rc.parse_silencedetect(SILENCE_STDERR, 60.0) == [[10.5, 12.8], [57.014, 60.0]]


def test_parse_loudness_ebur128_reads_summary_not_running_lines():
    loud = rc.parse_loudness(EBUR128_STDERR)
    assert loud == {"lufs": -14.2, "true_peak": -0.4, "lra": 3.1}


def test_parse_loudness_loudnorm_json():
    loud = rc.parse_loudness(LOUDNORM_STDERR)
    assert loud["lufs"] == pytest.approx(-18.52)
    assert loud["true_peak"] == pytest.approx(-3.10)


def test_parse_loudness_missing_and_silent():
    assert rc.parse_loudness("no loudness here") is None
    silent = rc.parse_loudness("Summary:\n  Integrated loudness:\n    I:         -inf LUFS\n")
    assert silent["lufs"] == float("-inf")


def test_parse_ffprobe_streams_and_rate():
    text = json.dumps({
        "format": {"duration": "10.02"},
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30000/1001",
             "duration": "10.01"},
            {"codec_type": "audio", "duration": "9.5"},
        ],
    })
    info = rc.parse_ffprobe(text)
    assert info["has_video"] and info["has_audio"]
    assert info["fps"] == pytest.approx(29.97, abs=0.01)
    assert info["audio_duration"] == 9.5
    assert info["width"] == 1920


def test_parse_ffprobe_reads_matroska_duration_tags():
    # MKV/WebM carry per-stream length only in a DURATION tag; without it a short audio track looked complete.
    text = json.dumps({
        "format": {"duration": "10.000000"},
        "streams": [
            {"codec_type": "video", "width": 320, "height": 180, "avg_frame_rate": "25/1",
             "tags": {"DURATION": "00:00:10.000000000"}},
            {"codec_type": "audio", "tags": {"DURATION-eng": "00:00:07.008000000"}},
        ],
    })
    info = rc.parse_ffprobe(text)
    assert info["video_duration"] == pytest.approx(10.0)
    assert info["audio_duration"] == pytest.approx(7.008)
    assert statuses(rc.evaluate(measured(**info)), "audio_length") == ["FAIL"]


def test_parse_srt_accepts_webvtt_timestamps_without_hours():
    # Whisper writes WebVTT as mm:ss.ttt; these used to parse as zero cues, so the subtitle gate passed silently.
    cues = rc.parse_srt("WEBVTT\n\n00:01.000 --> 00:04.000 line:0\nHello\n\n01:09.500 --> 01:15.000\nLater\n")
    assert [(c["start"], c["end"]) for c in cues] == [(1.0, 4.0), (69.5, 75.0)]
    assert cues[0]["lines"] == ["Hello"]


def test_read_subtitles_decodes_utf16_with_bom(tmp_path):
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n", encoding="utf-16")
    cues = rc.parse_srt(rc.read_subtitles(srt))
    assert len(cues) == 1 and cues[0]["lines"] == ["Hello"]


def test_parse_srt_tolerates_bom_crlf_tags_and_missing_numbers():
    text = ("\ufeff1\r\n00:00:01,000 --> 00:00:02,500\r\n<i>Hello</i> there\r\n\r\n"
            "00:00:03.000 --> 00:00:04,000\r\nNo index\r\n")
    cues = rc.parse_srt(text)
    assert len(cues) == 2
    assert cues[0] == {"index": 1, "start": 1.0, "end": 2.5, "lines": ["Hello there"]}
    assert cues[1]["start"] == 3.0 and cues[1]["lines"] == ["No index"]


def test_fmt_time_and_project_detection(tmp_path):
    assert rc.fmt_time(0) == "00:00.0"
    assert rc.fmt_time(65.3) == "01:05.3"
    assert rc.fmt_time(3599.97) == "60:00.0"
    (tmp_path / "render").mkdir()
    assert rc.find_project(tmp_path / "render" / "final.mp4") == tmp_path.resolve()
    assert rc.find_project(tmp_path / "final.mp4") == tmp_path.resolve()


def test_decode_command_full_and_fast():
    full = " ".join(rc.build_decode_cmd(Path("v.mp4"), True, True))
    assert "fps=5,scale=320:-2" in full and "blackdetect=d=0.3:pix_th=0.10" in full
    assert "freezedetect=n=0.003:d=2" in full and "silencedetect=noise=-50dB:d=1.5" in full
    assert "ebur128=peak=true" in full
    fast = " ".join(rc.build_decode_cmd(Path("v.mp4"), True, False, fast=True))
    assert "fps=2,scale=160:-2" in fast and "-an" in fast


# ------------------------------------------------------------------------------------------- evaluate thresholds

def measured(**overrides):
    base = {"duration": 120.0, "video_duration": 120.0, "audio_duration": 120.0, "has_video": True,
            "has_audio": True, "width": 1920, "height": 1080, "fps": 30.0, "lufs": -14.0, "true_peak": -1.5,
            "black": [], "freezes": [], "silences": []}
    base.update(overrides)
    return base


def statuses(checks, name):
    return [c["status"] for c in checks if c["name"] == name]


def test_clean_measurements_pass():
    checks = rc.evaluate(measured())
    assert rc.verdict_of(checks) == "PASS"
    assert all(c["status"] == "PASS" for c in checks)


def test_missing_streams_fail():
    assert statuses(rc.evaluate(measured(has_audio=False, audio_duration=None)), "audio_stream") == ["FAIL"]
    checks = rc.evaluate(measured(has_video=False, video_duration=None))
    assert statuses(checks, "video_stream") == ["FAIL"]
    assert statuses(checks, "black") == [] and statuses(checks, "freeze") == []


def test_audio_shorter_than_video():
    assert statuses(rc.evaluate(measured(audio_duration=119.0)), "audio_length") == ["FAIL"]
    assert statuses(rc.evaluate(measured(audio_duration=119.6)), "audio_length") == ["PASS"]


def test_trailing_silence_and_gaps():
    assert statuses(rc.evaluate(measured(silences=[[116.5, 120.0]])), "trailing_silence") == ["FAIL"]
    assert statuses(rc.evaluate(measured(silences=[[117.5, 120.0]])), "trailing_silence") == ["WARN"]
    checks = rc.evaluate(measured(silences=[[30.0, 32.2], [50.0, 51.6]]))
    assert statuses(checks, "silence_gap") == ["WARN"]
    assert rc.verdict_of(checks) == "PASS"


@pytest.mark.parametrize("lufs,expected", [(-14.0, "PASS"), (-16.0, "PASS"), (-17.0, "WARN"), (-11.0, "WARN"),
                                           (-21.0, "FAIL"), (-8.0, "FAIL"), (float("-inf"), "FAIL")])
def test_loudness_bands(lufs, expected):
    assert statuses(rc.evaluate(measured(lufs=lufs)), "loudness") == [expected]


def test_true_peak_warns_only():
    checks = rc.evaluate(measured(true_peak=-0.4))
    assert statuses(checks, "true_peak") == ["WARN"]
    assert rc.verdict_of(checks) == "PASS"


def test_black_segments():
    assert statuses(rc.evaluate(measured(black=[[10.0, 11.2]])), "black") == ["FAIL"]
    assert statuses(rc.evaluate(measured(black=[[0.0, 1.0]])), "black") == ["FAIL"]
    assert statuses(rc.evaluate(measured(black=[[10.0, 10.5]])), "black") == ["WARN"]
    assert statuses(rc.evaluate(measured(black=[[119.2, 120.0]])), "black") == ["INFO"]
    assert statuses(rc.evaluate(measured(black=[[117.0, 120.0]])), "black") == ["FAIL"]


def test_freeze_zones():
    hook = rc.evaluate(measured(freezes=[[0.0, 5.0]]))
    assert "FAIL" in statuses(hook, "freeze")
    assert statuses(rc.evaluate(measured(freezes=[[10.0, 12.5]])), "freeze") == ["PASS"]
    assert statuses(rc.evaluate(measured(freezes=[[60.0, 70.0]])), "freeze") == ["WARN"]
    assert statuses(rc.evaluate(measured(freezes=[[60.0, 66.0]])), "freeze") == ["PASS"]
    end = statuses(rc.evaluate(measured(freezes=[[105.0, 120.0]])), "freeze")
    assert end == ["PASS", "INFO"]


def test_hook_seconds_option():
    m = measured(freezes=[[50.0, 54.0]])
    assert "FAIL" in statuses(rc.evaluate(m, hook_seconds=60), "freeze")
    assert "FAIL" not in statuses(rc.evaluate(m, hook_seconds=45), "freeze")


def cue(index, start, end, *lines):
    return {"index": index, "start": start, "end": end, "lines": list(lines) or ["text"]}


def test_subtitle_checks():
    clean = rc.evaluate(measured(), [cue(1, 1, 3), cue(2, 3, 5)])
    assert statuses(clean, "subtitle_end") == ["PASS"]
    assert statuses(clean, "subtitle_overlap") == ["PASS"]
    assert statuses(clean, "subtitle_lines") == ["PASS"]
    late = rc.evaluate(measured(), [cue(1, 118, 120.4), cue(2, 119, 121)])
    assert statuses(late, "subtitle_end") == ["FAIL"]
    overlap = rc.evaluate(measured(), [cue(1, 1, 3.5), cue(2, 3, 5)])
    assert statuses(overlap, "subtitle_overlap") == ["WARN"]
    long_line = rc.evaluate(measured(), [cue(1, 1, 3, "x" * 43)])
    assert statuses(long_line, "subtitle_lines") == ["WARN"]
    three = rc.evaluate(measured(), [cue(1, 1, 3, "a", "b", "c")])
    assert statuses(three, "subtitle_lines") == ["WARN"]
    assert rc.verdict_of(three) == "PASS"


# ------------------------------------------------------------------------------------------- end to end (ffmpeg)

def make_clip(path, seconds=10, vf=None, audio_seconds=None):
    """A small moving test pattern with a 440 Hz tone set to about -15 LUFS."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=10:d={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={audio_seconds or seconds}"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-af", "volume=7dB", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "96k", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def run_main(capsys, *args):
    code = rc.main([str(a) for a in args])
    out = capsys.readouterr().out
    return code, out


def report_of(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def moving_clip(tmp_path_factory):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    project = tmp_path_factory.mktemp("proj")
    return make_clip(project / "render" / "final.mp4")


@needs_ffmpeg
def test_e2e_moving_clip_passes_and_binds_to_bytes(moving_clip, capsys):
    code, out = run_main(capsys, moving_clip)
    project = moving_clip.parent.parent
    report_path = project / "qa" / "render-check.json"
    report = report_of(report_path)
    assert statuses(report["checks"], "black") == ["PASS"]
    assert "FAIL" not in statuses(report["checks"], "freeze")
    assert report["verdict"] == "PASS" and code == 0
    assert report["video"] == "render/final.mp4"
    assert report["sha256"] == hashlib.sha256(moving_clip.read_bytes()).hexdigest()
    assert report["size"] == moving_clip.stat().st_size
    assert report["measured"]["width"] == 160
    assert str(project) not in report_path.read_text(encoding="utf-8")
    assert "VERDICT: PASS" in out and "qa/render-check.json" in out


@needs_ffmpeg
def test_e2e_black_gap_fails(tmp_path, capsys):
    clip = make_clip(tmp_path / "black.mp4",
                     vf="drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,4,6)'")
    code, _ = run_main(capsys, clip)
    report = report_of(tmp_path / "qa" / "render-check.json")
    black = [c for c in report["checks"] if c["name"] == "black"]
    assert [c["status"] for c in black] == ["FAIL"]
    assert 3.5 <= black[0]["start"] <= 4.5
    assert code == 1 and report["verdict"] == "FAIL"


@needs_ffmpeg
def test_e2e_black_gap_fails_in_fast_mode(tmp_path, capsys):
    clip = make_clip(tmp_path / "black.mp4",
                     vf="drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,4,6)'")
    code, _ = run_main(capsys, clip, "--fast", "--out", tmp_path / "fast.json")
    report = report_of(tmp_path / "fast.json")
    assert report["measured"]["fast"] is True
    assert statuses(report["checks"], "black") == ["FAIL"] and code == 1


@needs_ffmpeg
def test_e2e_still_opening_fails_hook(tmp_path, capsys):
    clip = make_clip(tmp_path / "still.mp4", seconds=12,
                     vf="drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='lt(t,5)'")
    code, _ = run_main(capsys, clip)
    report = report_of(tmp_path / "qa" / "render-check.json")
    freeze = [c for c in report["checks"] if c["name"] == "freeze" and c["status"] == "FAIL"]
    assert len(freeze) == 1 and freeze[0]["start"] < 0.5
    assert statuses(report["checks"], "black") == ["PASS"]
    assert code == 1


@needs_ffmpeg
def test_e2e_short_audio_fails(tmp_path, capsys):
    clip = make_clip(tmp_path / "short.mp4", seconds=10, audio_seconds=8)
    code, _ = run_main(capsys, clip)
    report = report_of(tmp_path / "qa" / "render-check.json")
    assert statuses(report["checks"], "audio_length") == ["FAIL"]
    assert code == 1


@needs_ffmpeg
def test_e2e_short_audio_in_matroska_fails(tmp_path, capsys):
    clip = make_clip(tmp_path / "short.mkv", seconds=10, audio_seconds=7)
    code, _ = run_main(capsys, clip)
    report = report_of(tmp_path / "qa" / "render-check.json")
    assert statuses(report["checks"], "audio_length") == ["FAIL"]
    assert code == 1


@needs_ffmpeg
def test_e2e_subtitle_past_end_fails_and_json_output(moving_clip, tmp_path, capsys):
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n\n2\n00:00:09,000 --> 00:00:15,000\nToo late\n",
                   encoding="utf-8")
    code, out = run_main(capsys, moving_clip, "--srt", srt, "--out", tmp_path / "sub.json", "--json")
    printed = json.loads(out)
    assert statuses(printed["checks"], "subtitle_end") == ["FAIL"]
    assert printed["verdict"] == "FAIL" and code == 1
    assert printed["measured"]["srt_cues"] == 2
    assert not Path(printed["measured"]["srt"]).is_absolute()


def test_missing_video_is_usage_error(tmp_path, capsys):
    code = rc.main([str(tmp_path / "nope.mp4")])
    assert code == 2
    assert "not found" in capsys.readouterr().err


def test_rel_path_outside_project_keeps_only_the_file_name(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "elsewhere" / "captions.srt"
    assert rc.rel_path(outside, project) == "captions.srt"
    assert rc.rel_path(project / "voice" / "captions.srt", project) == "voice/captions.srt"


def test_subtitle_file_without_cues_fails():
    checks = rc._check_subtitles({"duration": 60.0}, [])
    assert [(c["name"], c["status"]) for c in checks] == [("subtitle_end", "FAIL")]
