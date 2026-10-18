"""Tests for skills/retention-by-scene/scripts/retention.py. No network: the fetchers are replaced."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "retention-by-scene" / "scripts" / "retention.py"
VIDEO_ID = "abcdefghijk"


def _load():
    spec = importlib.util.spec_from_file_location("tubecrew_retention", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rt = _load()


def synthetic_curve():
    """100 points over a 200 s video (one every 2 s): steep opening to 30 s, a cliff at 118-124 s, a rewatch bump
    at 158-164 s, a gentle slide everywhere else."""
    points, v = [], 1.0
    for i in range(1, 101):
        if i <= 15:
            v -= 0.02
        elif 60 <= i <= 62:
            v -= 0.05
        elif 80 <= i <= 82:
            v += 0.01
        else:
            v -= 0.002
        points.append((i / 100, round(v, 6)))
    return points


def report_from(points, relative=None):
    headers = [{"name": "audienceWatchRatio"}, {"name": "elapsedVideoTimeRatio"}]
    if relative is not None:
        headers.append({"name": "relativeRetentionPerformance"})
    rows = []
    for k, (x, y) in enumerate(points):
        row = [y, x]
        if relative is not None:
            row.append(relative[k])
        rows.append(row)
    return {"columnHeaders": headers, "rows": rows}


VIDEO = {
    "snippet": {"title": "A test video", "publishedAt": "2026-10-01T12:00:00Z",
                "description": "Intro text\n\n0:00 Cold open\n0:30 Setup\n1:50 The turn\n2:10 Ending"},
    "contentDetails": {"duration": "PT3M20S"},
    "statistics": {"viewCount": "1234"},
}

SRT = """﻿1
00:00:00,000 --> 00:00:04,000
Here is the <i>cold</i> open.

2
00:01:56,500 --> 00:02:00.000
So that was the setup, and now a long aside.

3
00:02:30,000 --> 00:02:34,000
Back to the story.
"""


# --- pure helpers -------------------------------------------------------------------------------------------------

def test_parse_iso_duration():
    assert rt.parse_iso_duration("PT8M12S") == 492
    assert rt.parse_iso_duration("PT1H2M3S") == 3723
    assert rt.parse_iso_duration("PT45S") == 45
    assert rt.parse_iso_duration("PT2H") == 7200
    assert rt.parse_iso_duration("P1DT1S") == 86401
    assert rt.parse_iso_duration("P0D") == 0
    for bad in ("PT", "P", "", "8:12", "P1DT", None):
        with pytest.raises(ValueError):
            rt.parse_iso_duration(bad)


def test_curve_points_reads_columns_by_name_and_skips_bad_rows():
    report = {"columnHeaders": [{"name": "audienceWatchRatio"}, {"name": "elapsedVideoTimeRatio"}],
              "rows": [[0.5, 0.02], [0.9, 0.01], ["x", 0.03], [True, 0.04], [0.4]]}
    assert rt.curve_points(report, "audienceWatchRatio") == [(0.01, 0.9), (0.02, 0.5)]
    assert rt.curve_points(report, "relativeRetentionPerformance") == []
    assert rt.curve_points({}, "audienceWatchRatio") == []


def test_interpolation_and_marks():
    points = [(0.0, 1.0), (0.5, 0.5), (1.0, 0.25)]
    assert rt.ratio_to_seconds(0.25, 200) == 50
    assert rt.value_at(points, 25, 100) == pytest.approx(0.75)
    assert rt.value_at(points, 75, 100) == pytest.approx(0.375)
    assert rt.value_at(points, 101, 100) is None
    assert rt.value_at([], 5, 100) is None
    assert rt.value_at([(0.1, 0.9), (1.0, 0.1)], 2, 100) == 0.9  # before the first point: the first value
    marks = rt.retention_marks(points, 50)
    assert list(marks) == [5, 10, 15, 20, 30, 45]
    assert rt.opening_loss(points, 20) is None  # shorter than 30 s
    assert rt.opening_loss(points, 100) == pytest.approx(1.0 - 0.7)


def test_drops_and_bumps_on_synthetic_curve():
    points = synthetic_curve()
    duration = 200.0
    drops = rt.steepest_drops(points, duration)
    assert len(drops) == 5
    cliff = drops[0]
    assert cliff["start"] == pytest.approx(118) and cliff["end"] == pytest.approx(124)
    assert cliff["loss_pp"] == pytest.approx(15, abs=0.01)
    assert cliff["per_second_pp"] == pytest.approx(2.5, abs=0.01)
    assert all(d["start"] >= 30 for d in drops)  # the opening is reported separately
    bumps = rt.rewatch_bumps(points, duration)
    assert len(bumps) == 1
    assert bumps[0]["start"] == pytest.approx(158) and bumps[0]["end"] == pytest.approx(164)
    assert bumps[0]["rise_pp"] == pytest.approx(3, abs=0.01) and bumps[0]["label"] == "clip/Short candidate"
    assert rt.rewatch_bumps([(0.1, 0.5), (0.2, 0.503), (0.3, 0.4)], 100) == []  # 0.3 points is noise
    assert rt.opening_loss(points, duration) == pytest.approx(0.98 - 0.70)
    assert rt.retention_marks(points, duration)[5] == pytest.approx(0.95)


def test_chapter_parsing_from_description():
    sections = rt.chapter_sections(VIDEO["snippet"]["description"], 200)
    assert [(s["start"], s["end"], s["text"]) for s in sections] == [
        (0, 30, "Cold open"), (30, 110, "Setup"), (110, 130, "The turn"), (130, 200, "Ending")]
    late = rt.chapter_sections("0:10 First\n1:00 Second", 120)
    assert late[0]["text"] == "(before the first chapter)" and late[0]["end"] == 10
    assert rt.chapter_sections("0:00 Only one", 120) == []
    assert rt.chapter_sections("no chapters", 120) == []
    assert rt.parse_chapters("1:02:03 Late\n0:75 bad") == [(3723, "Late")]


def test_srt_parsing_and_windows():
    cues = rt.parse_srt(SRT)
    assert len(cues) == 3
    assert cues[0] == {"start": 0.0, "end": 4.0, "text": "Here is the cold open."}
    assert cues[1]["start"] == pytest.approx(116.5) and cues[1]["end"] == pytest.approx(120.0)
    windows = rt.srt_windows(cues, 200)
    assert [w["start"] for w in windows] == [0.0, 116.5, 150.0]
    assert windows[-1]["end"] == 200
    assert rt.quotes_near(cues, 118, 124) == ["So that was the setup, and now a long aside."]
    assert rt.quotes_near(cues, 60, 70) == []
    assert rt.parse_srt("") == []


def test_timeline_validation():
    sections = rt.load_timeline([{"id": "b", "start": 30, "end": 60}, {"id": "a", "start": 0, "end": 30,
                                                                       "text": "open"}])
    assert [s["id"] for s in sections] == ["a", "b"] and sections[1]["text"] == ""
    for bad in ([], {"id": "a"}, [{"id": "a", "start": 10, "end": 5}], [{"id": "a", "start": "0", "end": 5}]):
        with pytest.raises(ValueError):
            rt.load_timeline(bad)


def test_section_ranking():
    points = synthetic_curve()
    sections = rt.chapter_sections(VIDEO["snippet"]["description"], 200)
    ranked = rt.rank_sections(rt.section_losses(points, 200, sections))
    assert [r["text"] for r in ranked][:2] == ["Cold open", "The turn"]
    assert [r["rank"] for r in ranked] == [1, 2, 3, 4]
    assert ranked[0]["opening"] is True and ranked[1]["opening"] is False
    turn = ranked[1]
    assert turn["loss_pp"] == pytest.approx(turn["at_start_pct"] - turn["at_end_pct"])
    assert turn["loss_per_min_pp"] == pytest.approx(turn["loss_pp"] / 20 * 60)
    tiny = rt.section_losses(points, 200, [{"id": "t", "start": 100, "end": 101, "text": ""}])
    assert tiny[0]["coarse"] is True


def test_fixed_windows():
    windows = rt.fixed_windows(75)
    assert [(w["start"], w["end"]) for w in windows] == [(0, 30), (30, 60), (60, 75)]


def test_analyze_and_render():
    points = synthetic_curve()
    relative = [0.42] * len(points)
    totals = {"columnHeaders": [{"name": "views"}, {"name": "averageViewDuration"},
                                {"name": "averageViewPercentage"}], "rows": [[1100, 95, 47.5]]}
    cues = rt.parse_srt(SRT)
    sections = rt.chapter_sections(VIDEO["snippet"]["description"], 200)
    report = rt.analyze(VIDEO_ID, VIDEO, report_from(points, relative), totals, sections, "description chapters",
                        cues)
    assert report["duration_s"] == 200 and report["views_data_api"] == 1234
    assert report["analytics"] == {"views": 1100, "average_view_duration_s": 95, "average_view_percentage": 47.5}
    assert report["relative_retention"] == {"10": 0.42, "30": 0.42}
    assert report["opening"]["loss_pp"] == pytest.approx(28.0)
    assert report["steepest_drops"][0]["said"] == ["So that was the setup, and now a long aside."]
    text = rt.render_text(report)
    assert "autoplay previews" in text and "STEEPEST DROPS AFTER 30 s" in text and "The turn" in text
    no_relative = rt.analyze(VIDEO_ID, VIDEO, report_from(points), totals, sections, "x")
    assert no_relative["relative_retention"] is None


# --- main with mocked fetchers ------------------------------------------------------------------------------------

class FakeCred:
    token = "fake-access-token"


def _mock_api(monkeypatch, curve, totals=None):
    monkeypatch.setattr(rt, "load_credentials", lambda *a, **k: FakeCred())
    monkeypatch.setattr(rt, "new_session", lambda: object())
    monkeypatch.setattr(rt, "fetch_video_info", lambda session, token, video_id: VIDEO)
    monkeypatch.setattr(rt, "fetch_curve", lambda session, token, video_id, start, end: curve)

    def fake_totals(session, token, video_id, start, end):
        if totals is None:
            raise AssertionError("totals should not be fetched")
        return totals

    monkeypatch.setattr(rt, "fetch_totals", fake_totals)


def test_no_rows_path(monkeypatch, capsys):
    _mock_api(monkeypatch, {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}], "rows": []})
    assert rt.main([VIDEO_ID]) == 0
    assert capsys.readouterr().out.strip() == rt.NO_DATA
    _mock_api(monkeypatch, {"columnHeaders": []})
    assert rt.main([VIDEO_ID, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"video_id": VIDEO_ID, "data": False, "message": rt.NO_DATA}


def test_full_run_writes_out_without_absolute_paths(monkeypatch, capsys, tmp_path):
    totals = {"columnHeaders": [{"name": "views"}], "rows": [[50]]}
    _mock_api(monkeypatch, report_from(synthetic_curve(), [0.5] * 100), totals)
    srt = tmp_path / "captions.srt"
    srt.write_text(SRT, encoding="utf-8")
    out = tmp_path / "report.json"
    assert rt.main([VIDEO_ID, "--srt", str(srt), "--json", "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    written = out.read_text(encoding="utf-8")
    assert json.loads(written) == printed
    assert printed["sections_source"] == "description chapters"
    assert str(tmp_path) not in written
    text_out = tmp_path / "report.txt"
    assert rt.main([VIDEO_ID, "--srt", str(srt), "--out", str(text_out)]) == 0
    assert "SECTIONS (from description chapters)" in text_out.read_text(encoding="utf-8")


def test_usage_errors(capsys, tmp_path):
    assert rt.main([]) == 2
    assert rt.main(["not-a-video-id"]) == 2
    assert rt.main([VIDEO_ID, "--profile", "../escape"]) == 2
    bad = tmp_path / "timeline.json"
    bad.write_text("[{\"id\": \"a\"}]", encoding="utf-8")
    assert rt.main([VIDEO_ID, "--timeline", str(bad)]) == 2


def test_missing_token_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TUBECREW_HOME", str(tmp_path))
    monkeypatch.setattr(rt, "new_session", lambda: (_ for _ in ()).throw(AssertionError("network")))
    assert rt.main([VIDEO_ID, "--profile", "mychannel"]) == 2
    err = capsys.readouterr().err
    assert "--login --profile mychannel" in err or "pip install" in err


def test_start_date_covers_the_pacific_publish_day(monkeypatch, capsys):
    # Analytics dates are Pacific time: 03:00 UTC on Oct 1 is the evening of Sep 30 there.
    assert rt.analytics_start("2026-10-01T03:00:00Z") == "2026-09-30"
    assert rt.analytics_start(None) is None and rt.analytics_start("soon") is None
    early = {**VIDEO, "snippet": {**VIDEO["snippet"], "publishedAt": "2026-10-01T03:00:00Z"}}
    starts = []
    _mock_api(monkeypatch, {"rows": []})
    monkeypatch.setattr(rt, "fetch_video_info", lambda session, token, video_id: early)
    monkeypatch.setattr(rt, "fetch_curve", lambda session, token, video_id, start, end: starts.append(start) or {})
    assert rt.main([VIDEO_ID]) == 0
    assert starts == ["2026-09-30"]


def test_fetch_curve_falls_back_without_relative_metric():
    class Response:
        def __init__(self, status, body):
            self.status_code, self._body, self.text = status, body, json.dumps(body)

        def json(self):
            return self._body

    class Session:
        def __init__(self):
            self.metrics = []

        def get(self, url, params=None, headers=None, timeout=None):
            self.metrics.append(params["metrics"])
            if "relativeRetentionPerformance" in params["metrics"]:
                return Response(400, {"error": "unsupported"})
            return Response(200, {"rows": [[0.01, 1.0]]})

    session = Session()
    assert rt.fetch_curve(session, "tok", VIDEO_ID, "2026-10-01", "2026-10-18") == {"rows": [[0.01, 1.0]]}
    assert session.metrics == ["audienceWatchRatio,relativeRetentionPerformance", "audienceWatchRatio"]
