"""Tests for skills/find-topic/scripts/transcripts.py (synthetic captions only, yt-dlp is replaced in every test)."""
import importlib.util
import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "find-topic" / "scripts" / "transcripts.py"


def _load():
    spec = importlib.util.spec_from_file_location("transcripts", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tr = _load()
REAL_YTDLP_COMMAND = tr.ytdlp_command
VID = "vid00000001"
ES_VID = "vid00000002"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any real yt-dlp call fails the test; pauses are skipped."""
    monkeypatch.setattr(tr, "PAUSE_S", 0)
    monkeypatch.setattr(tr, "ytdlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: pytest.fail("network must not be used"))


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _rolling_vtt(lines):
    """Automatic captions the way YouTube sends them: every line is shown again above the next one, with a 10 ms
    cue in between that repeats it, and the new words carry timing tags."""
    out = ["WEBVTT", "Kind: captions", "Language: en", ""]
    prev = None
    for i, (start, text) in enumerate(lines):
        end = lines[i + 1][0] - 0.01 if i + 1 < len(lines) else start + 3
        words = text.split()
        tagged = words[0] + "".join(f"<{_ts(start + 0.3 * (j + 1))}><c> {w}</c>" for j, w in enumerate(words[1:]))
        out += [f"{_ts(start)} --> {_ts(end)} align:start position:0%", prev or " ", tagged, ""]
        out += [f"{_ts(end)} --> {_ts(end + 0.01)} align:start position:0%", text, " ", ""]
        prev = text
    return "\n".join(out) + "\n"


LINES = [
    (0.16, "so here&#39;s a question"),
    (2.879, "why did this bridge fall"),
    (12.01, "it cost 6 million dollars"),
    (31.51, "and nobody saw it coming"),
    (45.01, "the wind was not that strong"),
    (75.01, "here is what really happened"),
]
EN_VTT = _rolling_vtt(LINES)
ES_VTT = _rolling_vtt([(0.5, "hola a todos"), (4.0, "hoy hablamos de puentes")])

SAMPLE_AUTO = """WEBVTT
Kind: captions
Language: en

00:00:00.160 --> 00:00:02.869 align:start position:0%

in<00:00:00.320><c> this</c><00:00:00.560><c> video</c>

00:00:02.869 --> 00:00:02.879 align:start position:0%
in this video


00:00:02.879 --> 00:00:05.150 align:start position:0%
in this video
we&#39;ll<00:00:03.120><c> look</c><00:00:03.400><c> at</c><00:00:03.700><c> bridges</c>

00:00:05.150 --> 00:00:05.160 align:start position:0%
we'll look at bridges

"""

SAMPLE_MANUAL = """WEBVTT

NOTE made by hand
with two lines

1
00:00:01.000 --> 00:00:03.500
<v Host>Welcome back.</v>

2
00:00:03.500 --> 00:00:06.000
<i>Today:</i> bridges &amp; wind,
and why they fail.

3
00:00:06.000 --> 00:00:08.000
and why they fail.
"""


def _info(vid, title="How bridges fail", channel="Example Lab", subtitles=(), auto=(), language=None):
    track = [{"ext": "vtt", "url": "https://example.com/captions.vtt"}]
    return {"id": vid, "title": title, "channel": channel, "language": language,
            "subtitles": {k: track for k in subtitles}, "automatic_captions": {k: track for k in auto}}


EN_INFO = _info(VID, auto=["ab", "af", "en-orig", "en", "es", "fr"], language="en")
ES_INFO = _info(ES_VID, title="Puentes que caen", auto=["ab", "en", "es-orig", "es", "fr"], language="es")


def _unescape(regex: str) -> str:
    return re.sub(r"\\(.)", r"\1", regex)


class FakeYtDlp:
    """Stands in for subprocess.run: answers `-J` with an info dict, writes the requested VTT, lists a channel."""

    def __init__(self, infos=None, vtts=None, flat=None):
        self.infos, self.vtts, self.flat, self.calls = infos or {}, vtts or {}, flat, []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if "--flat-playlist" in cmd:
            end = int(cmd[cmd.index("--playlist-end") + 1])
            return subprocess.CompletedProcess(cmd, 0, json.dumps({**self.flat, "entries": self.flat["entries"][:end]}),
                                               "")
        vid = cmd[-1].rsplit("v=", 1)[-1]
        if "-J" in cmd:
            info = self.infos.get(vid)
            if info is None:
                return subprocess.CompletedProcess(cmd, 1, "", f"ERROR: [youtube] {vid}: Video unavailable\n")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(info), "")
        track = _unescape(cmd[cmd.index("--sub-langs") + 1])
        text = self.vtts.get((vid, track))
        if text is not None:
            template = cmd[cmd.index("-o") + 1]
            Path(template.replace("%(id)s", vid).replace("%(ext)s", f"{track}.vtt")).write_text(text, "utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def downloads(self):
        return [(c[-1].rsplit("v=", 1)[-1], _unescape(c[c.index("--sub-langs") + 1]),
                 "manual" if "--write-subs" in c else "auto") for c in self.calls if "--sub-langs" in c]


def _fake(monkeypatch, **kwargs):
    fake = FakeYtDlp(**kwargs)
    monkeypatch.setattr(tr.subprocess, "run", fake)
    return fake


def _block_network(monkeypatch):
    monkeypatch.setattr(tr.subprocess, "run", lambda *a, **k: pytest.fail("network must not be used"))
    monkeypatch.setattr(tr, "ytdlp_command", lambda: pytest.fail("yt-dlp must not be needed"))


# ---------------------------------------------------------------------------------------------------------------
# Cleaning captions
# ---------------------------------------------------------------------------------------------------------------

def test_rolling_auto_captions_are_deduplicated():
    assert tr.parse_vtt(SAMPLE_AUTO) == [(0.16, "in this video"), (2.879, "we'll look at bridges")]
    assert [t for _, t in tr.parse_vtt(EN_VTT)] == [
        "so here's a question", "why did this bridge fall", "it cost 6 million dollars", "and nobody saw it coming",
        "the wind was not that strong", "here is what really happened"]


def test_manual_captions_drop_cue_ids_notes_and_tags():
    assert tr.parse_vtt(SAMPLE_MANUAL) == [
        (1.0, "Welcome back."), (3.5, "Today: bridges & wind,"), (3.5, "and why they fail.")]
    assert tr.parse_vtt(SAMPLE_MANUAL.replace("\n", "\r\n")) == tr.parse_vtt(SAMPLE_MANUAL)


def test_growing_caption_is_merged_not_repeated():
    vtt = ("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nthe\n\n00:00:02.000 --> 00:00:03.000\nthe bridge\n\n"
           "00:00:03.000 --> 00:00:04.000\nthe bridge fell\n\n00:00:04.000 --> 00:00:05.000\nthese ropes\n")
    assert tr.parse_vtt(vtt) == [(1.0, "the bridge fell"), (4.0, "these ropes")]


def test_text_has_a_stamp_about_every_30_seconds():
    text = tr.render_text(tr.parse_vtt(EN_VTT))
    assert text == ("[00:00] so here's a question why did this bridge fall it cost 6 million dollars\n\n"
                    "[00:31] and nobody saw it coming the wind was not that strong\n\n"
                    "[01:15] here is what really happened\n")
    assert tr.fmt_stamp(3725.4) == "1:02:05"
    assert tr.segments_from_text(text) == [
        (0, "so here's a question why did this bridge fall it cost 6 million dollars"),
        (31, "and nobody saw it coming the wind was not that strong"),
        (75, "here is what really happened"),
    ]


def test_first_number_and_opening():
    segments = tr.parse_vtt(EN_VTT)
    assert tr.first_number(tr.opening(segments, 60)) == (12.01, "it cost 6 million dollars")
    assert tr.first_number([(1.0, "no one knew"), (2.0, "a third of it")]) is None  # 'one' is not a number here
    assert tr.first_number([(1.0, "two years later")]) == (1.0, "two years later")
    assert [t for t, _ in tr.opening(segments, 31.51)] == [0.16, 2.879, 12.01]


# ---------------------------------------------------------------------------------------------------------------
# Choosing a track
# ---------------------------------------------------------------------------------------------------------------

def test_pick_track_language_fallback():
    pick = tr.pick_track
    assert pick(EN_INFO, ["en"]) == {"track": "en-orig", "language": "en", "kind": "auto", "choice": "requested",
                                     "original": "en"}
    # a Spanish video: the automatic "en" track is a machine translation, so the original language wins
    es = pick(ES_INFO, ["en"])
    assert (es["track"], es["language"], es["choice"]) == ("es-orig", "es", "original")
    # a human translation the creator uploaded is a real track and counts as requested
    with_manual = _info(ES_VID, subtitles=["en", "live_chat"], auto=["en", "es-orig", "es"])
    assert pick(with_manual, ["en"])["kind"] == "manual"
    # within a language, manual beats automatic; the exact code beats a regional one
    assert pick(_info(VID, subtitles=["en-GB", "en"], auto=["en-orig"]), ["en"])["track"] == "en"
    assert pick(_info(VID, subtitles=["en-GB"], auto=["en-orig"]), ["en"])["track"] == "en-GB"
    # several requested languages are tried in order
    assert pick(_info(VID, subtitles=["fr"], auto=["de-orig", "de"]), ["it", "fr"])["track"] == "fr"


def test_pick_track_first_available_and_none():
    pick = tr.pick_track
    only_manual = pick(_info(VID, subtitles=["fr"]), ["en"])
    assert (only_manual["track"], only_manual["choice"]) == ("fr", "first available")
    unknown = pick(_info(VID, auto=["ko"]), ["en"])  # no '-orig' mark and no language field
    assert (unknown["track"], unknown["kind"], unknown["choice"]) == ("ko", "auto", "first available")
    regional = pick(_info(VID, auto=["ab", "en"], language="en-US"), ["de"])  # translations are never taken
    assert (regional["track"], regional["choice"]) == ("en", "original")
    assert pick(_info(VID, subtitles=["live_chat"]), ["en"]) is None
    assert pick(_info(VID), ["en"]) is None


# ---------------------------------------------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------------------------------------------

def test_classify_target():
    assert tr.classify_target(VID) == ("video", VID)
    assert tr.classify_target(f"https://www.youtube.com/watch?v={VID}&t=42s") == ("video", VID)
    assert tr.classify_target(f"https://youtu.be/{VID}?si=abc") == ("video", VID)
    assert tr.classify_target(f"https://www.youtube.com/shorts/{VID}") == ("video", VID)
    assert tr.classify_target("@acme") == ("channel", "https://www.youtube.com/@acme/videos")
    assert tr.classify_target("https://www.youtube.com/@acme/streams") == ("channel",
                                                                           "https://www.youtube.com/@acme/videos")
    assert tr.classify_target("UCxxxxxxxxxxxxxxxxxxxxxx") == (
        "channel", "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx/videos")
    with pytest.raises(tr.UsageError):
        tr.classify_target("not a video")


# ---------------------------------------------------------------------------------------------------------------
# End to end with a fake yt-dlp
# ---------------------------------------------------------------------------------------------------------------

def test_fetch_writes_text_vtt_and_manifest(monkeypatch, tmp_path, capsys):
    fake = _fake(monkeypatch, infos={VID: EN_INFO}, vtts={(VID, "en-orig"): EN_VTT})
    out = tmp_path / "my transcripts"
    assert tr.main([f"https://www.youtube.com/watch?v={VID}", "--out", str(out)]) == 0
    assert fake.downloads() == [(VID, "en-orig", "auto")]
    dl = next(c for c in fake.calls if "--sub-langs" in c)
    assert "--skip-download" in dl and dl[dl.index("--sub-format") + 1] == "vtt"
    text = (out / f"{VID}_how-bridges-fail.en.txt").read_text(encoding="utf-8")
    assert text.startswith(f"# How bridges fail\n# https://www.youtube.com/watch?v={VID}\n"
                           "# channel: Example Lab | language: en (auto) | fetched ")
    assert "\n\n[00:31] and nobody saw it coming" in text and text.count("why did this bridge fall") == 1
    entry = json.loads((out / "manifest.json").read_text(encoding="utf-8"))[VID]
    assert {k: entry[k] for k in ("id", "title", "channel", "language", "kind", "choice", "file", "vtt")} == {
        "id": VID, "title": "How bridges fail", "channel": "Example Lab", "language": "en", "kind": "auto",
        "choice": "requested", "file": f"{VID}_how-bridges-fail.en.txt", "vtt": f"vtt/{VID}.en-orig.vtt"}
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["fetched"])
    assert (out / "vtt" / f"{VID}.en-orig.vtt").read_text(encoding="utf-8") == EN_VTT
    assert "1 fetched, 0 already present, 0 failed" in capsys.readouterr().out


def test_rerun_skips_fetched_videos_and_refresh_redoes_them(monkeypatch, tmp_path, capsys):
    out = tmp_path / "t"
    _fake(monkeypatch, infos={VID: EN_INFO}, vtts={(VID, "en-orig"): EN_VTT})
    assert tr.main([VID, "--out", str(out)]) == 0
    capsys.readouterr()
    _block_network(monkeypatch)
    assert tr.main([VID, "--out", str(out), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"] == {"fetched": 0, "present": 1, "failed": 0, "deferred": 0}
    assert data["videos"][0]["status"] == "present" and data["videos"][0]["language"] == "en"
    fake = _fake(monkeypatch, infos={VID: EN_INFO}, vtts={(VID, "en-orig"): EN_VTT})
    monkeypatch.setattr(tr, "ytdlp_command", lambda: ["yt-dlp"])
    assert tr.main([VID, "--out", str(out), "--refresh"]) == 0
    assert fake.downloads() == [(VID, "en-orig", "auto")]
    # a deleted text file means the video is fetched again
    (out / f"{VID}_how-bridges-fail.en.txt").unlink()
    fake.calls.clear()
    assert tr.main([VID, "--out", str(out)]) == 0
    assert len(fake.downloads()) == 1


def test_original_language_fallback_end_to_end(monkeypatch, tmp_path, capsys):
    fake = _fake(monkeypatch, infos={ES_VID: ES_INFO}, vtts={(ES_VID, "es-orig"): ES_VTT})
    out = tmp_path / "t"
    assert tr.main([ES_VID, "--out", str(out)]) == 0
    assert fake.downloads() == [(ES_VID, "es-orig", "auto")]
    assert "es auto (original language)" in capsys.readouterr().out
    entry = json.loads((out / "manifest.json").read_text(encoding="utf-8"))[ES_VID]
    assert (entry["language"], entry["choice"], entry["file"]) == ("es", "original",
                                                                   f"{ES_VID}_puentes-que-caen.es.txt")
    # asking for Spanish first takes the same track as a requested language
    assert tr.main([ES_VID, "--out", str(out), "--lang", "es,en", "--refresh", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["videos"][0]["choice"] == "requested"


def test_first_prints_only_the_opening(monkeypatch, tmp_path, capsys):
    _fake(monkeypatch, infos={VID: EN_INFO}, vtts={(VID, "en-orig"): EN_VTT})
    out = tmp_path / "t"
    assert tr.main([VID, "--out", str(out), "--first", "60"]) == 0
    text = capsys.readouterr().out
    assert "  [00:00] so here's a question\n  [00:02] why did this bridge fall\n  [00:12] it cost 6 million" in text
    assert "[00:45] the wind was not that strong" in text and "here is what really happened" not in text
    assert "First number: [00:12] it cost 6 million dollars" in text
    assert "cannot show the first cut" in text
    # later runs read the kept VTT: exact line times, no network
    _block_network(monkeypatch)
    assert tr.main([VID, "--out", str(out), "--first", "10", "--json"]) == 0
    video = json.loads(capsys.readouterr().out)["videos"][0]
    assert [o["text"] for o in video["opening"]] == ["so here's a question", "why did this bridge fall"]
    assert video["first_number"] is None
    # without the VTT the 30-second paragraphs of the text file are used
    (out / "vtt" / f"{VID}.en-orig.vtt").unlink()
    assert tr.main([VID, "--out", str(out), "--first", "10"]) == 0
    assert "[00:00] so here's a question why did this bridge fall it cost 6" in capsys.readouterr().out


def _catalog(tmp_path):
    catalog = {
        "_note": "synthetic",
        "Acme Science": [{"id": f"acme0000{i:03d}", "title": f"Video {i}", "views": v}
                         for i, v in enumerate([1000, 1200, 900, 1100, 6000])]
        + [{"id": "acmepushed1", "title": "Pushed", "views": 9000, "likes": 9}],
        "Example Lab": [{"id": "lab00000001", "title": "One", "views": 50}],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def test_from_json_catalog_takes_only_outliers(monkeypatch, tmp_path, capsys):
    infos = {"acme0000004": _info("acme0000004", title="Video 4", auto=["en-orig", "en"])}
    fake = _fake(monkeypatch, infos=infos, vtts={("acme0000004", "en-orig"): EN_VTT})
    out = tmp_path / "t"
    assert tr.main(["--from-json", str(_catalog(tmp_path)), "--min-multiple", "2", "--out", str(out), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert fake.downloads() == [("acme0000004", "en-orig", "auto")]
    assert [v["id"] for v in data["videos"]] == ["acme0000004"]
    assert any("Example Lab: skipped" in n for n in data["notes"])
    assert any("1 possibly ad-pushed" in n for n in data["notes"])


def test_from_json_outliers_report(monkeypatch, tmp_path, capsys):
    report = {"min_multiple": 2.0, "outliers": [
        {"id": "rep00000001", "title": "Big one", "channel": "Acme Science", "multiple": 5.5},
        {"id": "rep00000002", "title": "Pushed", "channel": "Acme Science", "multiple": 9.0, "ad_pushed": True},
        {"id": "rep00000003", "title": "Small", "channel": "Example Lab", "multiple": 2.1},
    ]}
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    rows, notes = tr.targets_from_json(path, 3)
    assert [r["id"] for r in rows] == ["rep00000001"] and notes
    rows, _ = tr.targets_from_json(path, None)
    assert [r["id"] for r in rows] == ["rep00000001", "rep00000003"]


def test_channel_takes_the_newest_long_videos(monkeypatch, tmp_path, capsys):
    flat = {"channel": "Acme Science", "entries": [
        {"id": "new00000000", "title": "Soon", "live_status": "is_upcoming"},
        {"id": "new00000001", "title": "Newest"}, {"id": "new00000002", "title": "Second"},
        {"id": "new00000003", "title": "Third"}]}
    infos = {v: _info(v, auto=["en-orig"]) for v in ("new00000001", "new00000002")}
    vtts = {(v, "en-orig"): EN_VTT for v in infos}
    fake = _fake(monkeypatch, infos=infos, vtts=vtts, flat=flat)
    assert tr.main(["@acme", "--last", "2", "--out", str(tmp_path / "t")]) == 0
    listing = fake.calls[0]
    assert listing[-1] == "https://www.youtube.com/@acme/videos"
    assert listing[listing.index("--playlist-end") + 1] == "2"
    assert [d[0] for d in fake.downloads()] == ["new00000001"]  # the upcoming premiere took one of the two slots


def test_max_defers_the_rest(monkeypatch, tmp_path, capsys):
    ids = ["max00000001", "max00000002", "max00000003"]
    fake = _fake(monkeypatch, infos={v: _info(v, auto=["en-orig"]) for v in ids},
                 vtts={(v, "en-orig"): EN_VTT for v in ids})
    assert tr.main(ids + ["--max", "2", "--out", str(tmp_path / "t")]) == 0
    assert [d[0] for d in fake.downloads()] == ids[:2]
    assert "deferred  1 video over --max 2" in capsys.readouterr().out


def test_exit_1_when_nothing_could_be_fetched(monkeypatch, tmp_path, capsys):
    _fake(monkeypatch, infos={VID: _info(VID)})  # no captions; ES_VID is unavailable
    assert tr.main([VID, ES_VID, "--out", str(tmp_path / "t")]) == 1
    text = capsys.readouterr().out
    assert "no captions (manual or automatic)" in text and "Video unavailable" in text
    assert "0 fetched, 0 already present, 2 failed" in text
    # a track yt-dlp cannot deliver is a failure too, not a crash
    _fake(monkeypatch, infos={VID: EN_INFO})
    assert tr.main([VID, "--out", str(tmp_path / "t")]) == 1
    assert "wrote no caption file" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [
    [],
    [VID, "--lang", "english please"],
    [VID, "--min-multiple", "2"],
    [VID, "--first", "0"],
    [VID, "--max", "0"],
    ["not a video"],
    ["--from-json", "missing.json"],
    [VID, "--bogus"],
])
def test_usage_errors(argv, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert tr.main(argv) == 2


def test_environment_errors(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(tr, "ytdlp_command", REAL_YTDLP_COMMAND)
    monkeypatch.setattr(tr.shutil, "which", lambda name: None)
    monkeypatch.setattr(tr.importlib.util, "find_spec", lambda name: None)
    out = tmp_path / "t"
    assert tr.main([VID, "--out", str(out)]) == 2
    assert "yt-dlp not found" in capsys.readouterr().err
    # nothing to fetch: yt-dlp is not needed at all
    out.mkdir(exist_ok=True)
    (out / "a.txt").write_text("[00:00] hello\n", encoding="utf-8")
    (out / "manifest.json").write_text(json.dumps({VID: {"id": VID, "title": "T", "url": tr.video_url(VID),
                                                         "language": "en", "kind": "auto", "file": "a.txt"}}),
                                       encoding="utf-8")
    assert tr.main([VID, "--out", str(out)]) == 0
    (out / "manifest.json").write_text("{broken", encoding="utf-8")
    assert tr.main([VID, "--out", str(out)]) == 2
    assert "fix it or move it away" in capsys.readouterr().err
