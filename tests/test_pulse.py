"""Tests for skills/channel-pulse/scripts/pulse.py. No network: a fake session answers like the two APIs."""
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "channel-pulse" / "scripts" / "pulse.py"


def _load():
    spec = importlib.util.spec_from_file_location("tubecrew_pulse", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


pu = _load()
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc)


# --- sample guards and the daily-views shape ------------------------------------------------------------------------

def label(series, age=500.0, engaged=1000):
    return pu.classify_pattern(series, age, engaged)["label"]


def test_age_guard_boundary():
    assert label([100, 100, 100, 10, 10, 10], age=71.99) == "too_early"
    assert label([100, 100, 100, 10, 10, 10], age=72.0) != "too_early"
    assert pu.classify_pattern([], None, None)["label"] == "too_early"


def test_engaged_views_guard_and_never_tested_after_a_week():
    assert pu.MIN_ENGAGED_VIEWS == 100 and pu.MIN_AGE_HOURS == 72
    assert label([50] * 5, age=167.9, engaged=99) == "too_early"
    assert label([50] * 5, age=168.0, engaged=99) == "never_tested"
    assert label([50] * 5, age=100.0, engaged=100) == "still_growing"
    assert label([50] * 5, age=100.0, engaged=None) == "too_early"


def test_never_tested_peak_boundary():
    assert label([29, 20, 10, 5, 5, 5]) == "never_tested"
    assert label([30, 20, 10, 5, 5, 5]) != "never_tested"


def test_growing_boundary():
    assert label([100, 50, 60, 60, 60]) == "still_growing"      # last three days at exactly 60% of the best day
    assert label([100, 50, 59, 59, 59]) == "tapering"
    assert label([10, 20, 30, 40]) == "still_growing"           # the best day is one of the last days


def test_cut_boundaries():
    cut = pu.classify_pattern([100] * 5 + [25] * 3, 500.0, 1000)
    assert cut["label"] == "tested_and_cut" and cut["burst_days"] == 5 and cut["recent_mean"] == 25
    assert label([100] * 6 + [25] * 3) == "tapering"            # a 6-day run is a life cycle, not a stopped test
    assert label([100] * 5 + [26] * 3) == "tapering"            # above a quarter of the peak
    assert label([0] * 6 + [100, 100] + [10] * 3) == "tested_and_cut"   # burst starts on day 7 (index 6)
    assert label([0] * 7 + [100, 100] + [10] * 3) == "tapering"         # starts after the first week


def test_daily_series_zero_fills_missing_days():
    rows = [{"day": "2026-10-02", "views": 5}, {"day": "2026-10-04", "views": 7}, {"day": "bad", "views": 1}]
    assert pu.daily_series(rows, dt.date(2026, 10, 1), dt.date(2026, 10, 5)) == [0, 5, 0, 7, 0]
    assert pu.daily_series(rows, dt.date(2026, 10, 5), dt.date(2026, 10, 1)) == []


def test_content_type_and_time_helpers():
    assert pu.content_type("videoOnDemand") == "long" and pu.content_type("VIDEO_ON_DEMAND") == "long"
    assert pu.content_type("shorts") == "short" and pu.content_type("LIVE_STREAM") == "live"
    assert pu.content_type("story") == "other" and pu.content_type(None) is None
    assert pu.analytics_start("2026-10-01T03:00:00Z") == "2026-09-30"
    assert pu.hours_since("2026-10-17T12:00:00Z", NOW) == pytest.approx(24)
    assert pu.hours_since("soon", NOW) is None
    assert pu.hours_since("2026-10-17T12:00:00.5Z", NOW) == pytest.approx(24, abs=0.01)   # 1 fraction digit
    assert pu.parse_iso_duration("PT9M") == 540


# --- retention, traffic, audience -----------------------------------------------------------------------------------

def curve_report(relative=0.25, n=100):
    headers = [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}]
    if relative is not None:
        headers.append({"name": "relativeRetentionPerformance"})
    rows = []
    for i in range(1, n + 1):
        row = [i / n, round(1 - i * 0.005, 4)]
        if relative is not None:
            row.append(relative if i / n < 0.3 else 0.6)
        rows.append(row)
    return {"columnHeaders": headers, "rows": rows}


def test_retention_summary():
    s = pu.retention_summary(curve_report(), 300.0)        # 30 s = 10% of the runtime
    assert s["retention_30s_pct"] == pytest.approx(95.0, abs=0.1)
    assert s["opening_percentile"] == 0.25 and s["body_percentile"] == 0.6
    no_rel = pu.retention_summary(curve_report(relative=None), 300.0)
    assert no_rel["opening_percentile"] is None and no_rel["body_percentile"] is None
    assert pu.retention_summary({"rows": []}, 300.0) is None
    assert pu.retention_summary(curve_report(), 20.0)["retention_30s_pct"] is None   # shorter than 30 s


def test_traffic_summary_splits_long_from_shorts():
    rows = [{"insightTrafficSourceType": "SUBSCRIBER", "creatorContentType": "videoOnDemand", "views": 30},
            {"insightTrafficSourceType": "YT_CHANNEL", "creatorContentType": "videoOnDemand", "views": 70},
            {"insightTrafficSourceType": "SHORTS", "creatorContentType": "shorts", "views": 900}]
    t = pu.traffic_summary(rows)
    assert t["split_by_type"] is True
    assert t["groups"]["all"]["discovery_share"] == pytest.approx(0.93)
    assert t["groups"]["long"]["discovery_share"] == pytest.approx(0.30)
    basis = pu.discovery_basis(t)
    assert basis["group"] == "long" and basis["total"] == 100
    flat = pu.traffic_summary([{"insightTrafficSourceType": "SUBSCRIBER", "views": 10}])
    assert flat["split_by_type"] is False and pu.discovery_basis(flat)["group"] == "all"
    assert pu.discovery_basis({"groups": {}}) is None


def test_share_summary_privacy_threshold():
    one = pu.share_summary([{"ageGroup": "age25-34", "viewerPercentage": 100.0}], "ageGroup", pu.AGE_GROUPS)
    assert one["status"] == "below_threshold" and one["readable"] is False and one["shares"] == {}
    assert pu.share_summary([], "ageGroup", pu.AGE_GROUPS)["status"] == "no_data"
    part = pu.share_summary([{"ageGroup": "age25-34", "viewerPercentage": 60.0},
                             {"ageGroup": "age35-44", "viewerPercentage": 40.0}], "ageGroup", pu.AGE_GROUPS)
    assert part["status"] == "partial" and part["readable"] and "age65-" in part["missing"]
    full = pu.share_summary([{"ageGroup": g, "viewerPercentage": 100 / 7} for g in pu.AGE_GROUPS], "ageGroup",
                            pu.AGE_GROUPS)
    assert full["status"] == "complete"
    gender = pu.share_summary([{"gender": "female", "viewerPercentage": 30.0},
                               {"gender": "male", "viewerPercentage": 70.0}], "gender", pu.GENDERS)
    assert gender["status"] == "complete" and gender["shares"] == {"female": 30.0, "male": 70.0}


def test_device_summary_thresholds():
    rows = [{"deviceType": "MOBILE", "views": 100, "estimatedMinutesWatched": 100.0},
            {"deviceType": "TV", "views": 49, "estimatedMinutesWatched": 147.0}]
    d = pu.device_summary(rows)
    assert d["readable"] and d["tv_mobile_ratio"] is None and "TV 49" in d["ratio_missing_because"]
    rows[1] = {"deviceType": "TV", "views": 50, "estimatedMinutesWatched": 150.0}
    assert pu.device_summary(rows)["tv_mobile_ratio"] == 3.0
    assert pu.device_summary([{"deviceType": "MOBILE", "views": 49}])["readable"] is False


def test_search_terms_sorted():
    rows = [{"insightTrafficSourceDetail": "b term", "views": 3}, {"insightTrafficSourceDetail": "a term", "views": 3},
            {"insightTrafficSourceDetail": "top", "views": 9}, {"views": 4}]
    assert [t["term"] for t in pu.search_terms(rows)] == ["top", "a term", "b term"]


# --- the one diagnosis --------------------------------------------------------------------------------------------

def vid(lbl, title="A video", retention=None, **pattern):
    return {"id": "x", "title": title, "published_at": "2026-10-01T00:00:00Z", "age_hours": 400.0,
            "pattern": {"label": lbl, "peak": 400, "burst_days": 3, "recent_mean": 20.0, **pattern},
            "retention": retention, "top_source": "SUBSCRIBER"}


def traffic(long_total, share):
    disc = round(long_total * share)
    return pu.traffic_summary([
        {"insightTrafficSourceType": "SUBSCRIBER", "creatorContentType": "videoOnDemand", "views": disc},
        {"insightTrafficSourceType": "YT_CHANNEL", "creatorContentType": "videoOnDemand", "views": long_total - disc}])


HEALTHY_TOTALS = {"views": 500, "subscribers_gained": 5}


def test_diagnosis_too_early_names_the_date():
    young = {**vid("too_early"), "published_at": "2026-10-17T12:00:00Z", "age_hours": 24.0}
    d = pu.diagnose([young], traffic(100, 0.9), HEALTHY_TOTALS, 28)
    assert d["code"] == "too_early" and "2026-10-20" in d["message"]
    assert pu.diagnose([], {}, HEALTHY_TOTALS, 28)["code"] == "too_early"


def test_diagnosis_order_never_tested_then_cut():
    d = pu.diagnose([vid("never_tested"), vid("never_tested"), vid("tested_and_cut")], traffic(100, 0.1),
                    HEALTHY_TOTALS, 28)
    assert d["code"] == "never_tested" and d["needs_studio_ctr"] is True
    d = pu.diagnose([vid("tested_and_cut", title="Newest"), vid("tested_and_cut"), vid("still_growing")],
                    traffic(100, 0.1), HEALTHY_TOTALS, 28)
    assert d["code"] == "tested_and_cut" and "Newest" in d["message"] and "CTR" in d["message"]
    weak = vid("tested_and_cut", retention={"opening_percentile": 0.2, "body_percentile": 0.5})
    assert "opening is weak" in pu.diagnose([weak], traffic(100, 0.9), HEALTHY_TOTALS, 28)["message"]
    assert "one observation" in pu.diagnose([weak], traffic(100, 0.9), HEALTHY_TOTALS, 28)["message"]


def test_diagnosis_discovery_boundary():
    growing = [vid("still_growing")]
    assert pu.diagnose(growing, traffic(100, 0.30), HEALTHY_TOTALS, 28)["code"] == "healthy"
    low = pu.diagnose(growing, traffic(100, 0.29), HEALTHY_TOTALS, 28)
    assert low["code"] == "discovery_low" and "long videos" in low["title"]
    assert pu.diagnose(growing, traffic(49, 0.0), HEALTHY_TOTALS, 28)["code"] == "healthy"   # too few views


def test_diagnosis_opening_boundary():
    def run(opening, body):
        return pu.diagnose([vid("still_growing", retention={"opening_percentile": opening, "body_percentile": body})],
                           traffic(100, 0.9), HEALTHY_TOTALS, 28)["code"]
    assert run(0.30, 0.60) == "healthy"
    assert run(0.29, 0.44) == "opening_weak"
    assert run(0.29, 0.43) == "healthy"       # the body is weak too: not an opening problem alone


def test_diagnosis_subscriber_boundary():
    def run(views, subs):
        return pu.diagnose([vid("still_growing")], traffic(100, 0.9), {"views": views, "subscribers_gained": subs},
                           28)["code"]
    assert run(1000, 0) == "healthy"          # judged only above 1,000 views
    assert run(1001, 1) == "no_subscriber_growth"
    assert run(2000, 2) == "healthy"          # exactly 1 per 1,000


# --- the whole run against a fake API -----------------------------------------------------------------------------

def report(headers, rows):
    return {"columnHeaders": [{"name": h} for h in headers], "rows": rows}


def days(start, values):
    d0 = dt.date.fromisoformat(start)
    return [[(d0 + dt.timedelta(days=i)).isoformat(), v] for i, v in enumerate(values)]


VIDEOS = {
    "newvideo001": ("2026-10-17T12:00:00Z", "PT9M", "public"),
    "cutvideo002": ("2026-10-06T15:00:00Z", "PT9M", "public"),
    "quietvid003": ("2026-09-29T15:00:00Z", "PT8M", "public"),
    "cutvideo004": ("2026-09-22T15:00:00Z", "PT10M", "public"),
    "privvideo05": ("2026-10-10T15:00:00Z", "PT5M", "private"),
}
DAILY = {
    "cutvideo002": days("2026-10-06", [300, 420, 380, 60, 40, 30, 20, 20, 20, 20, 20]),
    "quietvid003": days("2026-09-29", [12, 8, 5, 3]),
    "cutvideo004": days("2026-09-22", [200, 250, 50, 20, 10]),
}
TOTALS = {"cutvideo002": [1400, 900, 41.5, 220.0, 6, 3000.0], "quietvid003": [45, 40, 30.0, 140.0, 0, 90.0],
          "cutvideo004": [560, 450, 38.0, 230.0, 2, 1600.0]}


class Response:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


class FakeYouTube:
    def __init__(self, fail_channel=False):
        self.calls, self.fail_channel = [], fail_channel

    def get(self, url, params=None, headers=None, timeout=None):
        assert headers["Authorization"] == "Bearer fake-access-token"
        self.calls.append((url, dict(params)))
        status, body = self.answer(url, params)
        return Response(status, body)

    def answer(self, url, p):
        if url.endswith("/channels"):
            if self.fail_channel:
                return 500, {"error": "backend"}
            return 200, {"items": [{"id": "UCxxxxxxxxxxxxxxxxxxxxxx", "snippet": {"title": "Example Channel"},
                                    "statistics": {"subscriberCount": "1200", "videoCount": "40"},
                                    "contentDetails": {"relatedPlaylists": {"uploads": "UUxxxxxxxxxxxxxxxxxxxxxx"}}}]}
        if url.endswith("/playlistItems"):
            return 200, {"items": [{"contentDetails": {"videoId": v}} for v in VIDEOS]}
        if url.endswith("/videos"):
            items = [{"id": v, "snippet": {"title": f"Title of {v}", "publishedAt": VIDEOS[v][0]},
                      "contentDetails": {"duration": VIDEOS[v][1]}, "statistics": {"viewCount": "1500"},
                      "status": {"privacyStatus": VIDEOS[v][2]}} for v in p["id"].split(",")]
            return 200, {"items": items}
        assert url == pu.ANALYTICS_API and p["ids"] == "channel==MINE"
        dims, filt, metrics = p.get("dimensions"), p.get("filters", ""), p["metrics"]
        if dims == "insightTrafficSourceDetail":
            assert p["sort"] == "-views" and p["maxResults"] <= 25 and filt.startswith("insightTrafficSourceType==YT_SEARCH")
            if ";video==" in filt:
                return 200, report(["insightTrafficSourceDetail", "views", "estimatedMinutesWatched"],
                                   [["how a thing works", 12, 40.0]])
            return 200, report(["insightTrafficSourceDetail", "views", "estimatedMinutesWatched"],
                               [["thing explained", 40, 120.0], ["what is a thing", 25, 60.0]])
        if not filt:
            if dims == "day":
                return 200, report(["day", "views", "engagedViews", "estimatedMinutesWatched", "subscribersGained"],
                                   [r + [80, 300.0, 1] for r in days("2026-09-21", [100] * 26)])
            if dims.startswith("insightTrafficSourceType"):
                return 200, report(["insightTrafficSourceType", "creatorContentType", "views", "estimatedMinutesWatched"],
                                   [["SUBSCRIBER", "videoOnDemand", 1500, 4000.0],
                                    ["RELATED_VIDEO", "videoOnDemand", 300, 800.0],
                                    ["YT_CHANNEL", "videoOnDemand", 400, 900.0],
                                    ["SHORTS", "shorts", 400, 100.0]])
            if dims == "ageGroup":
                return 200, report(["ageGroup", "viewerPercentage"], [["age25-34", 100.0]])
            if dims == "gender":
                return 200, report(["gender", "viewerPercentage"], [["female", 35.0], ["male", 65.0]])
            if dims == "deviceType":
                return 403, {"error": "forbidden"}
        video = filt.replace("video==", "")
        assert video in TOTALS, f"unexpected Analytics call for {video}"
        if dims is None:
            return 200, report(["views", "engagedViews", "averageViewPercentage", "averageViewDuration",
                                "subscribersGained", "estimatedMinutesWatched"], [TOTALS[video]])
        if dims == "day":
            return 200, report(["day", "views", "engagedViews"], [r + [r[1] // 2] for r in DAILY[video]])
        if dims.startswith("insightTrafficSourceType"):
            return 200, report(["insightTrafficSourceType", "creatorContentType", "views", "estimatedMinutesWatched"],
                               [["SUBSCRIBER", "videoOnDemand", 1200, 3000.0],
                                ["YT_SEARCH", "videoOnDemand", 30, 90.0]])
        if dims == "elapsedVideoTimeRatio":
            if video == "cutvideo004" and "relativeRetentionPerformance" in metrics:
                return 400, {"error": "unsupported"}
            return 200, curve_report(relative=None if video == "cutvideo004" else 0.25)
        raise AssertionError(f"unexpected call {p}")


class FakeCred:
    token = "fake-access-token"


def _mock(monkeypatch, api):
    monkeypatch.setattr(pu, "load_credentials", lambda *a, **k: FakeCred())
    monkeypatch.setattr(pu, "new_session", lambda: api)
    monkeypatch.setattr(pu, "_now", lambda: NOW)


def test_full_run_json_and_file(monkeypatch, capsys, tmp_path):
    api = FakeYouTube()
    _mock(monkeypatch, api)
    assert pu.main(["--profile", "mychannel", "--json", "--out", str(tmp_path)]) == 0
    r = json.loads(capsys.readouterr().out)
    written = (tmp_path / f"pulse-{r['date']}.json").read_text(encoding="utf-8")
    assert json.loads(written) == r and str(tmp_path) not in written

    ids = [v["id"] for v in r["videos"]]
    assert ids == ["newvideo001", "cutvideo002", "quietvid003", "cutvideo004"]     # private video left out
    by = {v["id"]: v for v in r["videos"]}
    assert by["newvideo001"]["pattern"]["label"] == "too_early" and by["newvideo001"]["analytics"] is None
    assert not any("newvideo001" in str(p.get("filters")) for _, p in api.calls)  # no Analytics call for it
    cut = by["cutvideo002"]
    assert cut["pattern"]["label"] == "tested_and_cut" and cut["pattern"]["burst_days"] == 3
    assert cut["content_type"] == "long" and cut["top_source"] == "SUBSCRIBER"
    assert cut["analytics"]["engaged_views"] == 900 and cut["retention"]["opening_percentile"] == 0.25
    assert cut["search_terms"][0]["term"] == "how a thing works"
    assert cut["daily_start"] == "2026-10-06" and len(cut["daily_views"]) == 11
    assert by["quietvid003"]["pattern"]["label"] == "never_tested" and by["quietvid003"]["retention"] is None
    assert by["cutvideo004"]["pattern"]["label"] == "tested_and_cut"
    assert by["cutvideo004"]["retention"]["opening_percentile"] is None    # fell back to audienceWatchRatio only

    assert r["window"]["last_data_day"] == "2026-10-16"
    assert r["totals"]["views"] == 2600 and r["totals"]["subscribers_gained"] == 26
    assert r["traffic"]["groups"]["long"]["discovery_share"] == pytest.approx(0.818, abs=0.001)
    assert r["search_terms"][0] == {"term": "thing explained", "views": 40, "minutes": 120.0}
    assert r["audience"]["age"]["status"] == "below_threshold" and r["audience"]["gender"]["readable"]
    assert r["audience"]["device"] is None and any("device types" in n for n in r["notes"])
    d = r["diagnosis"]
    assert d["code"] == "tested_and_cut" and d["needs_studio_ctr"] is True
    assert "Title of cutvideo002" in d["message"] and "opening is weak" in d["message"]


def test_full_run_text(monkeypatch, capsys, tmp_path):
    _mock(monkeypatch, FakeYouTube())
    assert pu.main(["--out", str(tmp_path), "--videos", "4"]) == 0
    out = capsys.readouterr().out
    assert "DIAGNOSIS: The feed tested your videos and cut them" in out
    assert "too early to judge" in out and "never tested" in out
    assert "age: too little data to interpret" in out and "device: not available" in out
    assert "SEARCH TERMS THAT BROUGHT VIEWERS" in out and "thing explained" in out
    assert "not in the YouTube Analytics API" in out and "home feed and subscriptions" in out
    assert len(list(tmp_path.glob("pulse-*.json"))) == 1


def test_core_api_error_exits_2(monkeypatch, capsys, tmp_path):
    _mock(monkeypatch, FakeYouTube(fail_channel=True))
    assert pu.main(["--out", str(tmp_path)]) == 2
    assert "HTTP 500" in capsys.readouterr().err
    assert not list(tmp_path.glob("pulse-*.json"))


def test_usage_errors(capsys):
    assert pu.main(["--profile", "../escape"]) == 2
    assert pu.main(["--videos", "0"]) == 2
    assert pu.main(["--videos", "51"]) == 2
    assert pu.main(["--days", "366"]) == 2


def test_missing_token_exits_2_with_login_command(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TUBECREW_HOME", str(tmp_path))
    monkeypatch.setattr(pu, "new_session", lambda: (_ for _ in ()).throw(AssertionError("network")))
    assert pu.main(["--profile", "mychannel"]) == 2
    err = capsys.readouterr().err
    assert "--login --profile mychannel" in err or "pip install" in err
    assert pu.token_path("mychannel") == tmp_path / "tokens" / "mychannel-analytics.json"
