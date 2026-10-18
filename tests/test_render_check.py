"""Tests for skills/check-render/scripts/render_check.py.

Pure parser and threshold tests run everywhere; the end-to-end tests build tiny synthetic clips with ffmpeg's
lavfi sources and skip when ffmpeg/ffprobe are not installed.
"""
import hashlib
import importlib.util
import json
import os
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


# Two named detectors share one log. Recent ffmpeg prints "silencedetect@name", older builds only "name".
TWO_DETECTORS_STDERR = """
[silencedetect@tc_dead @ 000001a] silence_start: 3.9
[silencedetect@tc_total @ 000001b] silence_start: 4.02
[silencedetect@tc_total @ 000001b] silence_end: 5.5 | silence_duration: 1.48
[silencedetect@tc_dead @ 000001a] silence_end: 5.6 | silence_duration: 1.7
[tc_dead @ 0x5] silence_start: 58.0
[tc_total @ 0x6] silence_start: 58.9
"""


def test_parse_silencedetect_separates_the_two_named_detectors():
    total = rc.parse_silencedetect(TWO_DETECTORS_STDERR, 60.0, tag=rc.SILENCE_TAG_TOTAL)
    dead = rc.parse_silencedetect(TWO_DETECTORS_STDERR, 60.0, tag=rc.SILENCE_TAG_DEAD)
    assert total == [[4.02, 5.5], [58.9, 60.0]]
    assert dead == [[3.9, 5.6], [58.0, 60.0]]


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
    assert "freezedetect=n=0.003:d=2" in full
    assert "silencedetect@tc_total=noise=-60dB:d=1," in full          # total silence
    assert "silencedetect@tc_dead=noise=-50dB:d=1.5," in full         # dead air; shorter pauses are speech rhythm
    assert "ebur128=peak=true" in full
    fast = " ".join(rc.build_decode_cmd(Path("v.mp4"), True, False, fast=True))
    assert "fps=2,scale=160:-2" in fast and "-an" in fast
    music = " ".join(rc.build_decode_cmd(Path("v.mp4"), True, True, music=True))
    assert "silencedetect@tc_dead=noise=-50dB:d=0.5," in music        # music-only: half a second is a hole


def test_decode_command_motion_branch_goes_to_stdout_at_the_calibrated_sampling():
    cmd = rc.build_decode_cmd(Path("v.mp4"), True, True, motion=True, video_index=1, audio_index=0)
    joined = " ".join(cmd)
    assert "[0:1]split=2" in joined and "[0:0]silencedetect" in joined
    assert "fps=4,scale=96:54,format=gray[mo]" in joined
    assert cmd[-7:] == ["-map", "[mo]", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    assert cmd.index("null") < cmd.index("[mo]")      # the null output comes first, the frames last


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


def test_total_silence_over_one_second_fails_in_the_body_and_at_the_end():
    body = rc.evaluate(measured(total_silences=[[30.0, 31.5]], silences=[[29.8, 31.7]]))
    fails = [c for c in body if c["name"] == "total_silence"]
    assert [c["status"] for c in fails] == ["FAIL"] and "in the body" in fails[0]["detail"]
    assert (fails[0]["start"], fails[0]["end"]) == (30.0, 31.5)
    assert statuses(body, "silence_gap") == ["INFO"]          # the same gap is not reported twice
    end = rc.evaluate(measured(total_silences=[[118.6, 120.0]], silences=[[118.4, 120.0]]))
    assert [c["detail"] for c in end if c["name"] == "total_silence" and "at the end" in c["detail"]]
    assert statuses(end, "trailing_silence") == ["INFO"] and rc.verdict_of(end) == "FAIL"
    tail = rc.evaluate(measured(total_silences=[[105.0, 106.2]]))
    assert "in the tail (last 25 s)" in [c for c in tail if c["name"] == "total_silence"][0]["detail"]


def test_total_silence_of_one_second_or_less_passes():
    checks = rc.evaluate(measured(total_silences=[[60.0, 61.0]]))
    assert statuses(checks, "total_silence") == ["PASS"] and rc.verdict_of(checks) == "PASS"


def test_dead_air_checks_still_run_where_total_silence_does_not_reach():
    # a 3.5 s near-silent tail with under a second of total silence inside: the -50 dB rule still fails it
    checks = rc.evaluate(measured(total_silences=[[119.2, 120.0]], silences=[[116.5, 120.0]]))
    assert statuses(checks, "total_silence") == ["PASS"]
    assert statuses(checks, "trailing_silence") == ["FAIL"]


def test_silence_summary_is_written_back():
    m = measured(total_silences=[[30.0, 31.5], [118.0, 120.0]], silences=[[29.9, 31.6], [117.9, 120.0]])
    rc.evaluate(m)
    summary = m["silence_summary"]
    assert summary["total_silence_db"] == -60 and summary["dead_air_db"] == -50
    assert summary["longest_total_silence_body_s"] == 1.5 and summary["longest_total_silence_tail_s"] == 2.0
    assert summary["music_only"] is False and "music_holes" not in summary


def test_short_video_keeps_a_body_for_the_labels():
    checks = rc.evaluate(measured(duration=10.0, video_duration=10.0, audio_duration=10.0,
                                  total_silences=[[4.0, 5.5]]))
    assert "in the body" in [c for c in checks if c["name"] == "total_silence"][0]["detail"]


def test_music_only_soundtrack_holes():
    def gap(silences, music=True):
        m = measured(music=music, silences=silences)
        return statuses(rc.evaluate(m), "silence_gap"), m["silence_summary"]

    status, summary = gap([[40.0, 40.7]])
    assert status == ["WARN"] and summary["music_only"] is True and summary["music_holes"] == [[40.0, 40.7]]
    assert gap([[40.0, 41.2]])[0] == ["FAIL"]
    assert gap([[40.0, 40.3]])[0] == ["PASS"]                   # too short to hear
    assert gap([[117.5, 118.4]])[0] == ["PASS"]                 # inside the final 3 s fade
    assert gap([[40.0, 40.7]], music=False)[0] == ["PASS"]      # speech rhythm without --music
    assert rc.verdict_of(rc.evaluate(measured(music=True, silences=[[40.0, 41.2]]))) == "FAIL"


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


def test_freeze_zones_live_profile_keeps_the_lenient_body_rule():
    def live(freezes):
        return statuses(rc.evaluate(measured(freezes=freezes), profile="live"), "freeze")
    assert "FAIL" in live([[0.0, 5.0]])
    assert live([[10.0, 12.5]]) == ["PASS"]
    assert live([[60.0, 70.0]]) == ["WARN"]
    assert live([[60.0, 66.0]]) == ["PASS"]
    assert live([[105.0, 120.0]]) == ["PASS", "INFO"]


def test_freeze_zones_animated_profile_fails_three_still_seconds_anywhere_but_the_end_screen():
    def animated(freezes):
        return statuses(rc.evaluate(measured(freezes=freezes)), "freeze")
    assert "FAIL" in animated([[0.0, 5.0]])
    assert animated([[10.0, 12.5]]) == ["WARN"]          # 2-3 s: borderline
    assert animated([[60.0, 63.2]]) == ["FAIL"]          # body still for 3 s
    assert animated([[60.0, 61.9]]) == ["PASS"]
    assert animated([[105.0, 120.0]]) == ["PASS", "INFO"]   # end screen
    assert animated([[98.0, 102.0]]) == ["WARN"]     # only the 2 s before the end screen count


def test_hook_seconds_option():
    m = measured(freezes=[[50.0, 54.0]])
    hook = [c for c in rc.evaluate(m, hook_seconds=60) if c["name"] == "freeze"]
    assert hook[0]["status"] == "FAIL" and "hook must move" in hook[0]["detail"]
    body = [c for c in rc.evaluate(m, hook_seconds=45) if c["name"] == "freeze"]
    assert body[0]["status"] == "FAIL" and "in the body" in body[0]["detail"]
    assert "FAIL" not in statuses(rc.evaluate(m, hook_seconds=45, profile="live"), "freeze")


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

def make_clip(path, seconds=10, vf=None, audio_seconds=None, af=None):
    """A small moving test pattern with a 440 Hz tone set to about -15 LUFS (`af` is appended to the tone)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=10:d={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={audio_seconds or seconds}"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-af", "volume=7dB" + (f",{af}" if af else ""), "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "96k",
            str(path)]
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
    motion = report["measured"]["motion"]
    assert motion["size"] == "96x54" and motion["fps"] == 4 and motion["frames"] >= 39
    assert motion["hook"]["slow_pct"] == 0 and motion["hook"]["frozen_pct"] == 0
    assert "FAIL" not in statuses(report["checks"], "hook_motion")
    assert report["measured"]["profile"] == "animated" and report["exemptions"] == []


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
def test_e2e_digital_silence_hole_in_the_body_fails(tmp_path, capsys):
    # 1.5 s of digital silence: under the 2 s dead-air warning, so only the -60 dB total-silence rule catches it
    clip = make_clip(tmp_path / "hole.mp4", seconds=12, af="volume=0:enable='between(t,4,5.5)'")
    code, out = run_main(capsys, clip)
    report = report_of(tmp_path / "qa" / "render-check.json")
    total = [c for c in report["checks"] if c["name"] == "total_silence"]
    assert [c["status"] for c in total] == ["FAIL"] and "in the body" in total[0]["detail"]
    assert 3.8 <= total[0]["start"] <= 4.2 and 1.3 <= total[0]["end"] - total[0]["start"] <= 1.7
    assert "FAIL" not in statuses(report["checks"], "silence_gap")
    assert 1.3 <= report["measured"]["silence_summary"]["longest_total_silence_body_s"] <= 1.7
    assert report["measured"]["total_silences"]
    assert report["verdict"] == "FAIL" and code == 1 and "total_silence" in out


@needs_ffmpeg
def test_e2e_music_only_hole_warns_only_with_music(tmp_path, capsys):
    clip = make_clip(tmp_path / "music.mp4", seconds=12, af="volume=0:enable='between(t,4,4.7)'")
    code, _ = run_main(capsys, clip, "--out", tmp_path / "speech.json")
    speech = report_of(tmp_path / "speech.json")
    assert statuses(speech["checks"], "silence_gap") == ["PASS"]        # a 0.7 s pause is speech rhythm
    assert statuses(speech["checks"], "total_silence") == ["PASS"]
    code, out = run_main(capsys, clip, "--music", "--out", tmp_path / "music.json")
    report = report_of(tmp_path / "music.json")
    gap = [c for c in report["checks"] if c["name"] == "silence_gap"]
    assert [c["status"] for c in gap] == ["WARN"] and 3.8 <= gap[0]["start"] <= 4.2
    assert report["measured"]["music"] is True
    assert len(report["measured"]["silence_summary"]["music_holes"]) == 1
    assert report["verdict"] == "PASS" and code == 0 and "music-only soundtrack" in out


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


# ------------------------------------------------------------------------------------------- motion band (pure)

def test_frame_diffs_mean_absolute_difference():
    size = rc.MOTION_W * rc.MOTION_H
    raw = bytes([10]) * size + bytes([10]) * size + bytes([40]) * size
    assert rc.frame_diffs(raw) == [0.0, 30.0]
    assert rc.frame_diffs(raw + b"\x00" * 7) == [0.0, 30.0]      # a partial trailing frame is ignored


def test_still_windows_need_three_seconds():
    d = [0.0] * 11 + [5.0] + [0.0] * 12      # 2.75 s still (too short), a change, then 3.0 s still
    assert rc.still_windows(d, 0.15) == [(3.0, 6.0)]


def test_motion_summary_definitions():
    # 40 diffs = 41 frames = 10.25 s: two events, 3.5 s frozen, then 3 s more that is slow but not frozen
    d = [30.0, 30.0] + [0.05] * 14 + [0.8] * 12 + [5.0] * 12
    s = rc.motion_summary(d)
    assert s["duration_s"] == pytest.approx(10.2, abs=0.06)
    assert s["events_per_min"] == pytest.approx(11.7)
    assert s["frozen_pct"] == pytest.approx(34.1)
    assert s["slow_pct"] == pytest.approx(63.4)
    assert s["median_diff"] == 0.8
    assert s["slow_windows"] == [[0.5, 7.0]]
    assert rc.motion_summary([]) is None


def test_motion_report_slices_the_hook():
    d = [5.0] * 39 + [0.05] * 40          # moving for the first 10 s, still for the next 10 s
    rep = rc.motion_report(d, hook_seconds=10)
    assert rep["frames"] == 80
    assert rep["hook"]["slow_pct"] == 0 and rep["hook"]["duration_s"] == 10.0
    assert rep["video"]["frozen_pct"] == pytest.approx(50.0)
    assert rc.motion_report(d, hook_seconds=0)["hook"] is None


def motion_measured(hook, video=None, **overrides):
    return measured(motion={"fps": 4, "size": "96x54", "frames": 400, "hook": hook, "video": video}, **overrides)


def summary(events=45.0, frozen=0.0, slow=0.0, windows=()):
    return {"duration_s": 45.0, "events_per_min": events, "median_diff": 4.0, "frozen_pct": frozen,
            "slow_pct": slow, "slow_windows": [list(w) for w in windows]}


def test_hook_motion_band():
    assert statuses(rc.evaluate(motion_measured(summary())), "hook_motion") == ["PASS"]
    slow = [c for c in rc.evaluate(motion_measured(summary(slow=8.9, windows=[(4.0, 8.0)])))
            if c["name"] == "hook_motion"]
    assert slow[0]["status"] == "FAIL" and (slow[0]["start"], slow[0]["end"]) == (4.0, 8.0)
    assert statuses(rc.evaluate(motion_measured(summary(frozen=3.0, slow=3.0))), "hook_motion") == ["FAIL"]
    # few events depends on the genre: a warning, not a failure
    assert statuses(rc.evaluate(motion_measured(summary(events=12.0))), "hook_motion") == ["WARN"]
    live = rc.evaluate(motion_measured(summary(slow=50.0)), profile="live")
    assert statuses(live, "hook_motion") == ["WARN"] and rc.verdict_of(live) == "PASS"


def test_hook_motion_not_measurable_warns():
    assert statuses(rc.evaluate(motion_measured(None)), "hook_motion") == ["WARN"]


def test_motion_band_is_configurable():
    calm = {"min_events": 10.0, "max_frozen": 5.0, "max_slow": 12.0}
    checks = rc.evaluate(motion_measured(summary(events=12.0, slow=8.9)), band=calm)
    assert statuses(checks, "hook_motion") == ["PASS"]
    assert rc.parse_band("20,2.2,8") == {"min_events": 20.0, "max_frozen": 2.2, "max_slow": 8.0}
    for bad in ("20,2.2", "a,b,c", "20,-1,8"):
        with pytest.raises(rc.UsageError):
            rc.parse_band(bad)


def test_whole_video_motion_warns_in_animated_and_informs_in_live():
    video = summary(events=12.0, frozen=10.0, slow=30.0, windows=[(60.0, 66.5)])
    animated = [c for c in rc.evaluate(motion_measured(summary(), video)) if c["name"] == "motion"]
    assert animated[0]["status"] == "WARN" and "01:00.0-01:06.5" in animated[0]["detail"]
    assert statuses(rc.evaluate(motion_measured(summary(), video), profile="live"), "motion") == ["INFO"]
    assert statuses(rc.evaluate(motion_measured(summary(), summary())), "motion") == ["INFO"]


# ------------------------------------------------------------------------------------------- subtitles vs words

def words_of(*items):
    return [{"w": w, "s": s, "e": e} for w, s, e in items]


SPOKEN = words_of(("The", 1.0, 1.2), ("company", 1.2, 1.7), ("made", 1.7, 2.0), ("money.", 2.0, 2.6),
                  ("Then", 3.0, 3.3), ("it", 3.3, 3.4), ("stopped.", 3.4, 4.0))


def test_parse_words_accepts_whisper_segments_word_lists_and_short_keys():
    whisper = {"segments": [{"start": 0, "end": 1, "text": " Hello there", "words": [
        {"word": " Hello", "start": 0.0, "end": 0.5}, {"word": " there", "start": 0.5, "end": 1.0}]}]}
    assert [w["w"] for w in rc.parse_words(whisper)] == ["Hello", "there"]
    unsorted = {"words": [{"text": "b", "start": 2, "end": 3}, {"text": "a", "start": 1, "end": 2}]}
    assert [w["w"] for w in rc.parse_words(unsorted)] == ["a", "b"]
    assert rc.parse_words([{"w": "x", "s": 1, "e": 2}]) == [{"w": "x", "s": 1.0, "e": 2.0}]
    # a segment without word timings is skipped, not read as one long word
    assert rc.parse_words({"segments": [{"start": 0, "end": 2, "text": "no word timings here"}]}) == []
    with pytest.raises(ValueError):
        rc.parse_words({"text": "plain"})


def test_load_words_applies_the_offset_and_rejects_files_without_word_timings(tmp_path):
    path = tmp_path / "w.json"
    path.write_text(json.dumps({"words": [{"word": "Hi", "start": 0.5, "end": 0.9}]}), encoding="utf-8")
    assert rc.load_words(path, offset=2.0) == [{"w": "Hi", "s": 2.5, "e": 2.9}]
    path.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "text": "two words"}]}), encoding="utf-8")
    with pytest.raises(rc.UsageError, match="word timings"):
        rc.load_words(path)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(rc.UsageError):
        rc.load_words(path)


def test_cues_that_stay_until_their_last_word_pass():
    cues = [cue(1, 1.0, 2.7, "The company made money."), cue(2, 3.0, 4.1, "Then it stopped.")]
    checks = rc.evaluate(measured(), cues, words=SPOKEN)
    assert statuses(checks, "subtitle_words") == ["PASS"]
    assert statuses(checks, "subtitle_drift") == ["PASS"]


def test_a_cue_closing_before_its_last_word_fails():
    cues = [cue(1, 1.0, 2.2, "The company made money."), cue(2, 3.0, 4.1, "Then it stopped.")]
    checks = [c for c in rc.evaluate(measured(), cues, words=SPOKEN) if c["name"] == "subtitle_words"]
    assert [c["status"] for c in checks] == ["FAIL"]
    assert "#1" in checks[0]["detail"] and "money." in checks[0]["detail"]
    assert (checks[0]["start"], checks[0]["end"]) == (2.2, 2.6)


def test_a_shared_offset_is_reported_once_as_drift_not_on_every_cue():
    shift = -0.4     # every cue starts and ends 0.4 s before the speech
    cues = [cue(1, 1.0 + shift, 2.6 + shift, "The company made money."),
            cue(2, 3.0 + shift, 4.0 + shift, "Then it stopped.")]
    m = measured()
    checks = rc.evaluate(m, cues, words=SPOKEN)
    assert statuses(checks, "subtitle_words") == ["PASS"]
    assert statuses(checks, "subtitle_drift") == ["FAIL"]
    assert m["subtitle_words"]["median_drift_s"] == pytest.approx(-0.4)


def test_small_drift_warns():
    cues = [cue(1, 0.8, 2.6, "The company made money."), cue(2, 2.8, 4.0, "Then it stopped.")]
    assert statuses(rc.evaluate(measured(), cues, words=SPOKEN), "subtitle_drift") == ["WARN"]


def test_spelled_out_numbers_still_match():
    spoken = words_of(("It", 1.0, 1.1), ("made", 1.1, 1.4), ("fifty-four", 1.4, 2.0), ("point", 2.0, 2.2),
                      ("two", 2.2, 2.4), ("billion", 2.4, 2.9), ("dollars.", 2.9, 3.5))
    early = [cue(1, 1.0, 2.95, "It made $54.2 billion.")]
    assert statuses(rc.evaluate(measured(), early, words=spoken), "subtitle_words") == ["FAIL"]  # "dollars" runs on
    on_time = [cue(1, 1.0, 3.6, "It made $54.2 billion.")]
    assert statuses(rc.evaluate(measured(), on_time, words=spoken), "subtitle_words") == ["PASS"]


def test_words_from_another_recording_fail_instead_of_passing_silently():
    other = words_of(("Completely", 1.0, 1.5), ("different", 1.5, 2.0), ("words", 2.0, 2.5))
    checks = [c for c in rc.evaluate(measured(), [cue(1, 1.0, 2.6, "The company made money.")], words=other)
              if c["name"] == "subtitle_words"]
    assert [c["status"] for c in checks] == ["FAIL"] and "words file" in checks[0]["detail"]


def test_subtitles_without_words_are_noted_not_failed():
    checks = rc.evaluate(measured(), [cue(1, 1.0, 2.6, "Hello")])
    assert statuses(checks, "subtitle_words") == ["INFO"]
    assert rc.verdict_of(checks) == "PASS"


def test_subtitle_text_rules():
    def text(*cues):
        return statuses(rc.evaluate(measured(), list(cues)), "subtitle_text")
    assert text(cue(1, 1, 3, "It made $54.2 billion.")) == ["PASS"]
    assert text(cue(1, 1, 3, "a non -cash charge")) == ["WARN"]
    assert text(cue(1, 1, 3, "the stock fell from -32.7% to zero")) == ["PASS"]
    assert text(cue(1, 1, 3, "It made $54.2", "billion last year")) == ["WARN"]
    assert text(cue(1, 1, 3, "It made $54.2"), cue(2, 3, 5, "billion last year")) == ["WARN"]


def test_freshness_check():
    assert rc._check_freshness(measured()) == []
    fresh = rc._check_freshness(measured(inputs_checked=["voice/captions.srt"], stale_inputs=[]))
    assert [c["status"] for c in fresh] == ["PASS"]
    stale = rc._check_freshness(measured(inputs_checked=["voice/captions.srt"],
                                         stale_inputs=[{"file": "voice/captions.srt", "newer_by_s": 7200.0}]))
    assert [c["status"] for c in stale] == ["WARN"] and "2.0 h newer" in stale[0]["detail"]
    minutes = rc._check_freshness(measured(inputs_checked=["voice/captions.srt"],
                                           stale_inputs=[{"file": "voice/captions.srt", "newer_by_s": 125.0}]))
    assert "2 min newer" in minutes[0]["detail"] and "0.0 h" not in minutes[0]["detail"]


@pytest.mark.parametrize("seconds,text", [(3.0, "3 s"), (59.4, "59 s"), (125.0, "2 min"), (359.0, "6 min"),
                                          (360.0, "0.1 h"), (7200.0, "2.0 h")])
def test_fmt_age_never_says_zero_hours(seconds, text):
    assert rc.fmt_age(seconds) == text


def test_words_without_srt_is_a_usage_error(tmp_path, capsys):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    words = tmp_path / "w.json"
    words.write_text("{}", encoding="utf-8")
    assert rc.main([str(video), "--words", str(words)]) == 2
    assert "--words needs --srt" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------- exemptions

EXEMPTION = """# Render check exemption

Video: render/final.mp4 sha256:{sha}
Checks: freeze, hook_motion
Reason: screen recording; the face cam moves while the screen stays still. Decided by the creator.
"""


def write_exemption(project, sha, text=EXEMPTION, name="RENDER-EXEMPTION-2026-10-17.md"):
    qa = project / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    (qa / name).write_text(text.format(sha=sha), encoding="utf-8")


def test_parse_exemption():
    parsed = rc.parse_exemption(EXEMPTION.format(sha="abcdef012345") + "Video: render/v2.mp4 sha256:0123456789ab\n")
    assert parsed["videos"] == [("render/final.mp4", "abcdef012345"), ("render/v2.mp4", "0123456789ab")]
    assert parsed["checks"] == ["freeze", "hook_motion"]
    assert parsed["reason"].startswith("screen recording")


def test_exemption_turns_named_fails_into_warnings_only_for_the_matching_hash(tmp_path):
    sha = "ab" * 32
    write_exemption(tmp_path, sha[:12])
    exemptions, notes = rc.load_exemptions(tmp_path, sha)
    assert notes == [] and exemptions[0]["checks"] == ["freeze", "hook_motion"]
    checks = rc.evaluate(measured(freezes=[[0.0, 5.0]], silences=[[116.0, 120.0]]))
    rc.apply_exemptions(checks, exemptions)
    freeze = [c for c in checks if c["name"] == "freeze"][0]
    assert freeze["status"] == "WARN" and freeze["exempted"] is True
    assert freeze["exemption"]["file"] == "qa/RENDER-EXEMPTION-2026-10-17.md"
    assert statuses(checks, "trailing_silence") == ["FAIL"]     # not named: still fails
    assert exemptions[0]["applied_to"] == ["freeze"]
    other, notes = rc.load_exemptions(tmp_path, "cd" * 32)
    assert other == [] and [n["status"] for n in notes] == ["INFO"]


def test_exemption_needs_a_reason_and_exemptable_checks(tmp_path):
    sha = "ab" * 32
    write_exemption(tmp_path, sha[:12], text="Video: render/final.mp4 sha256:{sha}\nChecks: freeze\n")
    exemptions, notes = rc.load_exemptions(tmp_path, sha)
    assert exemptions == [] and "Reason" in notes[0]["detail"]
    write_exemption(tmp_path, sha[:12], text="Video: x.mp4 sha256:{sha}\nChecks: decode, nonsense\n"
                                             "Reason: this file cannot be decoded and that is fine\n")
    exemptions, notes = rc.load_exemptions(tmp_path, sha)
    assert exemptions == [] and any("cannot be exempted" in n["detail"] for n in notes)


def test_decode_failures_are_never_exempted():
    checks = [rc.check("decode", "FAIL", "broken")]
    rc.apply_exemptions(checks, [{"file": "qa/x.md", "checks": ["decode"], "reason": "x" * 20}])
    assert checks[0]["status"] == "FAIL"


# ------------------------------------------------------------------------------------------- end to end, new checks

def make_zoom_clip(path, seconds=10):
    """A slow, smooth zoom on one still frame: freezedetect sees change, the eye sees a still picture."""
    path.parent.mkdir(parents=True, exist_ok=True)
    vf = ("trim=end_frame=1,loop=loop=-1:size=1,setpts=N/25/TB,scale=2560:1440,"
          "zoompan=z='1+0.0006*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=320x180:fps=25")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=25:d={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={seconds}",
           "-vf", vf, "-t", str(seconds), "-af", "volume=7dB", "-c:v", "mpeg4", "-q:v", "5",
           "-c:a", "aac", "-b:a", "96k", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


@needs_ffmpeg
def test_e2e_slow_zoom_on_a_still_image_fails_the_hook_band(tmp_path, capsys):
    clip = make_zoom_clip(tmp_path / "zoom.mp4")
    code, _ = run_main(capsys, clip)
    report = report_of(tmp_path / "qa" / "render-check.json")
    assert "FAIL" not in statuses(report["checks"], "freeze")      # freezedetect misses a slow zoom ...
    assert statuses(report["checks"], "hook_motion") == ["FAIL"]   # ... the motion band does not
    hook = report["measured"]["motion"]["hook"]
    assert hook["slow_pct"] > 50 and hook["frozen_pct"] == 0
    assert code == 1


@needs_ffmpeg
def test_e2e_live_profile_turns_the_hook_band_into_a_warning(tmp_path, capsys):
    clip = make_zoom_clip(tmp_path / "zoom.mp4")
    code, out = run_main(capsys, clip, "--profile", "live")
    report = report_of(tmp_path / "qa" / "render-check.json")
    assert statuses(report["checks"], "hook_motion") == ["WARN"]
    assert code == 0 and "profile live" in out


@needs_ffmpeg
def test_e2e_body_freeze_fails_in_animated_and_passes_in_live(tmp_path, capsys):
    clip = make_clip(tmp_path / "body.mp4", seconds=30,
                     vf="drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='between(t,5,9.5)'")
    code, _ = run_main(capsys, clip, "--hook-seconds", "3")
    body = [c for c in report_of(tmp_path / "qa" / "render-check.json")["checks"]
            if c["name"] == "freeze" and c["status"] == "FAIL"]
    assert len(body) == 1 and "in the body" in body[0]["detail"] and code == 1
    code, _ = run_main(capsys, clip, "--hook-seconds", "3", "--profile", "live", "--out", tmp_path / "live.json")
    assert "FAIL" not in statuses(report_of(tmp_path / "live.json")["checks"], "freeze")
    assert code == 0


@needs_ffmpeg
def test_e2e_words_offset_freshness_and_report_fields(moving_clip, tmp_path, capsys):
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:02,000 --> 00:00:03,200\nThe company made money.\n\n"
                   "2\n00:00:04,000 --> 00:00:05,100\nThen it stopped.\n", encoding="utf-8")
    words = tmp_path / "voice-check-transcript.json"
    # times are relative to the voice file; the narration starts 1 s into the video
    words.write_text(json.dumps({"segments": [
        {"start": 1.0, "end": 2.6, "text": " The company made money.", "words": [
            {"word": " The", "start": 1.0, "end": 1.2}, {"word": " company", "start": 1.2, "end": 1.7},
            {"word": " made", "start": 1.7, "end": 2.0}, {"word": " money.", "start": 2.0, "end": 2.6}]},
        {"start": 3.0, "end": 4.0, "text": " Then it stopped.", "words": [
            {"word": " Then", "start": 3.0, "end": 3.3}, {"word": " it", "start": 3.3, "end": 3.4},
            {"word": " stopped.", "start": 3.4, "end": 4.0}]}]}), encoding="utf-8")
    older = moving_clip.stat().st_mtime - 60
    for path in (srt, words):
        os.utime(path, (older, older))
    args = [moving_clip, "--srt", srt, "--words", words, "--words-offset", "1", "--json"]
    code, out = run_main(capsys, *args, "--out", tmp_path / "w.json")
    report = json.loads(out)
    early = [c for c in report["checks"] if c["name"] == "subtitle_words"]
    assert [c["status"] for c in early] == ["FAIL"] and early[0]["end"] == 3.6   # "money." ends at 2.6 + 1
    assert statuses(report["checks"], "subtitle_drift") == ["PASS"]
    assert statuses(report["checks"], "freshness") == ["PASS"]
    assert report["measured"]["words_offset"] == 1.0
    assert report["measured"]["subtitle_words"]["match_ratio"] == 1.0
    assert code == 1
    newer = moving_clip.stat().st_mtime + 3600        # subtitles edited after the render
    os.utime(srt, (newer, newer))
    _, out = run_main(capsys, *args, "--out", tmp_path / "w2.json")
    stale = [c for c in json.loads(out)["checks"] if c["name"] == "freshness"]
    assert [c["status"] for c in stale] == ["WARN"] and "captions.srt" in stale[0]["detail"]


@needs_ffmpeg
def test_e2e_exemption_file_bound_to_the_video_hash(tmp_path, capsys):
    clip = make_clip(tmp_path / "render" / "final.mp4", seconds=12,
                     vf="drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='lt(t,5)'")
    code, _ = run_main(capsys, clip)
    assert code == 1
    write_exemption(tmp_path, hashlib.sha256(clip.read_bytes()).hexdigest()[:12])
    code, out = run_main(capsys, clip)
    report = report_of(tmp_path / "qa" / "render-check.json")
    exempted = [c for c in report["checks"] if c.get("exempted")]
    assert exempted and {c["name"] for c in exempted} <= {"freeze", "hook_motion"}
    assert all(c["status"] == "WARN" for c in exempted)
    assert report["verdict"] == "PASS" and code == 0
    assert report["exemptions"][0]["file"] == "qa/RENDER-EXEMPTION-2026-10-17.md"
    assert "exempted" in out
