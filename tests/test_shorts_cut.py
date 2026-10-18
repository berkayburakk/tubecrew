"""Tests for skills/make-shorts/scripts/shorts_cut.py.

Filter and caption builders are pure and run everywhere; the end-to-end cuts use tiny lavfi clips and skip when
ffmpeg/ffprobe are not installed. Nothing here needs libass or fonts: burning is only tested on its refusal path.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "make-shorts" / "scripts" / "shorts_cut.py"


def _load():
    spec = importlib.util.spec_from_file_location("shorts_cut", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sc = _load()
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


# --- pure builders --------------------------------------------------------------------------------------------------

def test_crop_filter_centres_or_uses_a_fixed_edge():
    assert sc.crop_filter(1920, 1080) == "[0:v]crop=608:1080:656:0,scale=1080:1920,setsar=1,format=yuv420p[v]"
    assert "crop=608:1080:0:0" in sc.crop_filter(1920, 1080, 0)
    assert "crop=608:1080:1312:0" in sc.crop_filter(1920, 1080, 1312)
    with pytest.raises(sc.UsageError):
        sc.crop_filter(1920, 1080, 1400)
    with pytest.raises(sc.UsageError):
        sc.crop_filter(1920, 1080, -1)
    assert "crop=1080:1920:0:0" in sc.crop_filter(1080, 1920)          # already 9:16: nothing to cut
    assert "crop=1080:1920:0:240" in sc.crop_filter(1080, 2400)        # taller than 9:16: cut top and bottom
    with pytest.raises(sc.UsageError):
        sc.crop_filter(1080, 1920, 10)


def test_fit_size_and_filter():
    assert sc.fit_size(1920, 1080) == (1080, 608, 1920)
    assert sc.fit_size(1920, 1080, 0.2) == (1080, 760, 1536)
    assert sc.fit_size(1080, 1920) == (1080, 1920, 1080)
    graph = sc.fit_filter(1920, 1080, 0.2)
    assert "crop=1536:1080:192:0," in graph and "scale=1080:760" in graph
    assert "overlay=x=0:y=580" in graph and graph.endswith("[v]") and "gblur" in graph
    assert "crop=" not in sc.fit_filter(1920, 1080).split("[fgsrc]")[2]


def test_build_cmd_seeks_on_input_and_fades_the_edges(tmp_path):
    cmd = sc.build_cmd(tmp_path / "in.mp4", tmp_path / "out.mp4", 12.5, 30.0, sc.crop_filter(1920, 1080), True)
    assert cmd.index("-ss") < cmd.index("-i") and cmd[cmd.index("-t") + 1] == "30.000"
    assert "afade=t=out:st=29.940" in cmd[cmd.index("-af") + 1]
    assert "libx264" in cmd and "yuv420p" in cmd and "0:a:0" in cmd
    silent = sc.build_cmd(tmp_path / "in.mp4", tmp_path / "out.mp4", 0, 5, sc.crop_filter(1920, 1080), False)
    assert "-an" in silent and "-af" not in silent
    burned = sc.build_cmd(tmp_path / "in.mp4", tmp_path / "out.mp4", 0, 5, sc.fit_filter(1920, 1080), True,
                          burn="clip.ass")
    graph = burned[burned.index("-filter_complex") + 1]
    assert graph.endswith("[vb];[vb]subtitles=clip.ass[v]")


def test_retime_cues_shifts_and_clips_to_the_window():
    cues = [{"index": 1, "start": 8.0, "end": 9.5, "lines": ["before"]},
            {"index": 2, "start": 9.0, "end": 11.0, "lines": ["straddles the start"]},
            {"index": 3, "start": 12.0, "end": 14.0, "lines": ["inside"]},
            {"index": 4, "start": 19.0, "end": 21.0, "lines": ["straddles the end"]},
            {"index": 5, "start": 22.0, "end": 23.0, "lines": ["after"]}]
    out, notes = sc.retime_cues(cues, 10.0, 20.0)
    assert [c["lines"][0] for c in out] == ["straddles the start", "inside", "straddles the end"]
    assert out[0]["start"] == 0.0 and out[0]["end"] == 1.0 and out[1]["start"] == 2.0 and out[2]["end"] == 10.0
    assert [c["index"] for c in out] == [1, 2, 3]
    assert any("before the cut" in n for n in notes) and any("past the cut" in n for n in notes)


def test_words_become_short_caption_cues():
    words = [{"w": w, "s": 10 + i * 0.4, "e": 10 + i * 0.4 + 0.3} for i, w in
             enumerate("Acme paid three billion dollars for nothing. Why?".split())]
    inside = sc.window_words(words, 10.0, 14.0)
    assert inside[0] == {"word": "Acme", "start": 0.0, "end": 0.3}
    cues = sc.words_to_cues(inside)
    assert [c["lines"][0] for c in cues] == ["Acme paid three billion", "dollars for nothing.", "Why?"]
    for a, b in zip(cues, cues[1:]):
        assert a["end"] <= b["start"]
    assert sc.srt_stamp(3661.5) == "01:01:01,500"
    srt = sc.srt_text(cues)
    assert srt.startswith("1\n00:00:00,000 --> ") and "\n\n2\n" in srt
    assert sc.srt_text([]) == ""


def test_ass_captions_sit_above_the_bottom_ui_zone():
    ass = sc.ass_text([{"index": 1, "start": 0.0, "end": 1.5, "lines": ["Acme {paid}", "3 billion"]}])
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert f",2,{sc.ASS_MARGIN_LR},{sc.ASS_MARGIN_LR},{sc.ASS_MARGIN_V}," in ass
    assert "Dialogue: 0,0:00:00.00,0:00:01.50,Short,,0,0,0,,Acme (paid)\\N3 billion" in ass
    assert sc.ASS_MARGIN_V / 1920 > 0.25 and sc.ASS_MARGIN_LR / 1080 > 0.15


def test_rotation_is_read_from_tags_or_side_data():
    assert sc.rotation_of({"tags": {"rotate": "90"}}) == 90
    assert sc.rotation_of({"side_data_list": [{"rotation": -90}]}) == 270
    assert sc.rotation_of({}) == 0
    assert sc.display_size(1920, 1080, 90) == (1080, 1920) and sc.display_size(1920, 1080, 180) == (1920, 1080)


# --- end to end -----------------------------------------------------------------------------------------------------

def make_source(path: Path, seconds: float = 6, size: str = "320x180") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=25:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={seconds}",
                    "-af", "volume=7dB", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "96k", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


def probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-of", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg/ffprobe not installed")
    return make_source(tmp_path_factory.mktemp("proj") / "my video" / "render" / "final.mp4")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@needs_ffmpeg
def test_cut_fit_writes_a_vertical_short_and_keeps_the_source(source, capsys):
    before = sha(source)
    project = source.parent.parent
    code = sc.main([str(source), "--start", "1", "--end", "4", "--name", "first-cut", "--preset", "ultrafast",
                    "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    out = project / "shorts" / "first-cut.mp4"
    assert result["video"] == "shorts/first-cut.mp4" and result["sha256"] == sha(out)
    info = probe(out)
    video = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (video["width"], video["height"]) == (1080, 1920)
    assert any(s["codec_type"] == "audio" for s in info["streams"])
    assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.15)
    assert sha(source) == before
    # An existing Short is replaced only with --force.
    assert sc.main([str(source), "--start", "1", "--end", "4", "--name", "first-cut"]) == 2
    assert "--force" in capsys.readouterr().err
    assert sc.main([str(source), "--start", "1", "--end", "4", "--name", "first-cut", "--force",
                    "--mode", "crop", "--x", "0", "--preset", "ultrafast"]) == 0


@needs_ffmpeg
def test_cut_from_a_candidate_with_word_captions(source, capsys):
    project = source.parent.parent
    qa = project / "qa"
    qa.mkdir(exist_ok=True)
    (qa / "shorts-candidates.json").write_text(json.dumps({"candidates": [
        {"rank": 1, "name": "acme-deal", "cut_start": 0.5, "cut_end": 3.5}]}), encoding="utf-8")
    words = [{"word": f" w{i}.", "start": 0.6 + i * 0.5, "end": 0.9 + i * 0.5} for i in range(6)]
    (qa / "voice-check-transcript.json").write_text(json.dumps({"segments": [{"words": words}]}), encoding="utf-8")
    code = sc.main([str(source), "--candidate", "1", "--words", str(qa / "voice-check-transcript.json"),
                    "--preset", "ultrafast"])
    out = capsys.readouterr().out
    assert code == 0, out
    srt = (project / "shorts" / "acme-deal.srt").read_text(encoding="utf-8")
    assert srt.startswith("1\n00:00:00,100 --> ")
    timed = json.loads((project / "shorts" / "acme-deal.words.json").read_text(encoding="utf-8"))["words"]
    assert timed[0] == {"word": "w0.", "start": 0.1, "end": 0.4} and len(timed) == 6
    assert "shorts_check.py shorts/acme-deal.mp4 --srt shorts/acme-deal.srt --words" in out
    assert sc.main([str(source), "--candidate", "9"]) == 2 and "no candidate 9" in capsys.readouterr().err


@needs_ffmpeg
def test_cut_refuses_bad_windows_and_the_source_path(source, capsys):
    assert sc.main([str(source), "--start", "4", "--end", "3", "--name", "x"]) == 2
    assert sc.main([str(source), "--start", "1", "--end", "60", "--name", "x"]) == 2
    assert "video is" in capsys.readouterr().err
    assert sc.main([str(source), "--start", "1", "--end", "1.5", "--name", "x"]) == 2
    assert sc.main([str(source), "--start", "1", "--end", "3", "--out", str(source), "--force"]) == 2
    assert "overwrite the source" in capsys.readouterr().err
    assert sc.main([str(source), "--start", "1", "--end", "3", "--name", "bad name"]) == 2
    assert sc.main([str(source), "--start", "1", "--end", "3", "--name", "x", "--x", "10"]) == 2
    assert sc.main([str(source), "--start", "1", "--end", "3", "--name", "x", "--mode", "crop", "--x", "300"]) == 2
    assert "outside" in capsys.readouterr().err


@needs_ffmpeg
def test_cut_longer_than_the_shorts_limit_exits_2(tmp_path, capsys):
    long_source = tmp_path / "render" / "long.mp4"
    long_source.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=gray:s=32x18:r=5:d=182", "-c:v", "mpeg4", str(long_source)], check=True, timeout=120)
    assert sc.main([str(long_source), "--start", "0", "--end", "181", "--name", "too-long"]) == 2
    assert "180 s" in capsys.readouterr().err


@needs_ffmpeg
def test_burn_without_libass_stops_with_a_hint(source, capsys, monkeypatch, tmp_path):
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    monkeypatch.setattr(sc, "has_filter", lambda name: False)
    assert sc.main([str(source), "--start", "0.5", "--end", "3", "--name", "burned", "--srt", str(srt),
                    "--burn"]) == 2
    err = capsys.readouterr().err
    assert "libass" in err and "caption track" in err
    assert sc.main([str(source), "--start", "0.5", "--end", "3", "--name", "burned", "--burn"]) == 2
    assert "needs captions" in capsys.readouterr().err
