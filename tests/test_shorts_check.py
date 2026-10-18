"""Tests for skills/make-shorts/scripts/shorts_check.py.

The Shorts rules, the caption-position estimates and the UI zones are pure and run everywhere; the end-to-end
checks build tiny lavfi clips and skip when ffmpeg/ffprobe are not installed. No libass, fonts or OCR needed.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "make-shorts" / "scripts" / "shorts_check.py"
CUT = ROOT / "skills" / "make-shorts" / "scripts" / "shorts_cut.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sk = _load(SCRIPT, "shorts_check")
cut = _load(CUT, "shorts_cut_for_check")
HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")

MOVING = {"steps": 7, "events": 2, "still_share": 0.0, "frozen_share": 0.0, "moves": True}
STILL = {"steps": 7, "events": 0, "still_share": 1.0, "frozen_share": 1.0, "moves": False}
NO_ZONE = {"measured": False}


def status(checks, name):
    return [c["status"] for c in checks if c["name"] == name]


def detail(checks, name):
    return " ".join(c["detail"] for c in checks if c["name"] == name)


# --- pure rules -----------------------------------------------------------------------------------------------------

def test_aspect_duration_and_resolution():
    vertical = {"width": 1080, "height": 1920, "rotation": 0}
    checks = sk.shorts_checks(vertical, 45.0, MOVING, [], 2.0, NO_ZONE)
    assert status(checks, "aspect") == ["PASS"] and status(checks, "duration") == ["PASS"]
    assert status(checks, "resolution") == ["PASS"] and status(checks, "music") == []
    landscape = sk.shorts_checks({"width": 1920, "height": 1080, "rotation": 0}, 45.0, MOVING, [], None, NO_ZONE)
    assert status(landscape, "aspect") == ["FAIL"] and "regular video" in detail(landscape, "aspect")
    assert status(landscape, "resolution") == []
    square = sk.shorts_checks({"width": 720, "height": 720, "rotation": 0}, 45.0, MOVING, [], None, NO_ZONE)
    assert status(square, "aspect") == ["PASS"] and "square" in detail(square, "aspect")
    assert status(square, "resolution") == ["WARN"] and "1080x1080" in detail(square, "resolution")
    assert status(sk.shorts_checks(vertical, 180.5, MOVING, [], None, NO_ZONE), "duration") == ["FAIL"]
    assert status(sk.shorts_checks(vertical, 179.0, MOVING, [], None, NO_ZONE), "duration") == ["WARN"]
    assert status(sk.shorts_checks(vertical, 180.0, MOVING, [], None, NO_ZONE), "duration") == ["WARN"]
    long_one = sk.shorts_checks(vertical, 75.0, MOVING, [], None, NO_ZONE)
    assert status(long_one, "music") == ["INFO"] and "Content ID" in detail(long_one, "music")
    four_five = sk.shorts_checks({"width": 1080, "height": 1350, "rotation": 0}, 30, MOVING, [], None, NO_ZONE)
    assert status(four_five, "resolution") == ["WARN"]
    assert status(sk.shorts_checks({"width": None, "height": None}, 30, MOVING, [], None, NO_ZONE),
                  "aspect") == ["FAIL"]


def test_opening_must_move_or_show_text():
    vertical = {"width": 1080, "height": 1920, "rotation": 0}
    assert status(sk.shorts_checks(vertical, 30, MOVING, [], None, NO_ZONE), "opening") == ["PASS"]
    still = sk.shorts_checks(vertical, 30, STILL, [], None, NO_ZONE)
    assert status(still, "opening") == ["FAIL"] and "--ocr" in detail(still, "opening")
    assert status(sk.shorts_checks(vertical, 30, STILL, ["OCR"], None, NO_ZONE), "opening") == ["PASS"]
    assert status(sk.shorts_checks(vertical, 30, STILL, [], None, NO_ZONE, profile="live"), "opening") == ["WARN"]


def test_opening_summary_scales_the_band_to_two_seconds():
    assert sk.opening_summary([0.0] * 7)["moves"] is False
    assert sk.opening_summary([0.5, 0.4, 0.3, 30.0, 0.2, 0.1, 0.0])["moves"] is True       # one cut is enough
    assert sk.opening_summary([2.0, 2.5, 3.0, 0.5, 0.5, 2.2, 2.4])["moves"] is True       # most steps move
    assert sk.opening_summary([0.5, 0.6, 0.7, 0.8, 2.0, 0.5, 0.4])["moves"] is False      # a slow drift
    assert sk.opening_summary([])["moves"] is None


def test_loop_is_information_only():
    vertical = {"width": 1080, "height": 1920, "rotation": 0}
    assert "matches the first" in detail(sk.shorts_checks(vertical, 30, MOVING, [], 3.0, NO_ZONE), "loop")
    assert "differs" in detail(sk.shorts_checks(vertical, 30, MOVING, [], 40.0, NO_ZONE), "loop")
    assert status(sk.shorts_checks(vertical, 30, MOVING, [], 40.0, NO_ZONE), "loop") == ["INFO"]
    assert sk.frame_difference(b"\x00\x10", b"\x10\x00") == 16.0
    assert sk.frame_difference(b"", b"") is None


def test_ui_zones():
    assert sk.zones_hit((0.1, 0.90, 0.9, 0.96)) == ["bottom"]
    assert sk.zones_hit((0.88, 0.4, 0.98, 0.5)) == ["right"]
    assert sk.zones_hit((0.80, 0.80, 0.99, 0.95)) == ["bottom", "right"]
    assert sk.zones_hit((0.2, 0.40, 0.8, 0.5)) == []
    assert sk.zones_hit((0.2, 0.70, 0.8, 0.752)) == []           # touching the line is not hiding


def test_caption_positions_from_ass_and_srt():
    cues = [{"index": 1, "start": 0.2, "end": 1.5, "lines": ["Acme paid 3 billion dollars for nothing"]}]
    from_cut = sk.parse_ass(cut.ass_text(cues))
    assert len(from_cut) == 1 and from_cut[0]["lines"] == ["Acme paid 3 billion dollars for nothing"]
    box = from_cut[0]["box"]
    assert box[3] <= 0.75 and box[2] <= 0.85 and sk.zones_hit(box) == []
    pinned = cut.ass_text(cues).replace(",,Acme", ",,{\\an2\\pos(540,1880)}Acme")
    assert sk.zones_hit(sk.parse_ass(pinned)[0]["box"]) == ["bottom"]
    srt = "1\n00:00:00,500 --> 00:00:02,000\nDefault position\n\n2\n00:00:02,000 --> 00:00:03,000\n{\\an8}Top line\n"
    positioned = sk.srt_positions(srt)
    assert sk.zones_hit(positioned[0]["box"]) == ["bottom"] and positioned[0]["positioned"] is False
    assert sk.zones_hit(positioned[1]["box"]) == [] and positioned[1]["lines"] == ["Top line"]


def test_zone_report_fails_burned_captions_and_warns_ocr():
    vertical = {"width": 1080, "height": 1920, "rotation": 0}
    srt = "1\n00:00:00,500 --> 00:00:02,000\nDefault position\n"
    burned = sk.zone_report(sk.srt_positions(srt), True, None)
    checks = sk.shorts_checks(vertical, 30, MOVING, [], None, burned)
    assert status(checks, "safe_zone") == ["FAIL"] and "default bottom position" in detail(checks, "safe_zone")
    track = sk.zone_report(sk.srt_positions(srt), False, None)
    assert status(sk.shorts_checks(vertical, 30, MOVING, [], None, track), "safe_zone") == ["INFO"]
    ocr = sk.zone_report(None, False, [{"t": 3.0, "text": "SUBSCRIBE", "box": (0.3, 0.9, 0.7, 0.95)},
                                       {"t": 4.0, "text": "middle", "box": (0.3, 0.4, 0.7, 0.45)}])
    checks = sk.shorts_checks(vertical, 30, MOVING, [], None, ocr)
    assert status(checks, "safe_zone") == ["WARN"] and "SUBSCRIBE" in detail(checks, "safe_zone")
    clean = sk.zone_report(None, False, [{"t": 4.0, "text": "middle", "box": (0.3, 0.4, 0.7, 0.45)}])
    assert status(sk.shorts_checks(vertical, 30, MOVING, [], None, clean), "safe_zone") == ["PASS"]


def test_display_geometry_applies_rotation():
    data = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080, "side_data_list": [{"rotation": 90}]}]}
    assert sk.display_geometry(json.dumps(data)) == {"width": 1080, "height": 1920, "rotation": 90}
    assert sk.display_geometry("{}")["width"] is None


def test_exemptions_bind_to_the_hash_and_never_cover_platform_rules(tmp_path):
    qa = tmp_path / "qa"
    qa.mkdir()
    digest = "ab" * 32
    (qa / "RENDER-EXEMPTION-2026-10-18.md").write_text(
        f"Video: shorts/a.mp4 sha256:{digest[:12]}\nChecks: opening, aspect\n"
        "Reason: a deliberate still title card opens this Short (creator decision)\n", encoding="utf-8")
    (qa / "RENDER-EXEMPTION-2026-10-17.md").write_text(
        "Video: render/final.mp4 sha256:cdcdcdcdcdcd\nChecks: freeze\nReason: the long video's own exemption\n",
        encoding="utf-8")
    exemptions, notes = sk.load_exemptions(tmp_path, digest)
    assert exemptions == [{"file": "qa/RENDER-EXEMPTION-2026-10-18.md", "checks": ["opening"],
                           "reason": "a deliberate still title card opens this Short (creator decision)"}]
    assert len(notes) == 1 and "aspect" in notes[0]["detail"]


# --- end to end -----------------------------------------------------------------------------------------------------

def make_clip(path: Path, size: str = "108x192", seconds: float = 4, still: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    picture = f"color=c=gray:s={size}:r=10:d={seconds}" if still else f"testsrc2=size={size}:rate=10:d={seconds}"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", picture,
                    "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={seconds}",
                    "-af", "volume=7dB", "-c:v", "mpeg4", "-q:v", "5", "-c:a", "aac", "-b:a", "96k", str(path)],
                   check=True, capture_output=True, timeout=120)
    return path


def run(capsys, *args):
    code = sk.main([str(a) for a in args])
    return code, capsys.readouterr().out


@needs_ffmpeg
def test_e2e_vertical_moving_short_passes_with_a_resolution_warning(tmp_path, capsys):
    clip = make_clip(tmp_path / "my video" / "shorts" / "the-deal.mp4")
    code, out = run(capsys, clip)
    report_path = tmp_path / "my video" / "qa" / "shorts-check-the-deal.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert code == 0, out
    assert report["verdict"] == "PASS" and report["video"] == "shorts/the-deal.mp4"
    assert report["sha256"] == hashlib.sha256(clip.read_bytes()).hexdigest()
    checks = report["checks"]
    assert status(checks, "aspect") == ["PASS"] and status(checks, "resolution") == ["WARN"]
    assert status(checks, "opening") == ["PASS"] and status(checks, "safe_zone") == ["INFO"]
    assert report["measured"]["hook_seconds"] == pytest.approx(4.0, abs=0.2)
    assert report["measured"]["shorts"]["opening"]["steps"] == 7
    assert report["for_reviewer"] and "VERDICT: PASS" in out
    assert str(tmp_path) not in report_path.read_text(encoding="utf-8")


@needs_ffmpeg
def test_e2e_landscape_fails(tmp_path, capsys):
    clip = make_clip(tmp_path / "shorts" / "wide.mp4", size="192x108")
    code, _ = run(capsys, clip, "--out", tmp_path / "wide.json")
    report = json.loads((tmp_path / "wide.json").read_text(encoding="utf-8"))
    assert code == 1 and status(report["checks"], "aspect") == ["FAIL"]


@needs_ffmpeg
def test_e2e_still_opening_fails_and_burned_default_captions_sit_in_the_ui_zone(tmp_path, capsys):
    clip = make_clip(tmp_path / "shorts" / "still.mp4", still=True)
    srt = tmp_path / "shorts" / "still.srt"
    srt.write_text("1\n00:00:00,200 --> 00:00:01,800\nA caption at the bottom\n", encoding="utf-8")
    code, _ = run(capsys, clip)
    report = json.loads((tmp_path / "qa" / "shorts-check-still.json").read_text(encoding="utf-8"))
    assert code == 1
    assert status(report["checks"], "opening") == ["FAIL"] and "FAIL" in status(report["checks"], "freeze")
    code, _ = run(capsys, clip, "--srt", srt, "--burned")
    report = json.loads((tmp_path / "qa" / "shorts-check-still.json").read_text(encoding="utf-8"))
    assert status(report["checks"], "opening") == ["PASS"]          # burned text carries a still opening ...
    assert status(report["checks"], "safe_zone") == ["FAIL"]        # ... but it sits under YouTube's title
    assert status(report["checks"], "subtitle_end") == ["PASS"]


@needs_ffmpeg
def test_e2e_ocr_without_the_package_exits_2(tmp_path, capsys, monkeypatch):
    clip = make_clip(tmp_path / "shorts" / "ocr.mp4")

    def missing():
        raise sk.td.UsageError("not installed")
    monkeypatch.setattr(sk.td, "load_ocr", missing)
    assert sk.main([str(clip), "--ocr"]) == 2
    assert "rapidocr-onnxruntime" in capsys.readouterr().err


def test_usage_errors_exit_2(tmp_path, capsys):
    assert sk.main([str(tmp_path / "missing.mp4")]) == 2
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    assert sk.main([str(video), "--burned"]) == 2 and "--srt" in capsys.readouterr().err
    assert sk.main([str(video), "--words", str(video)]) == 2
