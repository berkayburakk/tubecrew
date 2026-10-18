"""Tests for skills/check-render/scripts/text_dose.py.

The OCR engine is always mocked (CI has no rapidocr-onnxruntime). Frame extraction is faked in the pure-Python
end-to-end test; one test uses real ffmpeg frames and skips when ffmpeg is not installed.
"""
import hashlib
import importlib.util
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "check-render" / "scripts" / "text_dose.py"


def _load():
    spec = importlib.util.spec_from_file_location("text_dose", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


td = _load()
HAS_FFMPEG = bool(shutil.which("ffmpeg"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def fake_jpeg(width=960, height=540):
    """The smallest byte string jpeg_size can read: SOI, an APP0 segment, then SOF0 with the size."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def quad(x, y, w, h):
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


BIG = (quad(100, 200, 700, 80), "RECORD PROFIT", 0.95)                 # 80/540 = 14.8% tall, 73% wide
SUBTITLE = (quad(200, 495, 560, 25), "a small subtitle line", 0.95)    # bottom band, 4.6% tall
DOC_LINE = (quad(50, 100, 400, 18), "small document text line", 0.9)   # 3.3% tall: evidence, not a card


def frame(t, *boxes, width=960, height=540):
    return {"t": t, "boxes": td.boxes_of(list(boxes), width, height)}


# ------------------------------------------------------------------------------------------- measures

def test_box_size_follows_the_box_sides_not_the_axis_bounds():
    assert td.box_size(quad(0, 0, 100, 20)) == (20.0, 100.0)
    # a long, slightly tilted line: the axis-aligned height would be 55 px, the real line height is ~20 px
    tilted = [[0, 35], [1000, 0], [1000, 20], [0, 55]]
    h, w = td.box_size(tilted)
    assert h == pytest.approx(20.0) and w == pytest.approx(1000.6, abs=0.1)


def test_boxes_of_drops_low_confidence_and_single_characters():
    boxes = td.boxes_of([BIG, (quad(0, 0, 50, 50), "x", 0.99), (quad(0, 0, 300, 60), "blurry", 0.3)], 960, 540)
    assert [b[0] for b in boxes] == ["RECORD PROFIT"]
    assert boxes[0][1] == pytest.approx(0.148, abs=0.001) and boxes[0][2] == pytest.approx(0.44, abs=0.01)


def test_card_definition_ignores_subtitles_and_small_document_text():
    assert td.is_card(frame(0, BIG)["boxes"])[0] is True
    assert td.is_card(frame(0, SUBTITLE)["boxes"])[0] is False
    assert td.is_card(frame(0, DOC_LINE, DOC_LINE, DOC_LINE)["boxes"])[0] is False
    small_big = (quad(100, 200, 100, 30), "tag", 0.9)                  # tall enough, too little area (0.6%)
    assert td.is_card(frame(0, small_big)["boxes"])[0] is False
    assert td.is_subtitle(0.05, 0.9) and not td.is_subtitle(0.08, 0.9) and not td.is_subtitle(0.05, 0.5)


def make_frames(card_indexes, count, every=2.0):
    return [frame(i * every, BIG, SUBTITLE) if i in card_indexes else frame(i * every, SUBTITLE)
            for i in range(count)]


def test_summarize_gates():
    # 60 samples = 120 s; cards at 0-2 (6 s run), 20-21 and 40: 6 cards, 3 entries
    frames = make_frames({0, 1, 2, 20, 21, 40}, 60)
    r = td.summarize(frames, 2.0)
    s = r["summary"]
    assert s["card_ratio"] == pytest.approx(0.1)
    assert s["longest_run_s"] == 6.0 and s["longest_run_at"] == 0.0
    assert s["entries_per_min"] == pytest.approx(1.5)
    assert s["worst_30s"] == pytest.approx(0.2) and s["worst_30s_at"] == 0.0
    assert r["over"] == []
    assert [c["t"] for c in r["card_frames"]] == [0.0, 2.0, 4.0, 40.0, 42.0, 80.0]
    assert r["card_frames"][0]["text"] == ["RECORD PROFIT"]


def test_summarize_flags_every_gate():
    frames = make_frames(set(range(0, 12)) | set(range(14, 30, 2)), 30)   # 24 s run, then cards every other frame
    over = {o["name"] for o in td.summarize(frames, 2.0)["over"]}
    assert over == {"card_ratio", "worst_30s", "longest_run_s", "entries_per_min"}


def test_a_short_tail_slice_does_not_decide_the_worst_stretch():
    frames = make_frames({31}, 32)        # one card in the 2-sample tail slice after one full 30 s slice
    r = td.summarize(frames, 2.0)
    assert r["slices"][-1]["ratio"] == 0.5 and r["summary"]["worst_30s"] == 0.0


def test_parse_limits():
    assert td.parse_limits(["card_ratio=0.3"])["card_ratio"] == 0.3
    assert td.parse_limits(None) == td.LIMITS
    for bad in (["nonsense=1"], ["card_ratio=lots"]):
        with pytest.raises(td.UsageError):
            td.parse_limits(bad)


def test_jpeg_size_reads_the_frame_header(tmp_path):
    path = tmp_path / "f.jpg"
    path.write_bytes(fake_jpeg(960, 404))
    assert td.jpeg_size(path) == (960, 404)
    path.write_bytes(b"not a jpeg")
    with pytest.raises(ValueError):
        td.jpeg_size(path)


# ------------------------------------------------------------------------------------------- main

def test_missing_ocr_package_is_exit_2_with_an_install_hint(tmp_path, monkeypatch, capsys):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(td.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)     # makes the import fail
    assert td.main([str(video)]) == 2
    assert "pip install rapidocr-onnxruntime" in capsys.readouterr().err


def test_usage_errors(tmp_path, capsys):
    assert td.main([str(tmp_path / "missing.mp4")]) == 2
    assert td.main([]) == 2
    assert td.main(["--from-json", str(tmp_path / "nope.json")]) == 2
    assert "error" in capsys.readouterr().err


class FakeOCR:
    """Big text on the frames whose number (f00001.jpg = 0) is in `cards`, a subtitle on every frame."""

    def __init__(self, cards):
        self.cards = cards
        self.calls = 0

    def __call__(self, path):
        self.calls += 1
        index = int(Path(path).stem[1:]) - 1
        return [BIG, SUBTITLE] if index in self.cards else [SUBTITLE]


def test_main_end_to_end_with_faked_frames_and_ocr(tmp_path, monkeypatch, capsys):
    project = tmp_path / "proj"
    (project / "render").mkdir(parents=True)
    video = project / "render" / "final.mp4"
    video.write_bytes(b"pretend video bytes")

    def fake_extract(video_path, every, folder, start=0.0, end=0.0):
        for i in range(30):
            (folder / f"f{i + 1:05d}.jpg").write_bytes(fake_jpeg())
        return sorted(folder.glob("f*.jpg"))

    ocr = FakeOCR(set(range(0, 10)))                         # a 20 s text card at the start
    monkeypatch.setattr(td.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(td, "load_ocr", lambda: ocr)
    monkeypatch.setattr(td, "extract_frames", fake_extract)
    code = td.main([str(video)])
    out = capsys.readouterr().out
    report = json.loads((project / "qa" / "text-dose.json").read_text(encoding="utf-8"))
    assert code == 1 and report["verdict"] == "FAIL" and ocr.calls == 30
    assert {o["name"] for o in report["over"]} >= {"card_ratio", "longest_run_s"}
    assert report["video"] == "render/final.mp4"
    assert report["sha256"] == hashlib.sha256(video.read_bytes()).hexdigest()
    assert report["samples"] == 30 and len(report["frames"]) == 30
    assert str(tmp_path) not in (project / "qa" / "text-dose.json").read_text(encoding="utf-8")
    assert "VERDICT: FAIL" in out and "qa/text-dose.json" in out

    # re-score the saved boxes with a looser limit for a text-heavy genre: no OCR, no video needed
    rescored = tmp_path / "rescored.json"
    code = td.main(["--from-json", str(project / "qa" / "text-dose.json"), "--limit", "card_ratio=0.5",
                    "--limit", "longest_run_s=30", "--limit", "worst_30s=0.7", "--out", str(rescored), "--json"])
    printed = json.loads(capsys.readouterr().out)
    assert code == 0 and printed["verdict"] == "PASS" and ocr.calls == 30
    assert printed["sha256"] == report["sha256"] and "frames" not in printed


@needs_ffmpeg
def test_real_frames_from_ffmpeg_with_mocked_ocr(tmp_path, monkeypatch, capsys):
    video = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=320x180:rate=10:d=12", "-c:v", "mpeg4", "-q:v", "5", str(video)],
                   check=True, capture_output=True)
    ocr = FakeOCR(set())
    monkeypatch.setattr(td, "load_ocr", lambda: ocr)
    code = td.main([str(video), "--every", "2", "--out", str(tmp_path / "td.json")])
    report = json.loads((tmp_path / "td.json").read_text(encoding="utf-8"))
    assert code == 0 and report["verdict"] == "PASS"
    assert report["samples"] == ocr.calls == 6
    assert [f["t"] for f in report["frames"]] == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    assert report["frames"][0]["boxes"][0][0] == "a small subtitle line"
