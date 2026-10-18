"""Tests for skills/check-render/scripts/transitions.py.

Pure detection and option tests run everywhere; the end-to-end tests build tiny synthetic clips with ffmpeg's
lavfi sources (a calm background, a box that snaps on and off, a fade, a hard cut) and skip when ffmpeg/ffprobe are
not installed.
"""
import array
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "check-render" / "scripts" / "transitions.py"


def _load():
    spec = importlib.util.spec_from_file_location("transitions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tr = _load()
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")
CELLS = tr.GRID_COLS * tr.GRID_ROWS


# ------------------------------------------------------------------------------------------- synthetic grids

def calm(steps, noise=0.5):
    return [[noise] * CELLS for _ in range(steps)]


def with_pop(steps=30, at=10, cells=range(6), value=120.0):
    diffs = calm(steps)
    for j in cells:
        diffs[at][j] = value
    return diffs


def test_a_box_that_snaps_on_over_a_calm_picture_is_an_overlay_pop():
    marks = tr.find_marks(with_pop(cells=[0, 1, 2, 32, 33, 34]))
    assert len(marks) == 1
    mark = marks[0]
    assert mark["kind"] == "overlay_pop" and mark["time"] == 1.1 and mark["cells"] == 6
    assert mark["region"] == [0.0, 0.0, round(3 / 32, 3), round(2 / 18, 3)]
    assert mark["at_cut"] is False and "cut" not in mark


def test_fewer_than_four_cells_is_a_cursor_not_an_overlay():
    assert tr.find_marks(with_pop(cells=range(3))) == []


def test_cells_that_were_moving_before_or_after_are_an_animation_not_a_pop():
    before = with_pop(cells=range(6))
    for j in range(6):
        before[7][j] = 5.0          # the same cells moved 0.3 s earlier
    assert tr.find_marks(before) == []
    after = with_pop(cells=range(6))
    for j in range(6):
        after[13][j] = 5.0          # and still move 0.3 s later
    assert tr.find_marks(after) == []


def test_a_pop_needs_half_a_second_of_calm_on_both_sides():
    assert tr.find_marks(with_pop(at=3, cells=range(6))) == []
    assert tr.find_marks(with_pop(steps=14, at=10, cells=range(6))) == []


def test_a_busy_frame_is_not_an_overlay_and_not_yet_a_jump():
    diffs = with_pop(cells=range(6))
    diffs[10] = [10.0] * CELLS      # overall 10: above the calm limit, below the jump limit
    for j in range(6):
        diffs[10][j] = 120.0
    assert tr.find_marks(diffs) == []


def test_hard_screen_jumps_and_cuts():
    diffs = calm(40)
    diffs[19] = [40.0] * CELLS      # t = 2.0
    diffs[29] = [40.0] * CELLS      # t = 3.0
    every = tr.find_marks(diffs)
    assert [(m["time"], m["kind"]) for m in every] == [(2.0, "screen_jump"), (3.0, "screen_jump")]
    assert every[0]["overall"] == 40.0
    at_cut = tr.find_marks(diffs, cuts=[2.1])
    assert [m["time"] for m in at_cut] == [2.0]
    assert at_cut[0]["at_cut"] is True and at_cut[0]["cut"] == 2.1
    assert tr.find_marks(diffs, cuts=[2.5]) == []


def test_marks_closer_than_the_gap_are_one_moment():
    diffs = calm(30)
    diffs[9] = [40.0] * CELLS
    diffs[10] = [40.0] * CELLS
    assert [m["time"] for m in tr.find_marks(diffs)] == [1.0]


def test_thresholds_are_options():
    diffs = calm(30)
    diffs[9] = [10.0] * CELLS
    assert tr.find_marks(diffs) == []
    assert len(tr.find_marks(diffs, jump=9.0)) == 1
    assert tr.find_marks(with_pop(cells=range(6), value=20.0)) == []
    assert len(tr.find_marks(with_pop(cells=range(6), value=20.0), step=15.0)) == 1


def test_describe_is_plain_english():
    pop = tr.find_marks(with_pop(cells=range(6)), cuts=[1.1])[0]
    text = tr.describe(pop)
    assert "6 cells appeared or vanished" in text and "at the cut 00:01.1" in text


# ------------------------------------------------------------------------------------------- decoding and options

def test_grid_frames_reads_gray16le():
    values = array.array("H", [0, 257 * 25, 65535] + [0] * (CELLS - 3))
    if sys.byteorder == "big":
        values.byteswap()
    raw = values.tobytes() * 2
    frames = tr.grid_frames(raw)
    assert len(frames) == 2 and frames[0][:3] == [0.0, 25.0, 255.0]


def test_vertical_video_keeps_cells_upright():
    assert tr.analysis_size(1920, 1080) == (320, 180, 32, 18)
    assert tr.analysis_size(1080, 1920) == (180, 320, 18, 32)


def test_grid_command_masks_then_differences_then_averages():
    cmd = tr.build_grid_cmd(Path("v.mp4"), 1920, 1080, masks=[(0, 0, 480, 1080)], index=0)
    chain = cmd[cmd.index("-vf") + 1]
    assert chain.startswith("drawbox=x=0:y=0:w=480:h=1080:color=black:t=fill,fps=10,scale=320:180")
    assert "tblend=all_mode=difference" in chain and "scale=32:18:flags=area" in chain
    assert cmd[-1] == "pipe:1" and "gray16le" in cmd and cmd[cmd.index("-map") + 1] == "0:0"


def test_parse_time_and_cuts(tmp_path):
    assert tr.parse_time("62.3") == pytest.approx(62.3)
    assert tr.parse_time("1:02.3") == pytest.approx(62.3)
    assert tr.parse_time("1:00:05") == 3605
    assert tr.parse_cuts("40, 12.5,1:02.3") == [12.5, 40.0, 62.3]
    listed = tmp_path / "cuts.json"
    listed.write_text("[3, 1.5]", encoding="utf-8")
    assert tr.parse_cuts(str(listed)) == [1.5, 3.0]
    keyed = tmp_path / "edit.json"
    keyed.write_text(json.dumps({"cuts": [8, 2]}), encoding="utf-8")
    assert tr.parse_cuts(str(keyed)) == [2.0, 8.0]
    plain = tmp_path / "cuts.txt"
    plain.write_text("4.0\n0:09\n", encoding="utf-8")
    assert tr.parse_cuts(str(plain)) == [4.0, 9.0]
    with pytest.raises(tr.UsageError):
        tr.parse_cuts("soon")
    bad = tmp_path / "bad.json"
    bad.write_text('{"cuts": ["x"]}', encoding="utf-8")
    with pytest.raises(tr.UsageError):
        tr.parse_cuts(str(bad))


def test_parse_mask():
    assert tr.parse_mask("0,0,480,1080") == (0, 0, 480, 1080)
    for text in ("0,0,480", "a,b,c,d", "0,0,0,10", "-1,0,10,10"):
        with pytest.raises(tr.UsageError):
            tr.parse_mask(text)


def test_usage_errors_exit_2(tmp_path, capsys):
    assert tr.main([str(tmp_path / "missing.mp4")]) == 2
    assert "video not found" in capsys.readouterr().err
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    assert tr.main([str(video), "--mask", "1,2,3"]) == 2
    assert "--mask" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------- end to end (ffmpeg)

def make_clip(path, seconds=9, vf=None, extra=None):
    """A calm 640x360 background; `vf` draws on it, `extra` is a second lavfi source for -filter_complex."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"color=c=0x336699:s=640x360:r=25:d={seconds}"]
    if extra:
        cmd += ["-f", "lavfi", "-i", extra[0], "-filter_complex", extra[1]]
    elif vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "mpeg4", "-q:v", "3", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


POP_VF = "drawbox=x=200:y=120:w=120:h=60:color=white:t=fill:enable='between(t,3,6)'"


def run_main(capsys, *args):
    code = tr.main([str(a) for a in args])
    return code, capsys.readouterr().out


@needs_ffmpeg
def test_e2e_a_box_that_pops_in_and_out_is_reported_with_frame_pairs(tmp_path, capsys):
    clip = make_clip(tmp_path / "render" / "final.mp4", vf=POP_VF)
    frames = tmp_path / "qa" / "transition-frames"
    code, out = run_main(capsys, clip, "--frames", frames)
    report = json.loads((tmp_path / "qa" / "transitions.json").read_text(encoding="utf-8"))
    pops = [m for m in report["marks"] if m["kind"] == "overlay_pop"]
    assert code == 0 and len(pops) == 2
    assert abs(pops[0]["time"] - 3.0) <= 0.15 and abs(pops[1]["time"] - 6.0) <= 0.15
    assert pops[0]["cells"] >= 4
    left, top, right, bottom = pops[0]["region"]
    assert 0.28 <= left <= 0.34 and 0.47 <= right <= 0.53 and 0.30 <= top <= 0.36 and 0.47 <= bottom <= 0.53
    assert report["video"] == "render/final.mp4" and report["grid"] == "32x18" and report["fps"] == 10
    assert report["counts"] == {"overlay_pop": 2, "screen_jump": 0}
    assert all(m["frames"].startswith("qa/transition-frames/pair-") for m in pops)
    assert len(list(frames.glob("pair-*.png"))) == 2
    assert str(tmp_path) not in (tmp_path / "qa" / "transitions.json").read_text(encoding="utf-8")
    assert "overlay pop" in out and "qa/transitions.json" in out


@needs_ffmpeg
def test_e2e_a_fade_is_not_a_pop(tmp_path, capsys):
    clip = make_clip(tmp_path / "fade.mp4", extra=(
        "color=c=white:s=120x60:r=25:d=9",
        "[1:v]format=rgba,fade=in:st=3:d=1:alpha=1[b];[0:v][b]overlay=200:120"))
    code, _ = run_main(capsys, clip, "--json")
    report = json.loads((tmp_path / "qa" / "transitions.json").read_text(encoding="utf-8"))
    assert code == 0 and report["marks"] == []


@needs_ffmpeg
def test_e2e_mask_hides_a_region(tmp_path, capsys):
    clip = make_clip(tmp_path / "pop.mp4", vf=POP_VF)
    code, _ = run_main(capsys, clip, "--mask", "180,100,160,100", "--out", tmp_path / "masked.json")
    report = json.loads((tmp_path / "masked.json").read_text(encoding="utf-8"))
    assert code == 0 and report["marks"] == [] and report["masks"] == [[180, 100, 160, 100]]


@needs_ffmpeg
def test_e2e_hard_jump_at_a_cut(tmp_path, capsys):
    path = tmp_path / "jump.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=0x336699:s=640x360:r=25:d=4",
                    "-f", "lavfi", "-i", "color=c=0xdd8833:s=640x360:r=25:d=4",
                    "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0", "-c:v", "mpeg4", "-q:v", "3", str(path)],
                   check=True, capture_output=True)
    code, out = run_main(capsys, path, "--json")
    jumps = json.loads(out)["marks"]
    assert code == 0 and [m["kind"] for m in jumps] == ["screen_jump"] and abs(jumps[0]["time"] - 4.0) <= 0.15
    _, out = run_main(capsys, path, "--cuts", "0:04", "--json", "--out", tmp_path / "cut.json")
    at_cut = json.loads(out)["marks"]
    assert len(at_cut) == 1 and at_cut[0]["at_cut"] is True and at_cut[0]["cut"] == 4.0
    _, out = run_main(capsys, path, "--cuts", "2", "--json", "--out", tmp_path / "elsewhere.json")
    assert json.loads(out)["marks"] == []


@needs_ffmpeg
def test_e2e_unreadable_file_is_an_environment_error(tmp_path, capsys):
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"not a video at all")
    assert tr.main([str(video)]) == 2
    assert "transitions: error" in capsys.readouterr().err
