"""Tests for skills/find-topic/scripts/outliers.py (synthetic data only, no network)."""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "find-topic" / "scripts" / "outliers.py"


def _load():
    spec = importlib.util.spec_from_file_location("outliers", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


out = _load()
TODAY = dt.date(2026, 6, 1)


def _entry(vid, title, views, duration=600, timestamp=None, **extra):
    """One entry shaped like `yt-dlp --flat-playlist -J` output for a channel's videos tab."""
    e = {
        "_type": "url", "ie_key": "Youtube", "id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
        "title": title, "duration": duration, "view_count": views, "timestamp": timestamp,
        "thumbnails": [{"url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "height": 202, "width": 360}],
        "live_status": None, "availability": None, "channel_url": None, "uploader_url": None,
    }
    e.update(extra)
    return e


def _epoch(d: dt.date) -> int:
    return int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp())


FLAT = {
    "_type": "playlist", "id": "UCxxxxxxxxxxxxxxxxxxxxxx", "title": "Acme Science - Videos",
    "channel": "Acme Science", "channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx", "uploader": "Acme Science",
    "uploader_id": "@acmescience", "channel_follower_count": 120000,
    "webpage_url": "https://www.youtube.com/@acmescience/videos", "extractor_key": "YoutubeTab",
    "entries": [
        _entry("vid00000001", "How bridges really fail", 950000, 1500, _epoch(TODAY - dt.timedelta(days=40))),
        _entry("vid00000002", "Why ships float", 100000, 900, _epoch(TODAY - dt.timedelta(days=60))),
        _entry("vid00000003", "The tiny screw that matters", 120000, 700),
        _entry("vid00000004", "Upcoming premiere", None, None, live_status="is_upcoming"),
        _entry("vid00000005", "Glass, explained", 80000, 800, upload_date="20250101"),
        _entry("vid00000006", "Rust never sleeps", 110000, 1000),
    ],
}


def test_median_basic():
    assert out.median([1, 3, 2]) == 2
    assert out.median([1, 2, 3, 4]) == 2.5
    assert out.median([]) is None


def test_parse_flat_playlist_shape():
    name, videos = out.parse_flat_playlist(FLAT)
    assert name == "Acme Science"
    ids = [v["id"] for v in videos]
    assert "vid00000004" not in ids  # no view count, upcoming
    assert len(videos) == 5
    first = videos[0]
    assert first["views"] == 950000 and first["duration"] == 1500 and first["title"] == "How bridges really fail"
    assert first["upload_date"] == (TODAY - dt.timedelta(days=40)).isoformat() and first["date_approx"] is True
    glass = next(v for v in videos if v["id"] == "vid00000005")
    assert glass["upload_date"] == "2025-01-01" and "date_approx" not in glass
    assert "upload_date" not in next(v for v in videos if v["id"] == "vid00000003")


def test_parse_flat_playlist_nested_tabs():
    nested = {"_type": "playlist", "channel": "Acme Science",
              "entries": [{"_type": "playlist", "title": "Videos", "entries": FLAT["entries"][:2]}]}
    name, videos = out.parse_flat_playlist(nested)
    assert [v["id"] for v in videos] == ["vid00000001", "vid00000002"]


def test_parse_video_metadata():
    assert out.parse_video_metadata({"upload_date": "20260315", "view_count": 5}) == "2026-03-15"
    assert out.parse_video_metadata({}) is None


def test_multiples_threshold_and_sort():
    catalog = {
        "Big Channel": [{"id": f"b{i}", "title": f"b{i}", "views": v} for i, v in
                        enumerate([1000, 1000, 1000, 1000, 5000, 2000, 1999])],
        "Small Channel": [{"id": f"s{i}", "title": f"s{i}", "views": v} for i, v in
                          enumerate([10, 10, 10, 10, 90])],
    }
    outliers, summaries = out.find_outliers(catalog, 2.0, TODAY)
    assert [o["id"] for o in outliers] == ["s4", "b4", "b5"]  # 9x, 5x, 2x; 1999 is just under 2x
    assert outliers[0]["multiple"] == 9.0 and outliers[0]["channel_median"] == 10
    big = next(s for s in summaries if s["channel"] == "Big Channel")
    assert big["n"] == 7 and big["median"] == 1000 and big["outliers"] == 2 and big["top_multiple"] == 5.0


def test_min_multiple_filter():
    catalog = {"C": [{"id": str(i), "title": "", "views": v} for i, v in enumerate([100, 100, 100, 100, 250, 400])]}
    ids3 = [o["id"] for o in out.find_outliers(catalog, 3.0, TODAY)[0]]
    ids2 = [o["id"] for o in out.find_outliers(catalog, 2.0, TODAY)[0]]
    assert ids3 == ["5"] and ids2 == ["5", "4"]


def test_thin_channel_skipped():
    catalog = {"Thin": [{"id": "a", "title": "", "views": 10}, {"id": "b", "title": "", "views": 1000}]}
    outliers, summaries = out.find_outliers(catalog, 2.0, TODAY)
    assert outliers == []
    assert summaries[0]["skipped"] and "a median of 2 videos is not a median" in summaries[0]["skipped"]


def test_zero_median_skipped():
    catalog = {"Z": [{"id": str(i), "title": "", "views": v} for i, v in enumerate([0, 0, 0, 0, 50])]}
    outliers, summaries = out.find_outliers(catalog, 2.0, TODAY)
    assert outliers == [] and "median" in summaries[0]["skipped"]


def test_age_labels():
    assert out.age_label(None) == "no date"
    assert out.age_label(3) == "too new"
    assert out.age_label(30) == ""
    assert out.age_label(400) == "old"
    assert out.age_days("2026-05-29", TODAY) == 3
    catalog = {"C": [
        {"id": "new", "title": "", "views": 900, "upload_date": "2026-05-30"},
        {"id": "old", "title": "", "views": 800, "upload_date": "20240101"},
        {"id": "x1", "title": "", "views": 100}, {"id": "x2", "title": "", "views": 100},
        {"id": "x3", "title": "", "views": 100}, {"id": "x4", "title": "", "views": 100},
    ]}
    outliers, _ = out.find_outliers(catalog, 2.0, TODAY)
    labels = {o["id"]: o["age_label"] for o in outliers}
    assert labels == {"new": "too new", "old": "old"}


def test_normalize_channel_url():
    assert out.normalize_channel_url("@acme") == "https://www.youtube.com/@acme/videos"
    assert out.normalize_channel_url("https://www.youtube.com/@acme") == "https://www.youtube.com/@acme/videos"
    assert out.normalize_channel_url("https://www.youtube.com/@acme/shorts") == "https://www.youtube.com/@acme/videos"
    assert out.normalize_channel_url("https://www.youtube.com/@acme/videos/") == "https://www.youtube.com/@acme/videos"
    assert (out.normalize_channel_url("UCxxxxxxxxxxxxxxxxxxxxxx")
            == "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx/videos")
    with pytest.raises(out.UsageError):
        out.normalize_channel_url("not a channel")


def _write_catalog(tmp_path):
    catalog = {
        "_note": "synthetic",
        "Acme Science": [{"id": f"a{i}", "title": f"Video {i}", "views": v, "duration": 600}
                         for i, v in enumerate([1000, 1200, 900, 1100, 6000])],
        "Example Lab": [{"id": "e1", "title": "One", "views": 50}, {"id": "e2", "title": "Two", "views": 70}],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def test_main_from_json_human(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(out, "ytdlp_command", lambda: pytest.fail("network must not be used"))
    rc = out.main(["--from-json", str(_write_catalog(tmp_path))])
    text = capsys.readouterr().out
    assert rc == 0
    assert "5.5x" in text and "Video 4" in text and "https://www.youtube.com/watch?v=a4" in text
    assert "Example Lab: skipped, n=2" in text
    assert "Outliers are leads, not topics." in text


def test_main_from_json_json_and_save(tmp_path, capsys):
    saved = tmp_path / "sub" / "copy.json"
    rc = out.main(["--from-json", str(_write_catalog(tmp_path)), "--json", "--save", str(saved)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert [o["id"] for o in data["outliers"]] == ["a4"]
    assert data["outliers"][0]["channel_median"] == 1100 and data["outliers"][0]["channel_n"] == 5
    assert json.loads(saved.read_text(encoding="utf-8"))["Acme Science"][4]["views"] == 6000


def test_main_usage_errors(tmp_path, capsys):
    assert out.main([]) == 2
    assert out.main(["--from-json", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert out.main(["--from-json", str(bad)]) == 2


def test_main_live_path_with_fake_ytdlp(monkeypatch, capsys):
    calls = []

    def fake_run_json(cmd, timeout=600):
        calls.append(cmd)
        if "--flat-playlist" in cmd:
            return FLAT
        return {"upload_date": "20260520"}

    monkeypatch.setattr(out, "ytdlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(out, "_run_json", fake_run_json)
    rc = out.main(["@acmescience", "--dates", "1", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert calls[0][-1] == "https://www.youtube.com/@acmescience/videos" and "--playlist-end" in calls[0]
    top = data["outliers"][0]
    assert top["id"] == "vid00000001" and top["upload_date"] == "2026-05-20" and top["date_approx"] is False
    assert len(calls) == 2  # one catalog pull, one date lookup for the single top outlier
