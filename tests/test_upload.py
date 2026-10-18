"""Tests for skills/upload-private/scripts/upload.py. No network: every HTTP call goes to a fake session."""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "upload-private" / "scripts" / "upload.py"
CHANNEL = "UCxxxxxxxxxxxxxxxxxxxxxx"
OTHER_CHANNEL = "UCyyyyyyyyyyyyyyyyyyyyyy"
DATE = "2026-10-18"


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


class FakeResponse:
    def __init__(self, status, headers=None, body=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self.text = json.dumps(body) if body is not None else ""

    def json(self):
        return self._body


class FakeSession:
    """Answers calls from a script of (method, response or exception) and records every call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        assert self.script, f"unexpected {method} {url}"
        expected, result = self.script.pop(0)
        assert expected == method, f"expected {expected}, got {method} {url}"
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


def no_network(*_args, **_kwargs):
    raise AssertionError("network was used")


@pytest.fixture
def home(tmp_path, monkeypatch):
    path = tmp_path / "tubecrew-home"
    path.mkdir()
    monkeypatch.setenv("TUBECREW_HOME", str(path))
    return path


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

def test_dry_run_end_to_end(tmp_path, capsys, monkeypatch, home):
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
    assert str(tmp_path) not in out
    assert not (project / "qa" / "upload-log.json").exists()


def test_dry_run_json(tmp_path, capsys, home):
    project = make_project(tmp_path)
    assert up.main([str(project), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["mode"] == "dry-run" and result["video"] == "render/final.mp4"
    assert {c["gate"] for c in result["checks"]} >= {"metadata", "chapters", "render check", "render audit",
                                                     "upload gate", "duplicate", "thumbnail"}


def test_dry_run_fails_without_reviews(tmp_path, capsys, home):
    project = make_project(tmp_path, reviews=False)
    assert up.main([str(project)]) == 1
    out = capsys.readouterr().out
    assert "render-check.json missing" in out and "RENDER-AUDIT" in out and "UPLOAD-GATE" in out
    assert out.strip().splitlines()[-1].startswith("DRY RUN - nothing was uploaded.")


def test_duplicate_detection(tmp_path, capsys, home):
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


def test_skip_review_reason_length(tmp_path, capsys, home):
    project = make_project(tmp_path, reviews=False)
    (project / "qa" / "render-check.json").write_text(
        json.dumps({"verdict": "PASS", "sha256": sha(project / "render" / "final.mp4")}), encoding="utf-8")
    assert up.main([str(project), "--skip-review", "too short"]) == 2
    assert "12 characters" in capsys.readouterr().err
    assert up.main([str(project), "--skip-review", "reviewer unavailable, test channel"]) == 0
    out = capsys.readouterr().out
    assert out.count("skipped by --skip-review") == 2


def test_skip_review_never_overrides_a_rejection_of_this_file(tmp_path, capsys, home):
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


def test_skip_review_does_not_skip_render_check(tmp_path, capsys, home):
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


# --- --upload -----------------------------------------------------------------------------------------------------

def test_upload_with_failing_gate_never_touches_network(tmp_path, capsys, monkeypatch, home):
    import requests
    for name in ("get", "post", "put", "request"):
        monkeypatch.setattr(requests, name, no_network)
    monkeypatch.setattr(requests.Session, "request", no_network)
    monkeypatch.setattr(up, "new_session", no_network)
    monkeypatch.setattr(up, "load_upload_credentials", no_network)
    project = make_project(tmp_path, reviews=False)
    assert up.main([str(project), "--upload"]) == 1
    assert "UPLOAD REFUSED" in capsys.readouterr().out


def test_upload_needs_channel_id(tmp_path, capsys, monkeypatch, home):
    monkeypatch.setattr(up, "new_session", no_network)
    project = make_project(tmp_path, channel_id=None)
    assert up.main([str(project)]) == 0  # dry run only warns
    assert up.main([str(project), "--upload"]) == 1


def test_upload_without_token_exits_2(tmp_path, capsys, monkeypatch, home):
    monkeypatch.setattr(up, "new_session", no_network)
    project = make_project(tmp_path)
    assert up.main([str(project), "--upload", "--profile", "mychannel"]) == 2
    err = capsys.readouterr().err
    assert "--login --profile mychannel" in err or "pip install" in err


def test_identity_mismatch_exits_1(tmp_path, capsys, monkeypatch, home):
    session = FakeSession([("GET", FakeResponse(200, body={"items": [{"id": OTHER_CHANNEL}]}))])
    monkeypatch.setattr(up, "load_upload_credentials", lambda *a, **k: FakeCred())
    monkeypatch.setattr(up, "new_session", lambda: session)
    project = make_project(tmp_path)
    assert up.main([str(project), "--upload"]) == 1
    out = capsys.readouterr().out
    assert "WRONG CHANNEL" in out and OTHER_CHANNEL in out
    assert [c[0] for c in session.calls] == ["GET"]
    assert session.calls[0][2]["params"] == {"part": "id", "mine": "true"}
    assert not (project / "qa" / "upload-log.json").exists()


def test_upload_end_to_end_with_fake_session(tmp_path, capsys, monkeypatch, home):
    project = make_project(tmp_path, thumbnail=True)
    answer ={"id": "abcdefghijk", "status": {"privacyStatus": "private"}, "snippet": {"channelId": CHANNEL}}
    session = FakeSession([
        ("GET", FakeResponse(200, body={"items": [{"id": CHANNEL}]})),
        ("POST", FakeResponse(200, headers={"Location": "https://upload.example.com/session/1"})),
        ("PUT", FakeResponse(200, body=answer)),
        ("POST", FakeResponse(200, body={"items": []})),  # thumbnails.set
    ])
    monkeypatch.setattr(up, "load_upload_credentials", lambda *a, **k: FakeCred())
    monkeypatch.setattr(up, "new_session", lambda: session)
    assert up.main([str(project), "--upload", "--skip-review", "not needed here but recorded"]) == 0
    out = capsys.readouterr().out
    assert "https://studio.youtube.com/video/abcdefghijk/edit" in out
    init = session.calls[1]
    assert init[2]["json"]["status"]["privacyStatus"] == "private"
    assert init[2]["params"] == {"uploadType": "resumable", "part": "snippet,status"}
    assert session.calls[3][2]["params"] == {"videoId": "abcdefghijk"}
    log = json.loads((project / "qa" / "upload-log.json").read_text(encoding="utf-8"))
    entry = log["uploads"][0]
    assert entry["video_id"] == "abcdefghijk" and entry["privacy"] == "private"
    assert entry["video"] == "render/final.mp4" and entry["channel_id"] == CHANNEL
    assert entry["sha256"] == sha(project / "render" / "final.mp4")
    assert entry["skip_review_reason"] == "not needed here but recorded"
    assert entry["thumbnail_http"] == 200
    assert str(tmp_path) not in json.dumps(log)
    # The same bytes are now a duplicate.
    assert up.main([str(project)]) == 1


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
