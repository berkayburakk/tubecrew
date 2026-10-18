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


def test_approximate_age_is_a_lower_bound():
    # yt-dlp turns "1 year ago" (12-23 months) into exactly one year; that is already old evidence
    assert out.age_label(365, approx=True) == "old"
    assert out.age_label(365) == "" and out.age_label(364, approx=True) == ""
    year_ago = (TODAY - dt.timedelta(days=365)).isoformat()
    catalog = {"C": [{"id": "y", "title": "", "views": 900, "upload_date": year_ago, "date_approx": True}]
               + [{"id": f"x{i}", "title": "", "views": 100} for i in range(4)]}
    outliers, _ = out.find_outliers(catalog, 2.0, TODAY)
    assert outliers[0]["age_label"] == "old"


def test_normalize_handle_with_tab():
    assert out.normalize_channel_url("@acme/videos") == "https://www.youtube.com/@acme/videos"
    assert out.normalize_channel_url("@acme/shorts") == "https://www.youtube.com/@acme/videos"


def test_live_pull_replaces_the_same_channel_from_a_saved_catalog(tmp_path, monkeypatch, capsys):
    saved = tmp_path / "catalog.json"
    old = [{"id": f"old{i}", "title": "", "views": v} for i, v in enumerate([100, 100, 100, 100, 900])]
    saved.write_text(json.dumps({"Acme Science": old, "Example Lab": old}), encoding="utf-8")
    monkeypatch.setattr(out, "ytdlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(out, "_run_json", lambda cmd, timeout=600: FLAT)
    rc = out.main(["--from-json", str(saved), "@acmescience", "--dates", "0", "--json", "--save", str(saved)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert sorted(json.loads(saved.read_text(encoding="utf-8"))) == ["Acme Science", "Example Lab"]  # no "(2)"
    acme = [o["id"] for o in data["outliers"] if o["channel"].startswith("Acme Science")]
    assert acme == ["vid00000001"]  # the fresh pull, listed once


def test_catalog_with_bom_loads_and_bad_views_are_a_usage_error(tmp_path, capsys):
    good = tmp_path / "bom.json"
    good.write_bytes(b"\xef\xbb\xbf" + json.dumps({"C": [{"id": "a", "views": 5}]}).encode("utf-8"))
    assert out.main(["--from-json", str(good)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"C": [{"id": "a", "views": "1,000"}]}), encoding="utf-8")
    assert out.main(["--from-json", str(bad)]) == 2
    bad.write_text(json.dumps({"C": [{"title": "no id", "views": 10}]}), encoding="utf-8")
    assert out.main(["--from-json", str(bad)]) == 2
    assert "without an id" in capsys.readouterr().err


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


def test_like_check_threshold():
    assert out.like_check(None, 1000) == {} and out.like_check(5, 0) == {}
    low = out.like_check(30, 10_000)  # 0.3 %
    assert low == {"likes": 30, "like_view_pct": 0.3, "ad_pushed": True}
    assert out.like_check(35, 10_000)["ad_pushed"] is False  # exactly 0.35 % is not under it
    assert out.like_check(400, 10_000)["ad_pushed"] is False
    assert out.parse_video_engagement({"like_count": 12, "view_count": 3400}) == (12, 3400)
    assert out.parse_video_engagement({"like_count": None}) == (None, None)


def test_catalog_likes_flag_ad_pushed_outliers(tmp_path, capsys):
    catalog = {"Acme Science": [
        {"id": "push", "title": "Pushed one", "views": 50_000, "likes": 60},      # 0.12 %
        {"id": "real", "title": "Organic one", "views": 40_000, "likes": 1_600},  # 4 %
        {"id": "nolk", "title": "No likes known", "views": 30_000},
    ] + [{"id": f"x{i}", "title": "", "views": 1_000} for i in range(4)]}
    outliers, _ = out.find_outliers(catalog, 2.0, TODAY)
    by_id = {o["id"]: o for o in outliers}
    assert by_id["push"]["ad_pushed"] is True and by_id["push"]["like_view_pct"] == 0.12
    assert by_id["real"]["ad_pushed"] is False
    assert "ad_pushed" not in by_id["nolk"]
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    assert out.main(["--from-json", str(path)]) == 0
    text = capsys.readouterr().out
    assert text.count("possibly ad-pushed: not evidence (likes 0.12% of views") == 1
    assert "likes checked for 2 of 3 outliers, 1 possibly ad-pushed" in text
    path.write_text(json.dumps({"C": [{"id": "a", "views": 5, "likes": "12"}]}), encoding="utf-8")
    assert out.main(["--from-json", str(path)]) == 2


def test_flat_entry_like_count_is_kept():
    data = {"channel": "Acme Science", "entries": [_entry("vid00000009", "Liked", 5000, like_count=40)]}
    _, videos = out.parse_flat_playlist(data)
    assert videos[0]["likes"] == 40


def test_dates_fetch_reads_likes_without_extra_requests(monkeypatch, capsys, tmp_path):
    calls = []

    def fake_run_json(cmd, timeout=600):
        calls.append(cmd)
        if "--flat-playlist" in cmd:
            return FLAT
        return {"upload_date": "20260520", "like_count": 1500, "view_count": 951_234}  # 0.158 %

    monkeypatch.setattr(out, "ytdlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(out, "_run_json", fake_run_json)
    saved = tmp_path / "catalog.json"
    rc = out.main(["@acmescience", "--dates", "1", "--json", "--save", str(saved)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and len(calls) == 2  # one catalog pull, one full read that gives date and likes together
    top = data["outliers"][0]
    assert top["ad_pushed"] is True and top["likes"] == 1500 and top["like_view_pct"] == 0.158  # exact views used
    assert data["ad_push_pct"] == 0.35
    first = json.loads(saved.read_text(encoding="utf-8"))["Acme Science"][0]
    assert first["likes"] == 1500 and first["upload_date"] == "2026-05-20"


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
