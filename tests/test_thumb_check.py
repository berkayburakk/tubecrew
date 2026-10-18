"""Tests for skills/check-thumbnail/scripts/thumb_check.py.

Pure checks (pixel statistics, file facts, text rules, title overlap) run everywhere; the end-to-end tests build
tiny synthetic images with ffmpeg's lavfi sources and skip when ffmpeg/ffprobe are not installed.
"""
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "check-thumbnail" / "scripts" / "thumb_check.py"
UPLOAD_PY = ROOT / "skills" / "upload-private" / "scripts" / "upload.py"

spec = importlib.util.spec_from_file_location("thumb_check", SCRIPT)
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not installed")


def statuses(checks):
    return {c["name"]: c["status"] for c in checks}


def info(**over):
    base = {"ext": ".jpg", "codec": "mjpeg", "width": 1280, "height": 720, "pix_fmt": "yuvj420p", "size": 200_000,
            "dark_ratio": 0.2, "clusters": 8, "decode_error": None}
    base.update(over)
    return base


# ------------------------------------------------------------------------------------------- pixel statistics

def test_pixel_stats_black_frame():
    s = tc.pixel_stats(bytes(3 * 100))
    assert s == {"dark_ratio": 1.0, "clusters": 1}


def test_pixel_stats_counts_coarse_colour_clusters():
    # 16 equal blocks, one per coarse red/green level pair -> 16 clusters of 6.25% each
    raw = bytearray()
    for r in (0, 64, 128, 192):
        for g in (0, 64, 128, 192):
            raw += bytes((r + 10, g + 10, 200)) * 50
    s = tc.pixel_stats(bytes(raw))
    assert s["clusters"] == 16
    assert s["dark_ratio"] == 0.0


def test_pixel_stats_ignores_tiny_specks_and_luma_threshold():
    raw = bytes((255, 255, 255)) * 995 + bytes((255, 0, 0)) * 5   # 0.5% red: below the 1% cluster share
    assert tc.pixel_stats(raw)["clusters"] == 1
    assert tc.pixel_stats(bytes((24, 24, 24)) * 10)["dark_ratio"] == 1.0
    assert tc.pixel_stats(bytes((26, 26, 26)) * 10)["dark_ratio"] == 0.0
    assert tc.pixel_stats(b"") == {"dark_ratio": None, "clusters": None}


# ------------------------------------------------------------------------------------------- file facts

def test_clean_thumbnail_passes():
    checks = tc.evaluate_image(info())
    assert set(statuses(checks).values()) == {"PASS"}
    assert tc.verdict_of(checks) == "PASS"


@pytest.mark.parametrize("over, name, status", [
    ({"ext": ".webp", "codec": "webp"}, "format", "FAIL"),
    ({"ext": ".bmp", "codec": "bmp"}, "format", "FAIL"),
    ({"ext": ".jpg", "codec": "png"}, "format", "FAIL"),           # PNG bytes with a .jpg name
    ({"ext": ".png", "codec": "webp"}, "format", "FAIL"),
    ({"ext": ".jpeg", "codec": "mjpeg"}, "format", "PASS"),
    ({"ext": ".png", "codec": "png", "pix_fmt": "rgba"}, "transparency", "WARN"),
    ({"width": 1024, "height": 768}, "aspect", "FAIL"),
    ({"width": 1290, "height": 720}, "aspect", "PASS"),              # under 1% off
    ({"width": 1320, "height": 720}, "aspect", "WARN"),              # about 3% off
    ({"width": 600, "height": 338}, "resolution", "FAIL"),
    ({"width": 960, "height": 540}, "resolution", "WARN"),
    ({"width": 1920, "height": 1080}, "resolution", "PASS"),
    ({"size": tc.THUMBNAIL_MAX_BYTES + 1}, "file_size", "FAIL"),
    ({"dark_ratio": 0.57}, "dark_area", "WARN"),
    ({"dark_ratio": 0.50}, "dark_area", "PASS"),
    ({"clusters": 15}, "clutter", "WARN"),
    ({"clusters": 14}, "clutter", "PASS"),
])
def test_file_and_picture_checks(over, name, status):
    assert statuses(tc.evaluate_image(info(**over)))[name] == status


def test_genre_limits_are_configurable():
    s = statuses(tc.evaluate_image(info(dark_ratio=0.7, clusters=20), dark_max=0.8, clutter_max=24))
    assert s["dark_area"] == "PASS" and s["clutter"] == "PASS"


def test_unreadable_image_is_one_failure():
    checks = tc.evaluate_image(info(decode_error="Invalid data found when processing input"))
    assert [(c["name"], c["status"]) for c in checks] == [("format", "FAIL")]


def test_size_limit_matches_the_upload_script():
    """thumb_check and upload.py must agree on the thumbnail size limit."""
    if not UPLOAD_PY.is_file():
        pytest.skip("upload.py not present")
    m = re.search(r"^THUMBNAIL_MAX_BYTES\s*=\s*([\d\s*]+)", UPLOAD_PY.read_text(encoding="utf-8"), re.M)
    if not m:
        pytest.skip("upload.py no longer defines THUMBNAIL_MAX_BYTES as a plain product")
    value = 1
    for part in m.group(1).split("*"):
        value *= int(part)
    assert value == tc.THUMBNAIL_MAX_BYTES


# ------------------------------------------------------------------------------------------- text rules

def test_no_text_skips_the_text_rules():
    assert statuses(tc.evaluate_text([], "Any title")) == {"text": "INFO"}


def test_title_overlap_matches_whole_words_only():
    assert statuses(tc.evaluate_text(["ART"], "Why Startups Fail"))["title_overlap"] == "PASS"
    assert statuses(tc.evaluate_text(["STARTUPS FAIL"], "Why Startups Fail"))["title_overlap"] == "WARN"


def test_good_text_passes():
    s = statuses(tc.evaluate_text(["SOLD AT", "THE TOP"], "How Acme Lost $3 Billion in One Week"))
    assert s == {"text_lines": "PASS", "text_length": "PASS", "text_balance": "PASS", "title_overlap": "PASS"}


@pytest.mark.parametrize("lines, name", [
    (["ONE", "TWO", "SIX", "TEN"], "text_lines"),
    (["THE WHOLE STORY IN ONE LINE"], "text_length"),
    (["A", "BILLION"], "text_balance"),
    (["$4M", "VERSUS", "$9BN"], "abbreviation"),
    (["4.5 BLN", "GONE"], "abbreviation"),
    (["REVENUE", "UP YOY"], "abbreviation"),
])
def test_text_warnings(lines, name):
    assert statuses(tc.evaluate_text(lines))[name] == "WARN"


def test_readable_units_are_not_abbreviations():
    assert tc.find_abbreviations(["$4M", "48 HOURS", "$4B", "10K VIEWS", "BNPL"]) == []
    assert tc.find_abbreviations(["$6BN"]) == ["$6BN"]
    assert tc.find_abbreviations(["2 TN", "w/ AI"]) == ["2 TN", "w/"]


def test_max_line_chars_is_configurable():
    lines = ["FOURTEEN CHARS"]
    assert statuses(tc.evaluate_text(lines))["text_length"] == "WARN"
    assert statuses(tc.evaluate_text(lines, max_line_chars=14))["text_length"] == "PASS"


def test_title_overlap_finds_shared_content_words():
    checks = tc.evaluate_text(["ACME'S", "LOSSES"], "Why Acme Took a $3 Billion Loss")
    overlap = next(c for c in checks if c["name"] == "title_overlap")
    assert overlap["status"] == "WARN"
    assert "acme" in overlap["detail"] and "losses" in overlap["detail"]
    assert "repeats the title" in overlap["detail"]


def test_title_overlap_ignores_function_words_and_short_words():
    assert tc.shared_words("What This Deal Did to the Bank", "WHAT THIS MEANT") == []
    assert tc.shared_words("Why Did It Fail?", "WHY IT DID") == []
    assert tc.shared_words("The 1987 Crash", "1987 AGAIN") == ["1987"]


def test_title_overlap_word_for_word():
    checks = tc.evaluate_text(["ONE WEEK"], "Acme Lost Everything in One Week")
    overlap = next(c for c in checks if c["name"] == "title_overlap")
    assert overlap["status"] == "WARN" and "word for word" in overlap["detail"]


def test_split_lines():
    assert tc.split_lines("SOLD AT| THE TOP |") == ["SOLD AT", "THE TOP"]
    assert tc.split_lines(["A", " ", "B"]) == ["A", "B"]
    assert tc.split_lines(None) == []
    with pytest.raises(tc.UsageError):
        tc.split_lines([1, 2])


# ------------------------------------------------------------------------------------------- end to end

def make_image(path: Path, source: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", source,
                    "-frames:v", "1", str(path)], check=True, timeout=60)
    return path


def blue(size="1280x720"):
    return f"color=c=0x3366cc:s={size}"


def write_upload_json(project: Path, **fields):
    meta = {"title": "How Acme Lost $3 Billion in One Week", "thumbnail": "thumbnail.jpg"}
    meta.update(fields)
    (project / "upload.json").write_text(json.dumps(meta), encoding="utf-8")


def read_report(project: Path) -> dict:
    return json.loads((project / "qa" / "thumbnail-check.json").read_text(encoding="utf-8"))


@needs_ffmpeg
def test_project_run_reads_upload_json_and_writes_qa(tmp_path, capsys):
    project = tmp_path / "my video"                      # a space in the path, as on many Windows machines
    thumb = make_image(project / "thumbnail.jpg", blue())
    write_upload_json(project, thumbnail_text=["SOLD AT", "THE TOP"])
    assert tc.main(["--project", str(project)]) == 0
    report = read_report(project)
    assert report["verdict"] == "PASS"
    assert report["thumbnail"] == "thumbnail.jpg"
    assert report["sha256"] == hashlib.sha256(thumb.read_bytes()).hexdigest()
    assert report["preview"] == "qa/thumbnail-preview.png"
    cand = report["candidates"][0]
    assert cand["text"] == ["SOLD AT", "THE TOP"]
    assert statuses(cand["checks"])["title_overlap"] == "PASS"
    assert str(tmp_path) not in json.dumps(report)      # no absolute paths in the report
    out = capsys.readouterr().out
    assert "VERDICT: PASS" in out and "Preview: qa/thumbnail-preview.png" in out


@needs_ffmpeg
def test_text_that_repeats_the_title_warns_but_passes(tmp_path):
    make_image(tmp_path / "thumbnail.jpg", blue())
    write_upload_json(tmp_path, thumbnail_text=["ACME LOST", "$3 BILLION"])
    assert tc.main(["--project", str(tmp_path), "--no-preview"]) == 0
    cand = read_report(tmp_path)["candidates"][0]
    assert statuses(cand["checks"])["title_overlap"] == "WARN"


@needs_ffmpeg
def test_candidates_side_by_side_with_preview(tmp_path, capsys):
    a = make_image(tmp_path / "thumbs" / "a.jpg", blue())
    b = make_image(tmp_path / "thumbs" / "b.png", "color=c=black:s=1280x720")
    c = make_image(tmp_path / "thumbs" / "c.png",
                   "nullsrc=s=1280x720,format=rgb24,geq=r='X*255/W':g='Y*255/H':b='128'")
    write_upload_json(tmp_path)                           # upload.json one level up: the project is tmp_path
    code = tc.main([str(a), str(b), str(c), "--text", "SOLD AT|THE TOP"])
    assert code == 0
    report = read_report(tmp_path)
    by_name = {Path(x["image"]).name: statuses(x["checks"]) for x in report["candidates"]}
    assert by_name["b.png"]["dark_area"] == "WARN"
    assert by_name["c.png"]["clutter"] == "WARN"
    assert all(x["text"] == ["SOLD AT", "THE TOP"] for x in report["candidates"])
    assert report["thumbnail"] is None                    # none of these is upload.json's thumbnail
    out = capsys.readouterr().out
    assert "3 candidates side by side" in out and "a.jpg" in out and "c.png" in out

    preview = tmp_path / "qa" / "thumbnail-preview.png"
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                            str(preview)], capture_output=True, text=True, check=True).stdout.strip()
    width, height = (int(v) for v in probe.split(","))
    pad = tc.PREVIEW_PAD
    assert width == sum(w + 2 * pad for w in tc.FEED_WIDTHS)
    assert height == 2 * 3 * (round(max(tc.FEED_WIDTHS) * 9 / 16) + 2 * pad)   # 3 rows on dark + 3 on light


@needs_ffmpeg
def test_one_text_per_candidate(tmp_path):
    a = make_image(tmp_path / "a.jpg", blue())
    b = make_image(tmp_path / "b.jpg", blue())
    assert tc.main([str(a), str(b), "--text", "OK", "--text", "ONE|TWO|THREE|FOUR", "--no-preview"]) == 0
    cands = read_report(tmp_path)["candidates"]
    assert statuses(cands[0]["checks"])["text_lines"] == "PASS"
    assert statuses(cands[1]["checks"])["text_lines"] == "WARN"


@needs_ffmpeg
def test_failing_candidate_fails_the_run(tmp_path, capsys):
    good = make_image(tmp_path / "good.jpg", blue())
    small = make_image(tmp_path / "small.jpg", blue("600x338"))
    square = make_image(tmp_path / "square.png", blue("720x720"))
    fake = tmp_path / "fake.jpg"
    shutil.copy(square, fake)                             # PNG bytes behind a .jpg name
    assert tc.main([str(good), str(small), str(fake), "--no-preview", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    verdicts = {Path(x["image"]).name: x["verdict"] for x in report["candidates"]}
    assert verdicts == {"good.jpg": "PASS", "small.jpg": "FAIL", "fake.jpg": "FAIL"}
    fake_checks = statuses(next(x for x in report["candidates"] if x["image"] == "fake.jpg")["checks"])
    assert fake_checks["format"] == "FAIL" and fake_checks["aspect"] == "FAIL"
    assert report["verdict"] == "FAIL"


@needs_ffmpeg
def test_transparent_png_and_unreadable_file(tmp_path):
    alpha = make_image(tmp_path / "alpha.png", "color=c=red@0.5:s=1280x720,format=rgba")
    broken = tmp_path / "broken.jpg"
    broken.write_bytes(b"not an image at all")
    assert tc.main([str(alpha), str(broken)]) == 1
    report = read_report(tmp_path)
    by_name = {x["image"]: x for x in report["candidates"]}
    assert statuses(by_name["alpha.png"]["checks"])["transparency"] == "WARN"
    assert by_name["broken.jpg"]["checks"][0]["name"] == "format"
    assert by_name["broken.jpg"]["verdict"] == "FAIL"
    assert report["preview"] == "qa/thumbnail-preview.png"   # the readable image is still previewed


@needs_ffmpeg
def test_custom_output_paths(tmp_path):
    img = make_image(tmp_path / "t.jpg", blue())
    out, prev = tmp_path / "elsewhere" / "r.json", tmp_path / "elsewhere" / "p.png"
    assert tc.main([str(img), "--out", str(out), "--preview", str(prev)]) == 0
    assert out.is_file() and prev.is_file()
    assert not (tmp_path / "qa").exists()


def test_usage_errors(tmp_path, capsys):
    assert tc.main([str(tmp_path / "missing.jpg")]) == 2
    assert tc.main([]) == 2
    (tmp_path / "upload.json").write_text(json.dumps({"title": "x"}), encoding="utf-8")
    assert tc.main(["--project", str(tmp_path)]) == 2            # no image and no thumbnail in upload.json
    (tmp_path / "upload.json").write_text("{broken", encoding="utf-8")
    assert tc.main(["--project", str(tmp_path)]) == 2
    assert tc.main(["--project", str(tmp_path / "nope")]) == 2
    assert tc.main(["--dark-max", "0", "--project", str(tmp_path)]) == 2
    assert "error" in capsys.readouterr().err


@needs_ffmpeg
def test_text_count_must_match_images(tmp_path):
    a = make_image(tmp_path / "a.jpg", blue())
    b = make_image(tmp_path / "b.jpg", blue())
    c = make_image(tmp_path / "c.jpg", blue())
    assert tc.main([str(a), str(b), str(c), "--text", "A", "--text", "B"]) == 2


@needs_ffmpeg
def test_bad_thumbnail_text_type_is_a_usage_error(tmp_path):
    make_image(tmp_path / "thumbnail.jpg", blue())
    write_upload_json(tmp_path, thumbnail_text=[1, 2])
    assert tc.main(["--project", str(tmp_path)]) == 2
