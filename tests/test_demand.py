"""Tests for skills/find-topic/scripts/demand.py (synthetic data only, the network is replaced in every test)."""
import datetime as dt
import importlib.util
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "find-topic" / "scripts" / "demand.py"


def _load():
    spec = importlib.util.spec_from_file_location("demand", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dm = _load()
TODAY = dt.date.today()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any real HTTP call fails the test; pauses are skipped."""
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network must not be used"))
    monkeypatch.setattr(dm, "WIKI_PAUSE_S", 0)
    monkeypatch.setattr(dm, "SUGGEST_PAUSE_S", 0)


def _ago(days):
    return (TODAY - dt.timedelta(days=days)).strftime("%Y%m%d")


def _video(vid, views, subs, days, duration=900, title=None, channel="Example Lab", **extra):
    e = {"id": vid, "title": title or f"Video {vid}", "view_count": views, "channel_follower_count": subs,
         "upload_date": _ago(days) if days is not None else None, "duration": duration, "channel": channel,
         "webpage_url": f"https://www.youtube.com/watch?v={vid}", "live_status": "not_live"}
    e.update(extra)
    return e


SERP_REACHABLE = {"_type": "playlist", "id": "example query", "entries": [
    _video("big00000001", 900_000, 2_000_000, 400),                     # giant: not small
    _video("step0000001", 25_000, 12_000, 60),                          # fresh, real step
    _video("short000001", 80_000, 3_000, 30, duration=45),              # under 3 minutes
    _video("stale000001", 40_000, 9_000, 800),                          # step, but old
    _video("pod00000001", 30_000, 20_000, 20, title="The Example Podcast Ep. 42"),
    _video("live0000001", 15_000, 7_000, 10, live_status="was_live"),
    _video("reup0000001", 60_000, 1_500, 15, title="Bridge disaster (full documentary) reupload"),
    _video("nosubs00001", 70_000, None, 40),                            # size unknown
    None,                                                               # an entry yt-dlp could not read
]}
SERP_GAP = {"entries": [_video("big00000001", 900_000, 2_000_000, 30), _video("stale000001", 40_000, 9_000, 800)]}
SERP_DESERT = {"entries": [_video("tiny0000001", 900, 1_000, 30), _video("tiny0000002", 4_000, 200, 90)]}


def _rows(data):
    return [dm.classify(r, TODAY) for r in dm.parse_serp(data)]


# ---------------------------------------------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------------------------------------------

def test_suggest_url_and_parsing():
    url = dm.suggest_url("bridge collapse & wind", "en", "US")
    assert url.startswith("https://suggestqueries.google.com/complete/search?")
    assert "ds=yt" in url and "client=firefox" in url and "hl=en" in url and "gl=US" in url
    assert "q=bridge+collapse+%26+wind" in url
    assert "gl=" not in dm.suggest_url("x", "en", None)
    assert dm.parse_suggestions('["bridge", ["bridge collapse", "bridge collapse", "bridge design"]]') == [
        "bridge collapse", "bridge design"]
    jsonp = 'window.google.ac.h(["bridge",[["bridge collapse",0,[512]],["bridge physics",0]],{"k":1}])'
    assert dm.parse_suggestions(jsonp) == ["bridge collapse", "bridge physics"]
    assert dm.parse_suggestions('["nothing here", []]') == []
    with pytest.raises(ValueError):
        dm.parse_suggestions('{"not": "a list"}')


def test_expand_queries_and_intent_flags():
    ex = dm.expand_queries("bridge collapse")
    assert len(ex) == 30 and ex[0] == "bridge collapse a" and ex[25] == "bridge collapse z"
    assert ex[-4:] == ["why bridge collapse", "how bridge collapse", "what bridge collapse", "when bridge collapse"]
    flags = dm.intent_flags(["bridge collapse movie scene", "bridge collapse in hindi", "bridge collapse physics",
                             "bridge collapse class 10 notes"])
    assert flags["film, music or games"] == ["bridge collapse movie scene"]
    assert flags["exam prep or another language market"] == ["bridge collapse in hindi",
                                                              "bridge collapse class 10 notes"]
    assert dm.intent_flags(["bridge collapse explained"]) == {}


def test_unique_queries_keeps_order_and_drops_duplicates():
    assert dm.unique_queries("Bridge  collapse", ["bridge collapse", "why bridges fail", " "]) == [
        "Bridge collapse", "why bridges fail"]


# ---------------------------------------------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------------------------------------------

def test_classify_flags_and_real_step():
    rows = {r["id"]: r for r in _rows(SERP_REACHABLE)}
    assert len(rows) == 8 and rows["nosubs00001"]["rank"] == 8
    assert rows["step0000001"]["real_step"] and rows["step0000001"]["flags"] == []
    assert not rows["big00000001"]["small"] and not rows["big00000001"]["step"]
    assert rows["short000001"]["step"] and rows["short000001"]["flags"] == ["under 3 min"]
    assert rows["stale000001"]["step"] and not rows["stale000001"]["fresh"] and not rows["stale000001"]["real_step"]
    assert rows["pod00000001"]["flags"] == ["podcast"]
    assert rows["live0000001"]["flags"] == ["stream replay"]
    assert rows["reup0000001"]["flags"] == ["possible re-upload"]
    assert not rows["nosubs00001"]["small"]  # an unknown size never counts as small
    real = sum(r["real_step"] for r in rows.values())
    assert real == 1


def test_short_detected_from_url_or_media_type():
    typed = _video("s1", 20_000, 5_000, 10, duration=None, media_type="short")
    by_url = _video("s2", 20_000, 5_000, 10, duration=None, webpage_url="https://www.youtube.com/shorts/s2")
    for row in _rows({"entries": [typed, by_url]}):
        assert row["flags"] == ["Short"] and not row["real_step"]


def test_serp_shapes():
    s = dm.serp_summary(_rows(SERP_REACHABLE))
    assert s["shape"] == "reachable" and s["real_steps"] == 1 and s["steps"] == 6
    assert s["stale_or_flagged_steps"] == 5 and s["unknown_size"] == 1 and s["top_age_days"] == 400
    gap = dm.serp_summary(_rows(SERP_GAP))
    assert gap["shape"] == "gap" and gap["steps"] == 1 and gap["real_steps"] == 0
    assert dm.serp_summary(_rows(SERP_DESERT))["shape"] == "desert"


def test_fresh_days_is_configurable():
    rows = [dm.classify(r, TODAY, fresh_days=1000) for r in dm.parse_serp(SERP_GAP)]
    assert dm.serp_summary(rows)["shape"] == "reachable"


# ---------------------------------------------------------------------------------------------------------------
# Wikipedia
# ---------------------------------------------------------------------------------------------------------------

def test_wiki_arguments_urls_and_window():
    assert dm.parse_wiki_arg("Suspension_bridge") == ("en", "Suspension bridge")
    assert dm.parse_wiki_arg("https://de.wikipedia.org/wiki/Br%C3%BCcke_(Bauwerk)?x=1", "en") == (
        "de", "Brücke (Bauwerk)")
    assert dm.parse_wiki_arg("https://en.m.wikipedia.org/wiki/Arch_bridge") == ("en", "Arch bridge")
    with pytest.raises(dm.UsageError):
        dm.parse_wiki_arg("https://example.com/wiki/Bridge")
    start, end = dm.wiki_window(dt.date(2026, 6, 1), 90)
    assert end == dt.date(2026, 5, 31) and (end - start).days + 1 == 90
    url = dm.pageviews_url("en", "Bridge (structure)/history", start, end)
    assert url.startswith("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia.org/"
                          "all-access/user/")
    assert "Bridge_%28structure%29%2Fhistory/daily/20260303/20260531" in url
    assert "redirects=1" in dm.resolve_url("en", "bridge") and "titles=bridge" in dm.resolve_url("en", "bridge")


def test_parse_resolve_and_pageviews():
    assert dm.parse_resolve({"query": {"redirects": [{"from": "Bridges", "to": "Bridge"}],
                                       "pages": [{"pageid": 1, "title": "Bridge"}]}}) == "Bridge"
    assert dm.parse_resolve({"query": {"pages": [{"title": "Nope", "missing": True}]}}) is None
    assert dm.parse_resolve({}) is None
    assert dm.parse_pageviews({"items": [{"views": 10}, {"views": 5}, {"views": None}]}) == 15
    with pytest.raises(ValueError):
        dm.parse_pageviews({"detail": "not found"})


# ---------------------------------------------------------------------------------------------------------------
# Triangulation
# ---------------------------------------------------------------------------------------------------------------

def test_parse_source():
    s = dm.parse_source("trends-yt=40/55")
    assert s["name"] == "trends-yt" and s["topic"] == 40 and s["baseline"] == 55
    assert round(s["multiple"], 3) == 0.727
    assert dm.parse_source("keyword tool = 12,000 / 8,000")["multiple"] == 1.5
    for bad in ("trends", "trends=40", "trends=40/0", "=4/5", "trends=a/b"):
        with pytest.raises(dm.UsageError):
            dm.parse_source(bad)


def test_triangulate_labels():
    assert dm.triangulate({})["label"] == "not measured"
    assert dm.triangulate({"wikipedia": 0.5, "x": None})["label"] == "single source"
    ok = dm.triangulate({"wikipedia": 0.5, "trends-yt": 1.4})
    assert ok["label"] == "consistent" and ok["spread"] == 2.8
    unclear = dm.triangulate({"wikipedia": 0.09, "keywords": 1.1})
    assert unclear["label"] == "demand unclear" and unclear["spread"] > 3
    assert dm.triangulate({"wikipedia": 0.0, "keywords": 1.0})["label"] == "demand unclear"
    assert dm.triangulate({"wikipedia": 0.0, "keywords": 0.0})["label"] == "consistent"
    assert dm.triangulate({"a": 1.0, "b": 3.0})["label"] == "consistent"  # exactly 3x is not over 3x


# ---------------------------------------------------------------------------------------------------------------
# HTTP and main
# ---------------------------------------------------------------------------------------------------------------

def test_http_get_sends_a_descriptive_user_agent(monkeypatch):
    seen = {}

    class FakeResp:
        headers = type("H", (), {"get_content_charset": staticmethod(lambda: "utf-8")})()

        def read(self):
            return b'["q", []]'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        seen["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert dm.http_get("https://example.com/x") == '["q", []]'
    assert seen["ua"].startswith("tubecrew-demand/") and "https://" in seen["ua"]
    assert seen["timeout"] == dm.HTTP_TIMEOUT_S


def _fake_http(missing=()):
    """A stand-in for http_get that answers the three endpoints from memory."""
    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        if "suggestqueries" in url:
            if "q=why+bridges+fail" in url:
                return '["why bridges fail", []]'
            return '["bridge collapse", ["bridge collapse explained", "bridge collapse movie"]]'
        if "w/api.php" in url:
            title = url.split("titles=")[1].replace("+", " ")
            if title in missing:
                return json.dumps({"query": {"pages": [{"title": title, "missing": True}]}})
            return json.dumps({"query": {"pages": [{"title": title.replace("%28", "(").replace("%29", ")")}]}})
        if "pageviews/per-article" in url:
            views = 90 if "/Topic_article/" in url else 450
            return json.dumps({"items": [{"views": views}] * 10})
        raise AssertionError(url)

    return fake, calls


def test_main_full_run_json(monkeypatch, capsys, tmp_path):
    fake, calls = _fake_http()
    monkeypatch.setattr(dm, "http_get", fake)
    monkeypatch.setattr(dm, "ytdlp_command", lambda: ["yt-dlp"])
    cmds = []

    def fake_run_json(cmd, timeout=None):
        cmds.append(cmd)
        return SERP_REACHABLE if cmd[-1].endswith(":bridge collapse") else SERP_DESERT

    monkeypatch.setattr(dm, "_run_json", fake_run_json)
    out = tmp_path / "research" / "demand.json"
    rc = dm.main(["bridge collapse", "--variant", "why bridges fail", "--wiki", "Topic article",
                  "--baseline", "Base article", "--source", "trends-yt=30/50", "--json", "--out", str(out)])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["gate"] == "PASS"
    assert cmds[0][-1] == "ytsearch18:bridge collapse" and "-J" in cmds[0] and "--ignore-errors" in cmds[0]
    assert cmds[1][-1] == "ytsearch18:why bridges fail"
    assert data["serp"]["queries"]["why bridges fail"]["summary"]["shape"] == "desert"
    assert data["suggestions"]["queries"]["bridge collapse"] == ["bridge collapse explained", "bridge collapse movie"]
    assert data["suggestions"]["no_query"] == ["why bridges fail"]
    assert "film, music or games" in data["suggestions"]["intent"]
    wiki = data["wikipedia"]
    assert wiki["topic"]["total"] == 900 and wiki["baselines"][0]["total"] == 4500 and wiki["multiple"] == 0.2
    tri = data["triangulation"]
    assert tri["sources"] == {"wikipedia": 0.2, "trends-yt": 0.6} and tri["label"] == "consistent"
    assert json.loads(out.read_text(encoding="utf-8"))["gate"] == "PASS"
    assert any("redirects=1" in c for c in calls)


def test_main_human_output_and_gate_fail(monkeypatch, capsys):
    fake, _ = _fake_http()
    monkeypatch.setattr(dm, "http_get", fake)
    monkeypatch.setattr(dm, "ytdlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(dm, "_run_json", lambda cmd, timeout=None: SERP_GAP)
    rc = dm.main(["bridge collapse", "--top", "5"])
    text = capsys.readouterr().out
    assert rc == 1
    assert "GAP, 0 fresh real steps (1 step in total)" in text and "STEP GATE: FAIL" in text
    assert "bridge collapse explained" in text and "Intent flag, film, music or games: 1" in text


def test_main_no_serp_does_not_need_ytdlp(monkeypatch, capsys):
    fake, _ = _fake_http(missing=("Missing article",))
    monkeypatch.setattr(dm, "http_get", fake)
    monkeypatch.setattr(dm, "ytdlp_command", lambda: pytest.fail("yt-dlp must not be needed"))
    rc = dm.main(["bridge collapse", "--no-serp", "--wiki", "Topic article", "--baseline", "Missing article",
                  "--source", "keywords=900/100"])
    text = capsys.readouterr().out
    assert rc == 0
    assert "STEP GATE: not measured" in text and "Wikipedia measures curiosity, not need" in text
    assert "not measured (no article 'Missing article'" in text
    assert "keywords 9.00x: single source" in text  # no baseline pageviews, so Wikipedia gives no multiple


def test_main_expand_collects_new_suggestions(monkeypatch, capsys):
    fake, calls = _fake_http()
    monkeypatch.setattr(dm, "http_get", fake)
    rc = dm.main(["bridge collapse", "--no-serp", "--expand", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["suggestions"]["expanded"] == []  # the fake answers the same list every time
    assert len(calls) == 31


def test_main_network_failures(monkeypatch, capsys):
    def broken(url, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(dm, "http_get", broken)
    assert dm.main(["bridge collapse", "--no-serp"]) == 2
    assert "nothing could be measured" in capsys.readouterr().err

    fake, _ = _fake_http()
    monkeypatch.setattr(dm, "http_get", fake)
    monkeypatch.setattr(dm, "ytdlp_command", lambda: ["yt-dlp"])

    def no_results(cmd, timeout=None):
        raise RuntimeError("ERROR: unable to download webpage")

    monkeypatch.setattr(dm, "_run_json", no_results)
    assert dm.main(["bridge collapse"]) == 2
    captured = capsys.readouterr()
    assert "step gate was not measured" in captured.err and "STEP GATE: not measured" in captured.out


def test_main_wiki_http_404_is_reported(monkeypatch, capsys):
    fake, _ = _fake_http()

    def with_404(url, timeout=None):
        if "pageviews/per-article" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return fake(url)

    monkeypatch.setattr(dm, "http_get", with_404)
    rc = dm.main(["bridge collapse", "--no-serp", "--wiki", "Topic article", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0 and data["wikipedia"]["topic"]["error"] == "no pageview data in this window"


@pytest.mark.parametrize("argv", [
    [],
    ["  "],
    ["q", "--top", "0"],
    ["q", "--days", "3"],
    ["q", "--baseline", "Some article"],
    ["q", "--source", "trends"],
    ["q", "--source", "a=1/2", "--source", "a=3/4"],
    ["q", "--wiki", "https://example.com/x"],
    ["q", "--no-suggest", "--no-serp"],
    ["q", "--bogus"],
])
def test_main_usage_errors(argv, monkeypatch):
    monkeypatch.setattr(dm, "ytdlp_command", lambda: ["yt-dlp"])
    assert dm.main(argv) == 2


def test_missing_ytdlp_is_an_environment_error(monkeypatch, capsys):
    monkeypatch.setattr(dm.shutil, "which", lambda name: None)
    monkeypatch.setattr(dm.importlib.util, "find_spec", lambda name: None)
    assert dm.main(["bridge collapse"]) == 2
    assert "--no-serp" in capsys.readouterr().err
