"""Tests for skills/upload-private/scripts/upload.py. No network: every HTTP call goes to a fake session."""
import datetime as dt
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "upload-private" / "scripts" / "upload.py"
CHANNEL = "UCxxxxxxxxxxxxxxxxxxxxxx"
OTHER_CHANNEL = "UCyyyyyyyyyyyyyyyyyyyyyy"
UPLOADS = "UUxxxxxxxxxxxxxxxxxxxxxx"
DATE = "2026-10-18"
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
PUBLISH_AT = "2026-10-20T16:00:00+02:00"  # 2026-10-20T14:00:00Z, two days after NOW
PUBLISH_AT_UTC = "2026-10-20T14:00:00Z"


def _load():
    spec = importlib.util.spec_from_file_location("tubecrew_upload", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


up = _load()


# --- helpers ------------------------------------------------------------------------------------------------------

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def base_meta(**overrides) -> dict:
    meta = {
        "channel_id": CHANNEL,
        "title": "How Acme Lost $3 Billion in One Week",
        "description": "What happened and why.\n\n0:00 Intro\n0:45 The deal\n3:10 What went wrong",
        "tags": ["acme", "acme corp", "business history"],
        "category_id": "27",
        "default_language": "en",
        "made_for_kids": False,
        "synthetic_media": False,
    }
    meta.update(overrides)
    return meta


def write_reviews(project: Path, video_sha: str = None, meta_sha: str = None, audit: str = "PASS",
                  gate: str = "GO") -> None:
    video_sha = video_sha or sha(project / "render" / "final.mp4")
    meta_sha = meta_sha or sha(project / "upload.json")
    qa = project / "qa"
    (qa / "render-check.json").write_text(json.dumps({"verdict": "PASS", "sha256": video_sha}), encoding="utf-8")
    (qa / f"RENDER-AUDIT-{DATE}.md").write_text(
        f"VERDICT: {audit}\nVideo: render/final.mp4 sha256:{video_sha[:12]}\n\n| severity | time |\n",
        encoding="utf-8")
    thumb = json.loads((project / "upload.json").read_text(encoding="utf-8")).get("thumbnail")
    thumb_line = f"Thumbnail: {thumb} sha256:{sha(project / thumb)[:12]}\n" if thumb else ""
    (qa / f"UPLOAD-GATE-{DATE}.md").write_text(
        f"VERDICT: {gate}\nVideo: render/final.mp4 sha256:{video_sha[:12]}\n"
        f"Metadata: upload.json sha256:{meta_sha[:12]}\n{thumb_line}\n| check | result | evidence |\n",
        encoding="utf-8")


def make_project(tmp_path: Path, reviews: bool = True, thumbnail: bool = False, **overrides) -> Path:
    project = tmp_path / "my video"  # a space on purpose
    (project / "render").mkdir(parents=True)
    (project / "qa").mkdir()
    (project / "render" / "final.mp4").write_bytes(b"not really an mp4, just bytes for hashing")
    meta = base_meta(**overrides)
    if thumbnail:
        (project / "thumb.jpg").write_bytes(b"\xff\xd8\xff fake jpeg")
        meta["thumbnail"] = "thumb.jpg"
    (project / "upload.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    if reviews:
        write_reviews(project)
    return project


def checks_of(project: Path, **kwargs) -> list:
    return up.build_plan(project, **kwargs).checks


class FakeResponse:
    def __init__(self, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body


class FakeSession:
    """Answers calls from a script of (method, response, exception or callable) and records every call. A callable
    is called with (url, kwargs) and returns the response, so a test can look at the disk mid-upload."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert self.script, f"unexpected {method} {url}"
        expected, result = self.script.pop(0)
        assert expected == method, f"expected {expected}, got {method} {url}"
        if callable(result) and not isinstance(result, FakeResponse):
            result = result(url, kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)

    def put(self, url, **kwargs):
        return self._next("PUT", url, **kwargs)


class FakeCred:
    token = "fake-access-token"
    refresh_token = None
    expiry = None
    valid = True

    def to_json(self):
        return json.dumps({"token": self.token, "refresh_token": self.refresh_token})


class FakeRefreshCred(FakeCred):
    refresh_token = "fake-refresh-token"


def channels_ok(channel: str = CHANNEL):
    return ("GET", FakeResponse(200, body={"items": [
        {"id": channel, "contentDetails": {"relatedPlaylists": {"uploads": UPLOADS}}}]}))


def playlist(*items):
    """playlistItems.list answer: items are (video_id, title, added_at)."""
    return ("GET", FakeResponse(200, body={"items": [
        {"snippet": {"title": title, "resourceId": {"kind": "youtube#video", "videoId": vid}, "publishedAt": added}}
        for vid, title, added in items]}))


def upload_ok(answer=None):
    answer = answer or {"id": "abcdefghijk", "status": {"privacyStatus": "private"},
                        "snippet": {"channelId": CHANNEL}}
    return [("POST", FakeResponse(200, headers={"Location": "https://upload.example.com/session/1"})),
            ("PUT", FakeResponse(200, body=answer))]


def no_network(*_args, **_kwargs):
    raise AssertionError("network was used")


def fake_upload(monkeypatch, script, cred=None):
    session = FakeSession(script)
    monkeypatch.setattr(up, "load_upload_credentials", lambda *a, **k: cred or FakeCred())
    monkeypatch.setattr(up, "new_session", lambda: session)
    return session


def at(monkeypatch, when=NOW):
    clock = {"now": when}
    monkeypatch.setattr(up, "utcnow", lambda: clock["now"])
    return clock


def log_entry(project: Path, n: int = 0) -> dict:
    return json.loads((project / "qa" / "upload-log.json").read_text(encoding="utf-8"))["uploads"][n]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test gets its own TUBECREW_HOME: no test ever looks at a real token folder."""
    path = tmp_path / "tubecrew-home"
    path.mkdir()
    monkeypatch.setenv("TUBECREW_HOME", str(path))
    monkeypatch.setattr(up, "READBACK_WAIT_S", 0)
    return path


def write_token(home: Path, profile: str = "default", record: dict = None) -> Path:
    """A fake token file (not a real credential) plus, optionally, its record."""
    tokens = home / "tokens"
    tokens.mkdir(exist_ok=True)
    token = tokens / f"{profile}-upload.json"
    token.write_text('{"token": "fake", "refresh_token": "fake-refresh-token"}', encoding="utf-8")
    if record is not None:
        (tokens / f"{profile}-upload.meta.json").write_text(json.dumps(record), encoding="utf-8")
    return token


# --- validators ---------------------------------------------------------------------------------------------------

def test_tag_weight_counts_quotes_and_commas():
    assert up.tag_weight([]) == 0
    assert up.tag_weight(["ab"]) == 2
    assert up.tag_weight(["a b", "cd"]) == (3 + 2) + 2 + 1


def test_title_validator():
    assert up.validate_title("")[0]
    assert up.validate_title(None)[0]
    assert up.validate_title("x" * 100)[0] == []
    assert up.validate_title("x" * 101)[0]
    assert up.validate_title("A <b> title")[0]
    ok, warn = up.validate_title("y" * 60)
    assert ok == [] and warn == []
    title = "z" * 59 + "END-OF-SIXTY-PLUS"
    errors, warnings = up.validate_title(title)
    assert errors == []
    assert len(warnings) == 1 and title[:60] in warnings[0] and "may be cut on mobile and in search" in warnings[0]


def test_description_validator_counts_utf8_bytes():
    assert up.validate_description("a" * 5000)[0] == []
    assert up.validate_description("a" * 5001)[0]
    assert up.validate_description("é" * 2501)[0]  # 2501 characters, 5002 bytes
    assert up.validate_description("use x > y")[0]
    assert up.validate_description("")[1]  # empty is a warning, not a failure


def test_tags_validator():
    assert up.validate_tags(["fine", "also fine"]) == ([], [])
    assert up.validate_tags("acme")[0]
    assert up.validate_tags(["ok", ""])[0]
    assert up.validate_tags(["ok", 3])[0]
    heavy = ["tag number %02d with spaces" % i for i in range(25)]
    assert up.tag_weight(heavy) > 500
    assert "weight" in up.validate_tags(heavy)[0][0]
    assert up.validate_tags([])[1] == ["no tags"]


def test_tag_weight_warns_above_460():
    tags = ["t%02d" % i + "x" * 15 for i in range(24)]  # 24 tags of 18 characters: 24 * 18 + 23 = 455
    assert up.tag_weight(tags) == 455
    assert up.validate_tags(tags) == ([], [])
    tags.append("abcdefghijklm")  # + 13 + 1 = 469
    errors, warnings = up.validate_tags(tags)
    assert errors == [] and any("469" in w and "460" in w for w in warnings)


def test_parse_chapters():
    chapters, errors = up.parse_chapters("Intro text\n0:00 Intro\n12:30 Middle\n1:02:03 Q&A\nAt 2:30 we talk\n0:75 Bad")
    assert chapters == [(0, "Intro"), (750, "Middle"), (3723, "Q&A")]
    assert len(errors) == 1 and "0:75" in errors[0]
    assert up.parse_chapters("no chapters here") == ([], [])


def test_validate_chapters_rules():
    good = [(0, "a"), (45, "b"), (190, "c")]
    assert up.validate_chapters(good, 300.0) == ([], [])
    assert up.validate_chapters([], 300.0) == ([], [])
    assert any("0:00" in e for e in up.validate_chapters([(5, "a"), (45, "b"), (190, "c")], 300.0)[0])
    assert any("at least 3" in e for e in up.validate_chapters([(0, "a"), (45, "b")], 300.0)[0])
    assert any("10 s apart" in e for e in up.validate_chapters([(0, "a"), (5, "b"), (60, "c")], 300.0)[0])
    assert any("does not come after" in e for e in up.validate_chapters([(0, "a"), (60, "b"), (30, "c")], 300.0)[0])
    errors, _ = up.validate_chapters(good, 195.0)  # last chapter starts 5 s before the end
    assert any("before the end" in e for e in errors)
    errors, warnings = up.validate_chapters(good, None)
    assert errors == [] and "duration unknown" in warnings[0]


def test_validate_metadata_required_flags_and_channel():
    errors, _ = up.validate_metadata(base_meta())
    assert errors == []
    meta = base_meta()
    del meta["made_for_kids"]
    assert any("made_for_kids" in e for e in up.validate_metadata(meta)[0])
    assert any("synthetic_media" in e for e in up.validate_metadata(base_meta(synthetic_media="false"))[0])
    assert any("category_id" in e for e in up.validate_metadata(base_meta(category_id="music"))[0])
    assert any("language" in e for e in up.validate_metadata(base_meta(default_language="English"))[0])
    no_channel = base_meta()
    del no_channel["channel_id"]
    errors, warnings = up.validate_metadata(no_channel, upload_mode=False)
    assert errors == [] and any("channel_id" in w for w in warnings)
    assert any("channel_id" in e for e in up.validate_metadata(no_channel, upload_mode=True)[0])
    assert any("channel_id" in e for e in up.validate_metadata(base_meta(channel_id="UC123"))[0])
    assert any("privacy" in w for w in up.validate_metadata(base_meta(privacy="public"))[1])


def test_new_fields_are_known_and_thumbnail_text_is_a_list():
    meta = base_meta(publish_at=PUBLISH_AT, thumbnail_text=["3 BILLION", "GONE"],
                     localizations={"de": {"title": "Wie Acme 3 Milliarden verlor", "description": "Was geschah."}})
    errors, warnings = up.validate_metadata(meta)
    assert errors == [] and not any("unknown" in w for w in warnings)
    assert any("thumbnail_text" in w for w in up.validate_metadata(base_meta(thumbnail_text="GONE"))[1])


def test_replacement_character_fails_in_any_text():
    for meta, where in ((base_meta(title="Caf\ufffd prices"), "title"),
                        (base_meta(tags=["acme", "caf\ufffd"]), "tags[1]"),
                        (base_meta(localizations={"de": {"title": "Gr\ufffd\ufffde", "description": "x"}}),
                         "localizations.de.title")):
        errors, _ = up.validate_metadata(meta)
        assert any(e.startswith(where) and "U+FFFD" in e for e in errors), (where, errors)


def test_upload_json_in_another_encoding_fails(tmp_path):
    project = make_project(tmp_path)
    text = json.dumps(base_meta(title="Café prices explained"), ensure_ascii=False)
    (project / "upload.json").write_bytes(text.encode("cp1252"))
    gate = [c for c in checks_of(project) if c.gate == "metadata"]
    assert gate[0].status == "FAIL" and "not UTF-8" in gate[0].detail and "Save it as UTF-8" in gate[0].detail
    (project / "upload.json").write_bytes(text.encode("utf-16"))
    assert any(c.status == "FAIL" and "UTF-16" in c.detail for c in checks_of(project))
    (project / "upload.json").write_bytes(text.encode("utf-8-sig"))  # a UTF-8 BOM is fine
    assert all(c.status != "FAIL" for c in checks_of(project) if c.gate == "metadata")


def test_localizations_rules():
    loc = {"de": {"title": "Wie Acme verlor", "description": "Was geschah."}}
    assert up.validate_localizations(loc, "en") == ([], [])
    assert up.validate_localizations(None, None) == ([], [])
    assert any("defaultLanguageNotSet" in e for e in up.validate_localizations(loc, None)[0])
    assert any("is the default language" in e for e in up.validate_localizations(
        {"en": {"title": "x", "description": "y"}}, "en")[0])
    assert any("is the default language" in e for e in up.validate_localizations(
        {"pt-br": {"title": "x", "description": "y"}}, "pt-BR")[0])
    assert any("not a language code" in e for e in up.validate_localizations(
        {"German": {"title": "x", "description": "y"}}, "en")[0])
    assert any('exactly "title" and "description"' in e for e in up.validate_localizations(
        {"de": {"title": "x"}}, "en")[0])
    errors, warnings = up.validate_localizations({"de": {"title": "x" * 101, "description": "a < b"}}, "en")
    assert any(e.startswith("[de] title is 101") for e in errors) and any(e.startswith("[de] description") for e in
                                                                        errors)
    assert any(w.startswith("[de]") for w in warnings)  # a title over 60 characters is warned per language too
    assert up.validate_localizations("de", "en")[0]


def test_build_body_is_always_private():
    body = up.build_body(base_meta(privacy="public", publish_at="2030-01-01T00:00:00Z", synthetic_media=True))
    assert body["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                              "containsSyntheticMedia": True}
    assert body["snippet"]["categoryId"] == "27" and body["snippet"]["defaultLanguage"] == "en"
    assert up.build_body({k: v for k, v in base_meta().items() if k != "category_id"})["snippet"]["categoryId"] == "22"
    with pytest.raises(RuntimeError):
        up.assert_private({"status": {"privacyStatus": "public"}})
    with pytest.raises(RuntimeError):
        up.assert_private({"status": {"privacyStatus": "private", "publishAt": "2030-01-01T00:00:00Z"}})
    with pytest.raises(RuntimeError):
        up.assert_private({"status": {"privacyStatus": "public", "publishAt": "2030-01-01T00:00:00Z"}},
                          allow_schedule=True)


def test_build_body_localizations_and_schedule():
    meta = base_meta(default_audio_language="en",
                     localizations={"de": {"title": "Wie Acme verlor", "description": "Was geschah."}})
    body = up.build_body(meta, publish_at=PUBLISH_AT_UTC)
    assert body["localizations"] == {"de": {"title": "Wie Acme verlor", "description": "Was geschah."}}
    assert body["snippet"]["defaultAudioLanguage"] == "en"
    assert body["status"]["privacyStatus"] == "private" and body["status"]["publishAt"] == PUBLISH_AT_UTC
    assert up.insert_part(body) == "snippet,status,localizations"
    assert up.insert_part(up.build_body(base_meta())) == "snippet,status"


def test_parse_publish_at():
    assert up.parse_publish_at(PUBLISH_AT, NOW) == (PUBLISH_AT_UTC, None)
    assert up.parse_publish_at("2026-10-20T14:00:00Z", NOW) == (PUBLISH_AT_UTC, None)
    assert up.parse_publish_at("2026-10-20T14:00:00.000Z", NOW) == (PUBLISH_AT_UTC, None)
    for value, why in (("2026-10-20T14:00:00", "no time zone"), ("2026-10-20", "no time zone"),
                       ("next tuesday", "not ISO 8601"), ("", "ISO 8601"), (12, "ISO 8601"),
                       ("2026-10-18T12:10:00Z", "only 10 minutes"), ("2026-10-17T12:00:00Z", "in the past")):
        utc, error = up.parse_publish_at(value, NOW)
        assert utc is None and why in error, (value, error)
    assert up.parse_publish_at("2026-10-18T12:15:00Z", NOW)[0] == "2026-10-18T12:15:00Z"  # exactly 15 minutes


def test_redact_hides_query_strings_and_upload_ids():
    text = ("HTTPSConnectionPool(host='www.googleapis.com', port=443): Max retries exceeded with url: "
            "/upload/youtube/v3/videos?uploadType=resumable&upload_id=SECRET1 (Caused by timeout) "
            "and https://upload.example.com/session?upload_id=SECRET2&x=1, upload_id=SECRET3")
    clean = up.redact(text)
    assert "SECRET" not in clean and "uploadType" not in clean
    assert "/upload/youtube/v3/videos (Caused by timeout)" in clean and "https://upload.example.com/session" in clean
    assert up.describe_error(OSError("boom")) == "OSError: boom"
    assert up.describe_error(up.UploadError("HTTP 400: bad")) == "HTTP 400: bad"
    assert up.describe_error(RuntimeError("")) == "RuntimeError"


def test_iso_duration_and_empty_records():
    assert up.iso_duration_seconds("P0D") == 0
    assert up.iso_duration_seconds("PT8M12S") == 492
    assert up.iso_duration_seconds("garbage") is None and up.iso_duration_seconds(None) is None
    items = [{"video_id": "aaaaaaaaaaa", "added_at": "2026-10-17T10:00:00Z"},
             {"video_id": "bbbbbbbbbbb", "added_at": "2026-10-01T10:00:00Z"},
             {"video_id": "ccccccccccc", "added_at": "2026-10-18T09:00:00Z"}]
    fresh = up.recent(items, NOW)
    assert [i["video_id"] for i in fresh] == ["aaaaaaaaaaa", "ccccccccccc"]
    states = {"aaaaaaaaaaa": {"seconds": 0}, "ccccccccccc": {"seconds": 300.0}}
    assert [i["video_id"] for i in up.find_empty(fresh, states)] == ["aaaaaaaaaaa"]


# --- verdict files ------------------------------------------------------------------------------------------------

def test_parse_verdict():
    text = "**VERDICT:** PASS\nVideo: `render/final v2.mp4` sha256:ABCDEF1234567890\n"
    v = up.parse_verdict(text)
    assert v["verdict"] == "PASS" and v["video_sha"] == "abcdef123456" and v["video_path"] == "render/final v2.mp4"
    assert up.parse_verdict("VERDICT: NO-GO\n")["verdict"] == "NO-GO"
    assert up.parse_verdict("VERDICT: PASS | FAIL\n")["verdict"] != "PASS"
    late = "\n" * 10 + "VERDICT: PASS\n"
    assert up.parse_verdict(late)["verdict"] is None
    gate = "VERDICT: GO\nVideo: render/a.mp4 sha256:111111111111\nMetadata: upload.json sha256:222222222222\n"
    assert up.parse_verdict(gate)["metadata_sha"] == "222222222222"


def test_newest_verdict_by_date_then_suffix(tmp_path):
    for name in ("RENDER-AUDIT-2026-10-09.md", "RENDER-AUDIT-2026-10-17-9.md", "RENDER-AUDIT-2026-10-18.md",
                 "RENDER-AUDIT-2026-10-18-2.md", "RENDER-AUDIT-draft.md", "UPLOAD-GATE-2026-10-20.md"):
        (tmp_path / name).write_text("VERDICT: PASS\n", encoding="utf-8")
    assert up.newest_verdict(tmp_path, "RENDER-AUDIT").name == "RENDER-AUDIT-2026-10-18-2.md"
    assert up.newest_verdict(tmp_path, "UPLOAD-GATE").name == "UPLOAD-GATE-2026-10-20.md"
    assert up.newest_verdict(tmp_path / "missing", "RENDER-AUDIT") is None
    assert up.verdict_key("UPLOAD-GATE-2026-10-18-3.md") == ("UPLOAD-GATE", "2026-10-18", 3)


def test_explicit_dash_one_is_newer_than_no_suffix(tmp_path):
    video_sha = "ab" * 32
    (tmp_path / f"RENDER-AUDIT-{DATE}.md").write_text(
        f"VERDICT: PASS\nVideo: render/final.mp4 sha256:{video_sha[:12]}\n", encoding="utf-8")
    (tmp_path / f"RENDER-AUDIT-{DATE}-1.md").write_text(
        f"VERDICT: FAIL\nVideo: render/final.mp4 sha256:{video_sha[:12]}\n", encoding="utf-8")
    assert up.newest_verdict(tmp_path, "RENDER-AUDIT").name == f"RENDER-AUDIT-{DATE}-1.md"
    assert not up.check_review(tmp_path, "RENDER-AUDIT", video_sha)[0]


def test_verdict_file_encodings_and_crlf(tmp_path):
    video_sha = "cd" * 32
    text = f"VERDICT: PASS\r\nVideo: render/final.mp4 sha256:{video_sha[:12]}\r\n"
    for encoding in ("utf-8", "utf-8-sig", "utf-16"):  # utf-16: Windows PowerShell 5.1's `>` default
        (tmp_path / f"RENDER-AUDIT-{DATE}.md").write_bytes(text.encode(encoding))
        ok, detail = up.check_review(tmp_path, "RENDER-AUDIT", video_sha)
        assert ok, (encoding, detail)


def test_sha_mismatch_fails(tmp_path):
    project = make_project(tmp_path)
    qa, video_sha = project / "qa", sha(project / "render" / "final.mp4")
    assert up.check_review(qa, "RENDER-AUDIT", video_sha)[0]
    (qa / f"RENDER-AUDIT-{DATE}-2.md").write_text("VERDICT: PASS\nVideo: render/final.mp4 sha256:000000000000\n",
                                                  encoding="utf-8")
    ok, detail = up.check_review(qa, "RENDER-AUDIT", video_sha)
    assert not ok and "-2.md" in detail and "000000000000" in detail
    (qa / "render-check.json").write_text(json.dumps({"verdict": "PASS", "sha256": "0" * 64}), encoding="utf-8")
    assert not up.check_render_json(qa, video_sha)[0]


def test_review_fails_when_verdict_is_not_pass(tmp_path):
    project = make_project(tmp_path, reviews=False)
    write_reviews(project, audit="FAIL", gate="NO-GO")
    video_sha = sha(project / "render" / "final.mp4")
    assert not up.check_review(project / "qa", "RENDER-AUDIT", video_sha)[0]
    assert not up.check_review(project / "qa", "UPLOAD-GATE", video_sha, sha(project / "upload.json"))[0]


def test_upload_gate_binds_metadata(tmp_path):
    project = make_project(tmp_path)
    meta = json.loads((project / "upload.json").read_text(encoding="utf-8"))
    meta["title"] = "A different title after the review"
    (project / "upload.json").write_text(json.dumps(meta), encoding="utf-8")
    plan = up.build_plan(project)
    gate = [c for c in plan.checks if c.gate == "upload gate"]
    assert gate[0].status == "FAIL" and "upload.json changed" in gate[0].detail


# --- dry run ------------------------------------------------------------------------------------------------------

def test_dry_run_end_to_end(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(up, "new_session", no_network)
    monkeypatch.setattr(up, "load_upload_credentials", no_network)
    project = make_project(tmp_path, thumbnail=True)
    code = up.main([str(project)])
    out = capsys.readouterr().out
    assert code == 0, out
    lines = out.strip().splitlines()
    assert lines[-1] == "DRY RUN - nothing was uploaded. Add --upload to upload as PRIVATE."
    assert "duration unknown" in out and "WARN" in out  # the fake mp4 has no readable duration
    assert f"Video: render/final.mp4 sha256:{sha(project / 'render' / 'final.mp4')[:12]}" in out
    assert f"Metadata: upload.json sha256:{sha(project / 'upload.json')[:12]}" in out
    assert "Checked only with --upload" in out
    assert str(tmp_path) not in out
    assert not (project / "qa" / "upload-log.json").exists()
    assert not (project / "qa" / up.LOCK_NAME).exists()  # the dry run writes nothing, not even a lock


def test_dry_run_json(tmp_path, capsys):
    project = make_project(tmp_path)
    assert up.main([str(project), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["mode"] == "dry-run" and result["video"] == "render/final.mp4"
    assert result["publish_at"] is None
    assert {c["gate"] for c in result["checks"]} >= {"metadata", "chapters", "render check", "render audit",
                                                     "upload gate", "duplicate", "thumbnail", "token"}


def test_dry_run_fails_without_reviews(tmp_path, capsys):
    project = make_project(tmp_path, reviews=False)
    assert up.main([str(project)]) == 1
    out = capsys.readouterr().out
    assert "render-check.json missing" in out and "RENDER-AUDIT" in out and "UPLOAD-GATE" in out
    assert out.strip().splitlines()[-1].startswith("DRY RUN - nothing was uploaded.")


def test_duplicate_detection(tmp_path, capsys):
    project = make_project(tmp_path)
    log = {"uploads": [{"video_id": "abcdefghijk", "sha256": sha(project / "render" / "final.mp4"),
                        "uploaded_at": "2026-10-17T10:00:00+00:00"}]}
    (project / "qa" / "upload-log.json").write_text(json.dumps(log), encoding="utf-8")
    assert up.main([str(project)]) == 1
    assert "already uploaded as abcdefghijk" in capsys.readouterr().out
    assert up.main([str(project), "--again"]) == 0
    assert "--again uploads a second copy" in capsys.readouterr().out
    (project / "qa" / "upload-log.json").write_text("{broken", encoding="utf-8")
    assert up.main([str(project), "--again"]) == 1


def test_skip_review_reason_length(tmp_path, capsys):
    project = make_project(tmp_path, reviews=False)
    (project / "qa" / "render-check.json").write_text(
        json.dumps({"verdict": "PASS", "sha256": sha(project / "render" / "final.mp4")}), encoding="utf-8")
    assert up.main([str(project), "--skip-review", "too short"]) == 2
    assert "12 characters" in capsys.readouterr().err
    assert up.main([str(project), "--skip-review", "reviewer unavailable, test channel"]) == 0
    out = capsys.readouterr().out
    assert out.count("skipped by --skip-review") == 2


def test_skip_review_never_overrides_a_rejection_of_this_file(tmp_path, capsys):
    project = make_project(tmp_path, reviews=False)
    write_reviews(project, gate="NO-GO")  # the upload-checker rejected these exact bytes
    reason = "reviewer unavailable, test channel"
    assert up.main([str(project), "--skip-review", reason]) == 1
    out = capsys.readouterr().out
    assert "does not override a rejection" in out and "skipped by --skip-review" not in out
    # After upload.json changes, the NO-GO is outdated: skipping the new review is allowed again.
    meta = json.loads((project / "upload.json").read_text(encoding="utf-8"))
    meta["title"] = "A fixed title after the NO-GO"
    (project / "upload.json").write_text(json.dumps(meta), encoding="utf-8")
    assert up.main([str(project), "--skip-review", reason]) == 0


def test_skip_review_does_not_skip_render_check(tmp_path, capsys):
    project = make_project(tmp_path, reviews=False)
    assert up.main([str(project), "--skip-review", "reviewer unavailable, test channel"]) == 1


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs ffmpeg and ffprobe")
def test_duration_from_real_video_drives_the_chapter_end_check(tmp_path):
    video = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-y", str(video)],
                   check=True, timeout=60)
    duration = up.probe_duration(video)
    assert duration == pytest.approx(2.0, abs=0.2)
    errors, warnings = up.validate_chapters([(0, "a"), (45, "b"), (190, "c")], duration)
    assert any("before the end" in e for e in errors) and warnings == []


def test_thumbnail_checks(tmp_path):
    project = make_project(tmp_path)
    with (project / "big.png").open("wb") as f:
        f.truncate(50 * 1024 * 1024 + 1)  # thumbnails.set takes up to 52428800 bytes
    (project / "4k.png").write_bytes(b"0" * (2 * 1024 * 1024 + 1))  # over the old 2 MB, a valid 4K thumbnail
    (project / "art.gif").write_bytes(b"GIF89a")
    assert up.check_thumbnail(project, None)[1].status == "PASS"
    assert up.check_thumbnail(project, "4k.png")[1].status == "PASS"
    assert up.check_thumbnail(project, "big.png")[1].status == "FAIL"
    assert "50 MB" in up.check_thumbnail(project, "big.png")[1].detail
    assert up.check_thumbnail(project, "art.gif")[1].status == "FAIL"
    assert up.check_thumbnail(project, "missing.jpg")[1].status == "FAIL"
    assert up.check_thumbnail(project, "../outside.jpg")[1].status == "FAIL"


def test_dry_run_token_line(tmp_path, capsys, monkeypatch, home):
    at(monkeypatch)
    project = make_project(tmp_path)
    assert up.main([str(project)]) == 0
    assert "no upload token for profile 'default'" in capsys.readouterr().out
    write_token(home, record={"refresh_fingerprint": "abc", "issued_at": "2026-10-12T18:00:00+00:00",
                              "refresh_expires_at": "2026-10-19T18:00:00+00:00", "channel_id": CHANNEL})
    assert up.main([str(project)]) == 0  # a warning never fails the dry run
    out = capsys.readouterr().out
    token_line = [ln for ln in out.splitlines() if ln.startswith("token")][0]
    assert "WARN" in token_line and "expires in 30 hours" in token_line and "--login" in token_line
    assert '"my video" --profile default' in token_line and "upload.py" in token_line   # project folder, no abs path
    assert "fake-refresh-token" not in out
    write_token(home, record={"issued_at": "2026-10-17T12:00:00+00:00", "channel_id": OTHER_CHANNEL})
    up.main([str(project)])
    assert f"logged in to {OTHER_CHANNEL}" in capsys.readouterr().out


def test_dry_run_reports_a_lock(tmp_path, capsys):
    project = make_project(tmp_path)
    (project / "qa" / up.LOCK_NAME).write_text(json.dumps({"pid": 4242, "started_at": up.iso(up.utcnow())}),
                                               encoding="utf-8")
    assert up.main([str(project)]) == 0
    out = capsys.readouterr().out
    assert "lock" in out and "pid 4242" in out


# --- scheduling (opt-in) ------------------------------------------------------------------------------------------

def test_publish_at_without_schedule_is_ignored_with_a_warning(tmp_path, capsys, monkeypatch):
    at(monkeypatch)
    project = make_project(tmp_path, publish_at=PUBLISH_AT)
    assert up.main([str(project)]) == 0
    assert "publish_at is ignored: pass --schedule" in capsys.readouterr().out
    assert up.build_plan(project).publish_at is None


def test_schedule_dry_run(tmp_path, capsys, monkeypatch):
    at(monkeypatch)
    project = make_project(tmp_path, publish_at=PUBLISH_AT)
    assert up.main([str(project), "--schedule", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["publish_at"] == PUBLISH_AT_UTC
    assert any(c["gate"] == "schedule" and c["status"] == "PASS" for c in result["checks"])
    near = make_project(tmp_path / "near", publish_at="2026-10-18T12:10:00Z")
    assert up.main([str(near), "--schedule"]) == 1
    assert "at least 15 minutes ahead" in capsys.readouterr().out
    missing = make_project(tmp_path / "missing")
    assert up.main([str(missing), "--schedule"]) == 1
    assert "--schedule needs publish_at" in capsys.readouterr().out


def test_schedule_upload_end_to_end(tmp_path, capsys, monkeypatch):
    at(monkeypatch)
    project = make_project(tmp_path, publish_at=PUBLISH_AT)
    answer = {"id": "abcdefghijk", "status": {"privacyStatus": "private", "publishAt": PUBLISH_AT_UTC}}
    session = fake_upload(monkeypatch, [
        channels_ok(), playlist(),
        *upload_ok(answer),
        ("GET", FakeResponse(200, body={"items": [{"status": {"privacyStatus": "private"}}]})),  # stale read
        ("GET", FakeResponse(200, body={"items": [{"status": {"privacyStatus": "private",
                                                              "publishAt": "2026-10-20T14:00:00.000Z"}}]})),
    ])
    assert up.main([str(project), "--upload", "--schedule"]) == 0
    out = capsys.readouterr().out
    assert "SCHEDULED: abcdefghijk is private" in out and "2026-10-20T14:00:00.000Z" in out
    init = session.calls[2]
    assert init[2]["json"]["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False,
                                         "containsSyntheticMedia": False, "publishAt": PUBLISH_AT_UTC}
    assert session.calls[4][2]["params"] == {"part": "status", "id": "abcdefghijk"}
    entry = log_entry(project)
    assert entry["publish_at"] == PUBLISH_AT_UTC and entry["schedule_confirmed"] is True
    assert entry["privacy"] == "private"


def test_schedule_read_back_not_private_is_an_alarm(tmp_path, capsys, monkeypatch):
    at(monkeypatch)
    project = make_project(tmp_path, publish_at=PUBLISH_AT, thumbnail=True)
    fake_upload(monkeypatch, [
        channels_ok(), playlist(), *upload_ok(),
        ("GET", FakeResponse(200, body={"items": [{"status": {"privacyStatus": "public"}}]})),
    ])  # no thumbnails.set call is scripted: the alarm must stop before it
    assert up.main([str(project), "--upload", "--schedule"]) == 1
    out = capsys.readouterr().out
    assert "ALARM" in out and "'public'" in out
    entry = log_entry(project)
    assert entry["schedule_confirmed"] is False and "public" in entry["alarm"]
    assert up.main([str(project)]) == 1
    assert "ALARM at upload" in capsys.readouterr().out


def test_schedule_not_confirmed_exits_1_but_keeps_the_log(tmp_path, capsys, monkeypatch):
    at(monkeypatch)
    project = make_project(tmp_path, publish_at=PUBLISH_AT)
    stale = ("GET", FakeResponse(200, body={"items": [{"status": {"privacyStatus": "private"}}]}))
    fake_upload(monkeypatch, [channels_ok(), playlist(), *upload_ok()] + [stale] * up.READBACK_TRIES)
    assert up.main([str(project), "--upload", "--schedule"]) == 1
    assert "could not confirm the schedule" in capsys.readouterr().out
    assert log_entry(project)["video_id"] == "abcdefghijk"
    assert up.main([str(project)]) == 1
    assert "schedule was never confirmed" in capsys.readouterr().out


def test_schedule_margin_is_measured_again_before_the_insert(tmp_path, capsys, monkeypatch):
    clock = at(monkeypatch)
    project = make_project(tmp_path, publish_at=PUBLISH_AT)

    def late_answer(_url, _kwargs):
        clock["now"] = dt.datetime(2026, 10, 20, 13, 50, tzinfo=UTC)  # 10 minutes before publish_at
        return FakeResponse(200, body={"items": []})

    session = fake_upload(monkeypatch, [channels_ok(), ("GET", late_answer)])
    assert up.main([str(project), "--upload", "--schedule"]) == 1
    assert "UPLOAD REFUSED - publish_at" in capsys.readouterr().out
    assert [c[0] for c in session.calls] == ["GET", "GET"]
    assert not (project / "qa" / "upload-log.json").exists()


def test_schedule_margin_lost_during_upload_stops_before_the_last_chunk(tmp_path, capsys, monkeypatch):
    clock = at(monkeypatch)
    project = make_project(tmp_path, publish_at=PUBLISH_AT)

    def slow_start(_url, _kwargs):
        clock["now"] = dt.datetime(2026, 10, 20, 13, 55, tzinfo=UTC)
        return FakeResponse(200, headers={"Location": "https://upload.example.com/session/1"})

    session = fake_upload(monkeypatch, [channels_ok(), playlist(), ("POST", slow_start)])
    assert up.main([str(project), "--upload", "--schedule"]) == 1
    assert "UPLOAD STOPPED before the last chunk" in capsys.readouterr().out
    assert "PUT" not in [c[0] for c in session.calls]
    assert not (project / "qa" / "upload-log.json").exists()


def test_resumable_upload_guard_runs_before_the_last_chunk_only(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(bytes(range(25)))
    seen = []

    def guard():
        seen.append(len(session.calls))
        raise up.ScheduleTooClose("too close")

    session = FakeSession([
        ("POST", FakeResponse(200, headers={"Location": "https://upload.example.com/s"})),
        ("PUT", FakeResponse(308, headers={"Range": "bytes=0-9"})),
        ("PUT", FakeResponse(308, headers={"Range": "bytes=0-19"})),
    ])
    body = up.build_body(base_meta(), publish_at=PUBLISH_AT_UTC)
    with pytest.raises(up.ScheduleTooClose):
        up.resumable_upload(session, "tok", video, body, chunk_size=10, sleep=lambda s: None, allow_schedule=True,
                            before_last_chunk=guard)
    assert seen == [3]  # after the POST and two chunks; the chunk with bytes 20-24 was never sent
    with pytest.raises(RuntimeError):  # a scheduled body needs the explicit permission
        up.resumable_upload(FakeSession([]), "tok", video, body)


# --- --upload -----------------------------------------------------------------------------------------------------

def test_upload_with_failing_gate_never_touches_network(tmp_path, capsys, monkeypatch):
    import requests
    for name in ("get", "post", "put", "request"):
        monkeypatch.setattr(requests, name, no_network)
    monkeypatch.setattr(requests.Session, "request", no_network)
    monkeypatch.setattr(up, "new_session", no_network)
    monkeypatch.setattr(up, "load_upload_credentials", no_network)
    project = make_project(tmp_path, reviews=False)
    assert up.main([str(project), "--upload"]) == 1
    assert "UPLOAD REFUSED" in capsys.readouterr().out
    assert not (project / "qa" / up.LOCK_NAME).exists()


def test_upload_needs_channel_id(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(up, "new_session", no_network)
    project = make_project(tmp_path, channel_id=None)
    assert up.main([str(project)]) == 0  # dry run only warns
    assert up.main([str(project), "--upload"]) == 1


def test_upload_without_token_exits_2_and_releases_the_lock(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(up, "new_session", no_network)
    project = make_project(tmp_path)
    assert up.main([str(project), "--upload", "--profile", "mychannel"]) == 2
    err = capsys.readouterr().err
    assert ("--login" in err and "--profile mychannel" in err) or "pip install" in err
    assert not (project / "qa" / up.LOCK_NAME).exists()


def test_identity_mismatch_exits_1(tmp_path, capsys, monkeypatch):
    session = fake_upload(monkeypatch, [channels_ok(OTHER_CHANNEL)])
    project = make_project(tmp_path)
    assert up.main([str(project), "--upload"]) == 1
    out = capsys.readouterr().out
    assert "WRONG CHANNEL" in out and OTHER_CHANNEL in out
    assert [c[0] for c in session.calls] == ["GET"]
    assert session.calls[0][2]["params"] == {"part": "id,contentDetails", "mine": "true"}
    assert not (project / "qa" / "upload-log.json").exists()


def test_upload_end_to_end_with_fake_session(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path, thumbnail=True)
    lock = project / "qa" / up.LOCK_NAME

    def put(_url, _kwargs):
        assert lock.is_file()  # the lock is held while the bytes go up
        return FakeResponse(200, body={"id": "abcdefghijk", "status": {"privacyStatus": "private"}})

    def thumbnail(_url, _kwargs):
        entry = log_entry(project)  # the id is in the log before the thumbnail step starts
        assert entry["video_id"] == "abcdefghijk" and entry["thumbnail"] == "pending"
        return FakeResponse(200, body={"items": []})

    session = fake_upload(monkeypatch, [
        channels_ok(),
        playlist(("zzzzzzzzzzz", "Some older video", "2026-09-01T10:00:00Z")),
        ("POST", FakeResponse(200, headers={"Location": "https://upload.example.com/session/1"})),
        ("PUT", put),
        ("POST", thumbnail),
    ])
    assert up.main([str(project), "--upload", "--skip-review", "not needed here but recorded"]) == 0
    out = capsys.readouterr().out
    assert "https://studio.youtube.com/video/abcdefghijk/edit" in out
    assert session.calls[1][2]["params"] == {"part": "snippet", "playlistId": UPLOADS, "maxResults": 50}
    init = session.calls[2]
    assert init[2]["json"]["status"]["privacyStatus"] == "private" and "publishAt" not in init[2]["json"]["status"]
    assert init[2]["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    assert session.calls[4][2]["params"] == {"videoId": "abcdefghijk"}
    entry = log_entry(project)
    assert entry["video_id"] == "abcdefghijk" and entry["privacy"] == "private"
    assert entry["video"] == "render/final.mp4" and entry["channel_id"] == CHANNEL
    assert entry["sha256"] == sha(project / "render" / "final.mp4")
    assert entry["skip_review_reason"] == "not needed here but recorded"
    assert entry["thumbnail_http"] == 200 and entry["thumbnail"] == "set"
    assert "publish_at" not in entry
    assert str(tmp_path) not in (project / "qa" / "upload-log.json").read_text(encoding="utf-8")
    assert not lock.exists()
    # The same bytes are now a duplicate.
    assert up.main([str(project)]) == 1


def test_localizations_are_sent_with_their_part(tmp_path, capsys, monkeypatch):
    loc = {"de": {"title": "Wie Acme 3 Milliarden verlor", "description": "Was geschah und warum."}}
    project = make_project(tmp_path, localizations=loc, publish_at=PUBLISH_AT)  # publish_at without --schedule
    session = fake_upload(monkeypatch, [channels_ok(), playlist(), *upload_ok()])
    assert up.main([str(project), "--upload"]) == 0
    out = capsys.readouterr().out
    assert "translations: de" in out and "publish_at is ignored" in out
    init = session.calls[2]
    assert init[2]["params"]["part"] == "snippet,status,localizations"
    assert init[2]["json"]["localizations"] == loc
    assert "publishAt" not in init[2]["json"]["status"]


def test_same_title_on_the_channel_refuses_unless_again(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)
    title = base_meta()["title"]
    session = fake_upload(monkeypatch, [channels_ok(), playlist(("lostupload1", title, "2026-09-01T10:00:00Z"))])
    assert up.main([str(project), "--upload"]) == 1
    out = capsys.readouterr().out
    assert "already has a video with this exact title (lostupload1)" in out and "--again" in out
    assert [c[0] for c in session.calls] == ["GET", "GET"]
    assert not (project / "qa" / "upload-log.json").exists()
    fake_upload(monkeypatch, [channels_ok(), playlist(("lostupload1", f"  {title} ", "2026-09-01T10:00:00Z")),
                              *upload_ok()])
    assert up.main([str(project), "--upload", "--again"]) == 0
    assert "--again uploads anyway" in capsys.readouterr().out


def test_recent_empty_record_is_reported(tmp_path, capsys, monkeypatch):
    at(monkeypatch)
    project = make_project(tmp_path)
    session = fake_upload(monkeypatch, [
        channels_ok(),
        playlist(("emptyrecord", "Another video", "2026-10-17T10:00:00Z"),
                 ("oldvideo123", "Old video", "2026-08-01T10:00:00Z")),
        ("GET", FakeResponse(200, body={"items": [{"id": "emptyrecord", "contentDetails": {"duration": "P0D"},
                                                   "status": {"uploadStatus": "uploaded"}}]})),
        *upload_ok(),
    ])
    assert up.main([str(project), "--upload"]) == 0
    out = capsys.readouterr().out
    assert "emptyrecord" in out and "P0D" in out and "oldvideo123" not in out
    assert session.calls[2][2]["params"]["id"] == "emptyrecord"  # only the recent upload is looked up


def test_channel_without_uploads_and_listing_errors(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)
    fake_upload(monkeypatch, [channels_ok(), ("GET", FakeResponse(404, body={"error": {"code": 404}})),
                              *upload_ok()])
    assert up.main([str(project), "--upload"]) == 0  # 404: a new channel's uploads playlist may not exist yet
    other = make_project(tmp_path / "second")
    session = fake_upload(monkeypatch, [channels_ok(), ("GET", FakeResponse(500, body={"error": "backend"}))])
    assert up.main([str(other), "--upload"]) == 1
    assert "could not list the channel's recent uploads" in capsys.readouterr().out
    assert len(session.calls) == 2


def test_thumbnail_403_is_a_warning_and_a_rerun_does_not_upload(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path, thumbnail=True)
    fake_upload(monkeypatch, [channels_ok(), playlist(), *upload_ok(),
                              ("POST", FakeResponse(403, body={"error": {"message": "forbidden"}}))])
    assert up.main([str(project), "--upload"]) == 0
    out = capsys.readouterr().out
    assert "WARNING: thumbnail not set (HTTP 403)" in out and "phone verification" in out
    entry = log_entry(project)
    assert entry["thumbnail"] == "failed" and entry["thumbnail_http"] == 403
    monkeypatch.setattr(up, "new_session", no_network)
    monkeypatch.setattr(up, "load_upload_credentials", no_network)
    assert up.main([str(project), "--upload"]) == 1
    out = capsys.readouterr().out
    assert "already uploaded as abcdefghijk" in out and "thumbnail was not set" in out
    assert "too many" in up.thumbnail_hint(429) and up.thumbnail_hint(500) == ""


def test_upload_errors_never_show_query_strings(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)
    leak = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&upload_id=SECRET-SESSION"
    fake_upload(monkeypatch, [channels_ok(), playlist(),
                              ("POST", FakeResponse(400, body={"error": {"message": f"bad request for {leak}"}}))])
    assert up.main([str(project), "--upload", "--json"]) == 1
    captured = capsys.readouterr()
    assert "UPLOAD FAILED: starting the upload failed: HTTP 400" in captured.err
    assert "SECRET-SESSION" not in captured.err + captured.out and "upload_id" not in captured.err + captured.out
    error = OSError("Max retries exceeded with url: /youtube/v3/channels?part=id&upload_id=SECRET2 (Caused by x)")
    fake_upload(monkeypatch, [("GET", error)])
    assert up.main([str(project), "--upload"]) == 1
    out = capsys.readouterr().out
    assert "could not read the token's channel" in out and "SECRET2" not in out and "Caused by x" in out


def test_upload_log_not_writable_still_reports_the_id_and_exits_1(tmp_path, capsys, monkeypatch):
    project = make_project(tmp_path)
    fake_upload(monkeypatch, [channels_ok(), playlist(), *upload_ok()])

    def broken(_path, _entries):
        raise PermissionError(13, "locked by a sync client")

    monkeypatch.setattr(up, "write_upload_log", broken)
    assert up.main([str(project), "--upload"]) == 1
    out = capsys.readouterr().out
    assert "UPLOADED: abcdefghijk" in out and '"video_id": "abcdefghijk"' in out and "add it before running again" in out


# --- lock ---------------------------------------------------------------------------------------------------------

def test_second_run_is_refused_while_the_lock_is_held(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(up, "new_session", no_network)
    monkeypatch.setattr(up, "load_upload_credentials", no_network)
    project = make_project(tmp_path)
    lock = project / "qa" / up.LOCK_NAME
    content = json.dumps({"pid": 4242, "started_at": up.iso(up.utcnow() - dt.timedelta(hours=1))})
    lock.write_text(content, encoding="utf-8")
    assert up.main([str(project), "--upload"]) == 1
    out = capsys.readouterr().out
    assert "another upload of this project holds" in out and "pid 4242" in out
    assert lock.read_text(encoding="utf-8") == content  # someone else's lock is never touched


def test_stale_lock_is_replaced_and_released(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(up, "new_session", no_network)
    project = make_project(tmp_path, reviews=False)  # the gates fail after the lock is taken
    lock = project / "qa" / up.LOCK_NAME
    lock.write_text(json.dumps({"pid": 4242, "started_at": up.iso(up.utcnow() - dt.timedelta(hours=7))}),
                    encoding="utf-8")
    assert up.main([str(project), "--upload"]) == 1
    out = capsys.readouterr().out
    assert "replaced a stale lock" in out and "UPLOAD REFUSED - " in out
    assert not lock.exists()


def test_upload_lock_context(tmp_path):
    qa = tmp_path / "qa"
    qa.mkdir()
    with up.upload_lock(qa) as (held, note):
        assert held and note == ""
        data = json.loads((qa / up.LOCK_NAME).read_text(encoding="utf-8"))
        assert set(data) == {"pid", "started_at"}
        with up.upload_lock(qa) as (second, why):
            assert not second and "pid" in why
    assert not (qa / up.LOCK_NAME).exists()
    with pytest.raises(ValueError):
        with up.upload_lock(qa) as (held, _):
            raise ValueError("crash mid-upload")
    assert not (qa / up.LOCK_NAME).exists()
    with up.upload_lock(tmp_path / "no-qa") as (held, _):
        assert held


# --- token health -------------------------------------------------------------------------------------------------

def test_reported_lifetime():
    assert up.reported_lifetime(b'{"access_token": "x", "refresh_token_expires_in": 604799}') == 604799
    assert up.reported_lifetime({"refresh_token_expires_in": "86400"}) == 86400
    assert up.reported_lifetime(b'{"access_token": "x"}') is None
    assert up.reported_lifetime(b"not json") is None and up.reported_lifetime(None) is None


def test_describe_token():
    issued = "2026-10-15T12:00:00+00:00"
    info = up.describe_token({"issued_at": issued, "refresh_expires_at": "2026-10-22T12:00:00+00:00"}, NOW)
    assert info["status"] == "PASS" and info["expiry"] == "reported" and info["age_days"] == 3.0
    assert "4.0 days left" in info["detail"] and info["hours_left"] == 96.0
    soon = up.describe_token({"issued_at": issued, "refresh_expires_at": "2026-10-19T18:00:00+00:00"}, NOW)
    assert soon["status"] == "WARN" and "expires in 30 hours" in soon["detail"]
    gone = up.describe_token({"issued_at": issued, "refresh_expires_at": "2026-10-18T11:00:00+00:00"}, NOW)
    assert gone["status"] == "WARN" and "has passed" in gone["detail"]
    assumed = up.describe_token({"issued_at": "2026-10-12T18:00:00+00:00"}, NOW)
    assert assumed["status"] == "WARN" and "Testing mode" in assumed["detail"] and assumed["expiry"] == "assumed"
    young = up.describe_token({"issued_at": "2026-10-17T12:00:00+00:00"}, NOW)
    assert young["status"] == "PASS" and "no expiry" in young["detail"]
    unlimited = up.describe_token({"first_seen": "2026-09-01T12:00:00+00:00", "no_limit": True}, NOW)
    assert unlimited["status"] == "PASS" and unlimited["expiry"] == "none" and "at least" in unlimited["detail"]
    assert up.describe_token({}, NOW)["status"] == "WARN"


def test_update_token_meta(tmp_path):
    path = tmp_path / "default-upload.meta.json"
    meta = up.update_token_meta(path, "refresh-A", 604800, NOW, issued=True, channel_id=CHANNEL)
    assert meta["issued_at"] == "2026-10-18T12:00:00+00:00" and meta["refresh_expires_at"] == "2026-10-25T12:00:00+00:00"
    assert meta["channel_id"] == CHANNEL and "refresh-A" not in path.read_text(encoding="utf-8")
    later = NOW + dt.timedelta(days=2)
    meta = up.update_token_meta(path, "refresh-A", None, later)  # a refresh without a reported lifetime keeps it
    assert meta["issued_at"] == "2026-10-18T12:00:00+00:00" and meta["refresh_expires_at"] == "2026-10-25T12:00:00+00:00"
    meta = up.update_token_meta(path, "refresh-B", None, later)  # another token: the record starts over
    assert "issued_at" not in meta and meta["first_seen"] == up.iso(later) and "channel_id" not in meta
    meta = up.update_token_meta(path, "refresh-B", None, later + dt.timedelta(days=9))
    assert meta["no_limit"] is True


def test_load_upload_credentials_records_the_reported_lifetime(tmp_path, monkeypatch, home):
    at(monkeypatch)
    token = write_token(home)

    class FakeRequest:
        def __init__(self, session=None):
            self.session = session

        def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
            return types.SimpleNamespace(status=200, data=json.dumps(
                {"access_token": "fake-access-token", "expires_in": 3599,
                 "refresh_token_expires_in": 6 * 86400}).encode())

    class FakeRefreshError(Exception):
        pass

    class FakeCredentials(FakeRefreshCred):
        valid = False
        fail = None

        @classmethod
        def from_authorized_user_file(cls, path, scopes):
            return cls()

        def refresh(self, request):
            if self.fail:
                raise FakeRefreshError(self.fail)
            request("https://oauth2.example.com/token", method="POST")

    monkeypatch.setattr(up, "_google_auth", lambda: (FakeCredentials, FakeRequest, FakeRefreshError, OSError))
    up.load_upload_credentials(token, "LOGIN")
    record = json.loads((home / "tokens" / "default-upload.meta.json").read_text(encoding="utf-8"))
    assert record["refresh_expires_at"] == "2026-10-24T12:00:00+00:00"
    assert "fake-refresh-token" not in json.dumps(record) and "fake-access-token" not in json.dumps(record)
    FakeCredentials.fail = "invalid_grant: Token has been expired or revoked."
    with pytest.raises(up.EnvError, match="invalid_grant"):
        up.load_upload_credentials(token, "LOGIN")


def test_login_is_refused_before_saving_a_token_for_another_channel(capsys, home):
    say = up.Out(False)
    other = FakeSession([("GET", FakeResponse(200, body={"items": [{"id": OTHER_CHANNEL,
                                                                    "snippet": {"title": "Other"}}]}))])
    assert up.finish_login("default", FakeRefreshCred(), 604800, say, expected=CHANNEL, session=other) == 1
    assert "LOGIN NOT SAVED - WRONG CHANNEL" in capsys.readouterr().out
    assert not (home / "tokens" / "default-upload.json").exists()
    assert other.calls[0][2]["params"] == {"part": "id,snippet", "mine": "true"}
    none = FakeSession([("GET", FakeResponse(200, body={"items": []}))])
    assert up.finish_login("default", FakeRefreshCred(), None, say, session=none) == 1
    assert not (home / "tokens" / "default-upload.json").exists()
    with pytest.raises(up.EnvError, match="NOT saved"):
        up.finish_login("default", FakeRefreshCred(), None, say, session=FakeSession([("GET", OSError("offline"))]))
    assert not (home / "tokens").exists() or not (home / "tokens" / "default-upload.json").exists()


def test_login_saves_the_token_and_its_record(capsys, monkeypatch, home):
    at(monkeypatch)
    session = FakeSession([("GET", FakeResponse(200, body={"items": [{"id": CHANNEL, "snippet": {"title": "Mine"}}]}))])
    assert up.finish_login("cooking", FakeRefreshCred(), 604799, up.Out(False), expected=CHANNEL,
                           session=session) == 0
    out = capsys.readouterr().out
    assert "Saved the upload token for profile 'cooking'" in out and "publish the app" in out
    assert "fake-refresh-token" not in out and "fake-access-token" not in out
    assert json.loads((home / "tokens" / "cooking-upload.json").read_text(encoding="utf-8"))["refresh_token"]
    record = json.loads((home / "tokens" / "cooking-upload.meta.json").read_text(encoding="utf-8"))
    assert record["issued_at"] == "2026-10-18T12:00:00+00:00" and record["channel_id"] == CHANNEL
    assert record["refresh_expires_at"] == "2026-10-25T11:59:59+00:00"
    assert "fake-refresh-token" not in json.dumps(record)


def test_expected_channel_from_the_project(tmp_path):
    project = make_project(tmp_path)
    assert up.expected_channel(project) == CHANNEL
    assert up.expected_channel(make_project(tmp_path / "b", channel_id=None)) is None
    with pytest.raises(up.EnvError):
        up.expected_channel(make_project(tmp_path / "c", channel_id="UC123"))


def test_check_token(tmp_path, capsys, monkeypatch, home):
    at(monkeypatch)
    write_token(home, record={"refresh_fingerprint": "abc", "issued_at": "2026-10-12T18:00:00+00:00",
                              "refresh_expires_at": "2026-10-19T18:00:00+00:00"})
    monkeypatch.setattr(up, "load_upload_credentials", lambda *a, **k: FakeRefreshCred())
    monkeypatch.setattr(up, "new_session", lambda: FakeSession([channels_ok()]))
    assert up.main(["--check-token"]) == 0
    out = capsys.readouterr().out
    assert "token: alive" in out and CHANNEL in out and "expires in 30 hours" in out and "WARNING: log in again" in out
    assert "fake-refresh-token" not in out and "fake-access-token" not in out
    record = json.loads((home / "tokens" / "default-upload.meta.json").read_text(encoding="utf-8"))
    assert record["channel_id"] == CHANNEL
    project = make_project(tmp_path, channel_id=OTHER_CHANNEL)
    monkeypatch.setattr(up, "new_session", lambda: FakeSession([channels_ok()]))
    assert up.main([str(project), "--check-token", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["channel_match"] is False and result["status"] == "FAIL" and result["hours_left"] == 30.0
    assert result["expiry"] == "reported" and result["channel_ids"] == [CHANNEL]


def test_check_token_without_a_token_exits_2(capsys, monkeypatch):
    monkeypatch.setattr(up, "new_session", no_network)
    assert up.main(["--check-token", "--profile", "nothing"]) == 2
    err = capsys.readouterr().err
    assert "--login --profile nothing" in err or "pip install" in err


def test_modes_are_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        up.main([str(tmp_path), "--upload", "--login"])


# --- resumable protocol -------------------------------------------------------------------------------------------

def test_resumable_upload_resumes_after_308_and_5xx(tmp_path):
    video = tmp_path / "v.mp4"
    content = bytes(range(25))
    video.write_bytes(content)
    session_url = "https://upload.example.com/session/abc"
    done = {"id": "abcdefghijk", "status": {"privacyStatus": "private"}}
    session = FakeSession([
        ("POST", FakeResponse(200, headers={"Location": session_url})),
        ("PUT", FakeResponse(308, headers={"Range": "bytes=0-9"})),
        ("PUT", FakeResponse(308, headers={"Range": "bytes=0-14"})),   # kept only half of the second chunk
        ("PUT", FakeResponse(503)),
        ("PUT", FakeResponse(308, headers={"Range": "bytes=0-19"})),   # status query answer
        ("PUT", ConnectionError("reset")),
        ("PUT", FakeResponse(308, headers={"Range": "bytes=0-19"})),
        ("PUT", FakeResponse(201, body=done)),
    ])
    sleeps = []
    body = up.build_body(base_meta())
    result = up.resumable_upload(session, "tok", video, body, chunk_size=10, sleep=sleeps.append)
    assert result == done
    post = session.calls[0]
    assert post[2]["headers"]["X-Upload-Content-Length"] == "25"
    assert post[2]["headers"]["Authorization"] == "Bearer tok"
    assert post[2]["json"]["status"]["privacyStatus"] == "private"
    puts = [c for c in session.calls if c[0] == "PUT"]
    assert all(c[1] == session_url for c in puts)
    assert [c[2]["headers"]["Content-Range"] for c in puts] == [
        "bytes 0-9/25", "bytes 10-19/25", "bytes 15-24/25", "bytes */25", "bytes 20-24/25", "bytes */25",
        "bytes 20-24/25"]
    assert puts[2][2]["data"] == content[15:25]
    assert puts[3][2]["data"] == b""
    assert puts[-1][2]["data"] == content[20:25]
    assert len(sleeps) == 2


def test_resumable_upload_gives_up(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 12)
    script = [("POST", FakeResponse(200, headers={"location": "https://upload.example.com/s"}))]
    script += [("PUT", FakeResponse(503))] * (up.MAX_RETRIES + 1)
    session = FakeSession(script)
    with pytest.raises(up.UploadError, match="gave up"):
        up.resumable_upload(session, "tok", video, up.build_body(base_meta()), chunk_size=10, sleep=lambda s: None)


def test_resumable_upload_stops_on_client_error(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 12)
    session = FakeSession([("POST", FakeResponse(403, body={"error": {"message": "quotaExceeded"}}))])
    with pytest.raises(up.UploadError, match="HTTP 403"):
        up.resumable_upload(session, "tok", video, up.build_body(base_meta()), sleep=lambda s: None)


def test_resumable_upload_refuses_non_private_body(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        up.resumable_upload(FakeSession([]), "tok", video, {"status": {"privacyStatus": "public"}})


def test_thumbnail_swapped_after_the_review_fails_the_upload_gate(tmp_path):
    project = make_project(tmp_path, thumbnail=True)
    assert up.build_plan(project).ok
    (project / "thumb.jpg").write_bytes(b"\xff\xd8\xff a different fake jpeg")
    gate = [c for c in up.build_plan(project).checks if c.gate == "upload gate"][0]
    assert gate.status == "FAIL" and "thumbnail changed after the review" in gate.detail


def test_upload_gate_without_a_thumbnail_line_fails_when_a_thumbnail_is_set(tmp_path):
    project = make_project(tmp_path, thumbnail=True)
    gate_file = project / "qa" / f"UPLOAD-GATE-{DATE}.md"
    kept = [ln for ln in gate_file.read_text(encoding="utf-8").splitlines() if not ln.startswith("Thumbnail:")]
    gate_file.write_text("\n".join(kept), encoding="utf-8")
    gate = [c for c in up.build_plan(project).checks if c.gate == "upload gate"][0]
    assert gate.status == "FAIL" and "Thumbnail:" in gate.detail
