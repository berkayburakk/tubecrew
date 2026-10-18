"""Tests for skills/check-voiceover/scripts/pick_takes.py (precomputed transcripts; no audio, no faster-whisper)."""
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "check-voiceover" / "scripts" / "pick_takes.py"


def _load():
    spec = importlib.util.spec_from_file_location("pick_takes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pt = _load()

P1 = "Last year the company made 12.5 billion dollars in profit."
P2 = "Most of it came from chips that nobody outside the industry had heard of."
P3 = "Then a small rival in Ohio changed the plan for everyone."
SCRIPT_MD = f"# Story\n\n{P1}\n\n> note: not spoken\n\n{P2}\n\n{P3}\n"


def take_transcript(text, wpm=180):
    """Whisper-style JSON with word timings for `text` read at `wpm`."""
    step = 60.0 / wpm
    words = [{"word": " " + w, "start": round(0.3 + k * step, 3), "end": round(0.3 + k * step + step * 0.9, 3)}
             for k, w in enumerate(text.split())]
    return {"segments": [{"start": words[0]["start"], "end": words[-1]["end"], "text": " " + text, "words": words}]}


def add_take(folder, name, text, wpm=180, audio=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.wav").write_bytes(audio or f"RIFF-fake-{name}".encode())
    if text is not None:
        (folder / f"{name}.json").write_text(json.dumps(take_transcript(text, wpm)), encoding="utf-8")
    return folder / f"{name}.wav"


@pytest.fixture
def project(tmp_path):
    (tmp_path / "script.md").write_text(SCRIPT_MD, encoding="utf-8")
    cand = tmp_path / "voice" / "candidates"
    add_take(cand / "1", "seed-a", P1.replace("12.5", "125"))          # wrong number
    add_take(cand / "1", "seed-b", P1)                                  # clean
    add_take(cand / "1", "seed-c", P1, wpm=70)                          # clean words, far too slow
    add_take(cand / "003", "seed-d", P3.replace("Ohio", "Idaho"))       # garbled name
    return tmp_path


def run(capsys, *args):
    code = pt.main([str(a) for a in args])
    return code, capsys.readouterr()


def report_of(project):
    return json.loads((project / "qa" / "take-pick.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------- what must be heard

def test_required_items_find_numbers_and_names():
    text = ("Then a small rival in Ohio changed the plan. Ohio was ready, and I think OpenAI said 12% of 1,000 "
            "users paid $3 million.")
    items = pt.required_items(text, "en", pt.known_names([text]))
    assert [(i["kind"], i["text"]) for i in items] == [
        ("number", "12 %"), ("number", "1000"), ("number", "$ 3 million"), ("name", "Ohio"), ("name", "OpenAI")]


def test_sentence_start_word_is_a_name_only_when_capitalised_elsewhere():
    paragraphs = ["Nvidia sold more. Most buyers waited.", "Analysts said Nvidia would win."]
    names = pt.known_names(paragraphs)
    assert "Nvidia" in names and "Most" not in names and "Analysts" not in names
    assert [i["text"] for i in pt.required_items(paragraphs[0], "en", names)] == ["Nvidia"]


def test_heard_item_rules():
    heard = "open ai said a.i. would add 125 billion".split()
    name = {"kind": "name", "tokens": ["openai"]}
    acronym = {"kind": "name", "tokens": ["ai"]}
    number = {"kind": "number", "tokens": ["12.5", "billion"]}
    assert pt.heard_item(heard, name) and pt.heard_item(["a", "i", "would"], acronym)
    assert not pt.heard_item(heard, number)
    assert pt.heard_item(["$", "12.5", "billion"], {"kind": "number", "tokens": ["$", "12.5", "billion"]})
    assert not pt.heard_item(["12", "5"], {"kind": "number", "tokens": ["125"]})   # numbers never join


def test_paragraph_ids_are_not_required_words(tmp_path, capsys):
    (tmp_path / "script.md").write_text(f"**P01** {P1}\n\nP02. {P2}\n", encoding="utf-8")
    add_take(tmp_path / "voice" / "candidates" / "1", "seed-a", P1)
    code, _out = run(capsys, tmp_path / "script.md", tmp_path / "voice" / "candidates", "--paragraphs", "1")
    p1 = report_of(tmp_path)["paragraphs"][0]
    assert p1["must_hear"] == ["$ 12.5 billion"] and p1["status"] == "PASS" and code == 0


# ------------------------------------------------------------------------------------------- ranking

def test_picks_the_faithful_take_and_fails_a_paragraph_without_one(project, capsys):
    code, out = run(capsys, project / "script.md", project / "voice" / "candidates")
    assert code == 1
    report = report_of(project)
    assert report["verdict"] == "FAIL" and report["failing"] == [3]
    p1, p3 = report["paragraphs"]
    assert p1["paragraph"] == 1 and p1["status"] == "PASS"
    assert p1["best"] == "voice/candidates/1/seed-b.wav"
    assert p1["must_hear"] == ["$ 12.5 billion"]
    by_name = {Path(t["take"]).name: t for t in p1["takes"]}
    assert by_name["seed-a.wav"]["missing"] == ["$ 12.5 billion"] and not by_name["seed-a.wav"]["usable"]
    assert by_name["seed-c.wav"]["fidelity"] == 1.0 and "pace" in by_name["seed-c.wav"]["rejected"]
    assert by_name["seed-b.wav"]["usable"] and by_name["seed-b.wav"]["transcript"] == "voice/candidates/1/seed-b.json"
    assert p3["status"] == "FAIL" and p3["best"] is None and p3["must_hear"] == ["Ohio"]
    assert "missing Ohio" in p3["takes"][0]["rejected"] and "fidelity below 0.95" in p3["takes"][0]["rejected"]
    assert "BEST  seed-b.wav" in out.out and "regenerate paragraph 3 with new seeds" in out.out
    assert "VERDICT: FAIL" in out.out and "qa/take-pick.json" in out.out
    text = (project / "qa" / "take-pick.json").read_text(encoding="utf-8")
    assert str(project) not in text and "path" not in p1["takes"][0]


def test_tie_goes_to_the_pace_nearest_the_middle_of_the_band(tmp_path, capsys):
    (tmp_path / "script.md").write_text(SCRIPT_MD, encoding="utf-8")
    cand = tmp_path / "candidates"
    add_take(cand / "2", "a-slowish", P2, wpm=152)
    add_take(cand / "2", "b-steady", P2, wpm=185)
    add_take(cand / "2", "c-rushed", P2, wpm=212)
    code, _ = run(capsys, tmp_path / "script.md", cand)
    assert code == 0
    assert Path(report_of(tmp_path)["paragraphs"][0]["best"]).name == "b-steady.wav"


def test_waive_and_min_fidelity(tmp_path, capsys):
    (tmp_path / "script.md").write_text(SCRIPT_MD, encoding="utf-8")
    cand = tmp_path / "candidates"
    add_take(cand / "p3", "only", P3.replace("Ohio", "Oh hi oh"))
    assert run(capsys, tmp_path / "script.md", cand)[0] == 1
    code, _ = run(capsys, tmp_path / "script.md", cand, "--waive", "Ohio", "--min-fidelity", "0.8")
    report = report_of(tmp_path)
    assert code == 0 and report["waived"] == ["Ohio"] and report["min_fidelity"] == 0.8
    assert report["paragraphs"][0]["takes"][0]["missing"] == []


def test_paragraph_filter(project, capsys):
    code, _ = run(capsys, project / "script.md", project / "voice" / "candidates", "--paragraphs", "1")
    assert code == 0 and [r["paragraph"] for r in report_of(project)["paragraphs"]] == [1]
    code, err = run(capsys, project / "script.md", project / "voice" / "candidates", "--paragraphs", "2")
    assert code == 2 and "no candidate folder for paragraph 2" in err.err


def test_empty_paragraph_folder_fails(tmp_path, capsys):
    (tmp_path / "script.md").write_text(SCRIPT_MD, encoding="utf-8")
    (tmp_path / "candidates" / "2").mkdir(parents=True)
    code, out = run(capsys, tmp_path / "script.md", tmp_path / "candidates")
    assert code == 1 and "no takes in candidates/2" in out.out


# ------------------------------------------------------------------------------------------- apply

def test_apply_copies_winners_and_keeps_what_it_replaces(project, capsys):
    dest = project / "voice" / "paragraphs"
    dest.mkdir(parents=True)
    (dest / "p001.wav").write_bytes(b"first round take")
    (dest / "p003.wav").write_bytes(b"untouched")
    code, out = run(capsys, project / "script.md", project / "voice" / "candidates", "--apply", dest)
    assert code == 1
    assert (dest / "p001.wav").read_bytes() == b"RIFF-fake-seed-b"
    assert (dest / "replaced" / "p001-1.wav").read_bytes() == b"first round take"
    assert (dest / "p003.wav").read_bytes() == b"untouched"   # no usable take: nothing copied
    applied = report_of(project)["applied"]
    assert applied == [{"paragraph": 1, "to": "voice/paragraphs/p001.wav",
                        "replaced": ["voice/paragraphs/replaced/p001-1.wav"], "unchanged": False}]
    assert "Applied p1: copied" in out.out
    run(capsys, project / "script.md", project / "voice" / "candidates", "--apply", dest)
    assert report_of(project)["applied"][0]["unchanged"] is True
    assert sorted(p.name for p in (dest / "replaced").iterdir()) == ["p001-1.wav"]


# ------------------------------------------------------------------------------------------- transcripts

def test_takes_without_transcripts_need_faster_whisper(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    (tmp_path / "script.md").write_text(SCRIPT_MD, encoding="utf-8")
    add_take(tmp_path / "candidates" / "2", "bare", None)
    code, err = run(capsys, tmp_path / "script.md", tmp_path / "candidates")
    assert code == 2 and "2/bare.wav has no transcript" in err.err and "pip install faster-whisper" in err.err


def test_transcribes_once_and_reuses_the_saved_transcript(tmp_path, capsys, monkeypatch):
    loads, calls = [], []

    class FakeModel:
        def __init__(self, name, device, compute_type):
            loads.append(name)

        def transcribe(self, path, **options):
            calls.append((Path(path).name, options.get("condition_on_previous_text")))
            words = [types.SimpleNamespace(word=w["word"], start=w["start"], end=w["end"])
                     for w in take_transcript(P2)["segments"][0]["words"]]
            segment = types.SimpleNamespace(start=words[0].start, end=words[-1].end, text=" " + P2, words=words)
            return iter([segment]), types.SimpleNamespace(language="en")

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    (tmp_path / "script.md").write_text(SCRIPT_MD, encoding="utf-8")
    first = add_take(tmp_path / "candidates" / "2", "one", None)
    add_take(tmp_path / "candidates" / "2", "two", None)
    assert run(capsys, tmp_path / "script.md", tmp_path / "candidates")[0] == 0
    assert loads == ["small"] and calls == [("one.wav", False), ("two.wav", False)]
    saved = json.loads(first.with_suffix(".json").read_text(encoding="utf-8"))
    assert len(saved["audio_sha256"]) == 64 and saved["decode"]["condition_on_previous_text"] is False

    monkeypatch.setitem(sys.modules, "faster_whisper", None)   # cached transcripts need no model
    assert run(capsys, tmp_path / "script.md", tmp_path / "candidates")[0] == 0
    first.write_bytes(b"regenerated with a new seed")             # stale transcript: must transcribe again
    code, err = run(capsys, tmp_path / "script.md", tmp_path / "candidates")
    assert code == 2 and "one.wav has no transcript" in err.err


# ------------------------------------------------------------------------------------------- command line

def test_list_numbers_paragraphs_like_voice_check(project, capsys):
    code, out = run(capsys, project / "script.md", "--list")
    assert code == 0
    assert out.out.splitlines() == [f"p001  (10 words)  {P1}", f"p002  (14 words)  {P2}", f"p003  (11 words)  {P3}"]
    code, out = run(capsys, project / "script.md", "--list", "--json")
    assert [p["paragraph"] for p in json.loads(out.out)["paragraphs"]] == [1, 2, 3]


def test_usage_errors(project, capsys):
    assert run(capsys, project / "missing.md", project / "voice" / "candidates")[0] == 2
    assert run(capsys, project / "script.md")[0] == 2
    assert run(capsys, project / "script.md", project / "nowhere")[0] == 2
    (project / "voice" / "candidates" / "9").mkdir()
    code, err = run(capsys, project / "script.md", project / "voice" / "candidates")
    assert code == 2 and "3 paragraphs" in err.err
    empty = project / "empty"
    empty.mkdir()
    code, err = run(capsys, project / "script.md", empty)
    assert code == 2 and "no paragraph folders" in err.err
