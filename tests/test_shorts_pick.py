"""Tests for skills/make-shorts/scripts/shorts_pick.py. Pure text and timing: no ffmpeg needed."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "make-shorts" / "scripts" / "shorts_pick.py"


def _load():
    spec = importlib.util.spec_from_file_location("shorts_pick", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sp = _load()

SCRIPT_MD = """# The deal

Acme paid 3 billion dollars for a company that made nothing. The buyer had one reason. They wanted the patents.
So they paid in cash, over one weekend.

And then the lawyers arrived. Why would anyone pay that much for paper? Because paper was the product. Every patent
was a toll booth on a road everyone used. The toll paid back the 3 billion dollars in four years.

## The fall

But the road moved. By the end of the decade, nobody drove on it. You can still buy those patents today. They cost
less than a used car.
"""

WORD_S, GAP_S, SENTENCE_PAUSE_S = 0.32, 0.06, 0.5


def spoken_words(markdown: str, offset: float = 1.0) -> list:
    """Whisper-style words for every spoken word of a script: fixed length, a pause after each sentence."""
    words, t = [], offset
    for line in markdown.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        for w in line.split():
            words.append({"word": " " + w, "start": round(t, 3), "end": round(t + WORD_S, 3)})
            t += WORD_S + GAP_S + (SENTENCE_PAUSE_S if w.endswith((".", "?", "!")) else 0)
    return words


def make_project(tmp_path: Path, script: str = SCRIPT_MD, words=None, with_script: bool = True) -> Path:
    project = tmp_path / "my video"
    (project / "qa").mkdir(parents=True)
    if with_script:
        (project / "script.md").write_text(script, encoding="utf-8")
    words = spoken_words(script) if words is None else words
    (project / "qa" / "voice-check-transcript.json").write_text(json.dumps({"segments": [{"words": words}]}),
                                                               encoding="utf-8")
    return project


def run(capsys, *args):
    code = sp.main([str(a) for a in args])
    return code, capsys.readouterr().out


def report_of(project: Path) -> dict:
    return json.loads((project / "qa" / "shorts-candidates.json").read_text(encoding="utf-8"))


# --- pure helpers ---------------------------------------------------------------------------------------------------

def test_split_sentences_keeps_abbreviations_initials_and_decimals():
    text = "Mr. Smith paid $3.5 billion. J. P. Morgan said no! Was it worth it? Yes."
    assert sp.split_sentences(text) == ["Mr. Smith paid $3.5 billion.", "J. P. Morgan said no!", "Was it worth it?",
                                        "Yes."]
    assert sp.split_sentences("It rose 4.5 percent. then it fell.") == ["It rose 4.5 percent. then it fell."]
    assert sp.split_sentences('He said "Stop." 100 people left.') == ['He said "Stop."', "100 people left."]


def test_backref_start_flags_context_dependent_openings():
    for line in ("And then it broke.", "So they paid.", "This is why it matters.", "That's the catch.",
                 "As we saw, prices rose.", "It never recovered.", "But nobody noticed."):
        assert sp.backref_start(line), line
    for line in ("Acme paid 3 billion dollars.", "Here is a coin flip you cannot win.", "You pay this fee twice.",
                 "There are three ways to lose money.", "Why does a toll road cost nothing?"):
        assert sp.backref_start(line) is None, line


def test_script_sections_follow_headings_and_skip_code_fences():
    sections = sp.script_sections("intro\n# One\na\n```\n# not a heading\n```\n## Two\nb\n")
    assert len(sections) == 3 and "# not a heading" in sections[1]


def test_score_window_signals():
    sentences = [
        {"text": "Acme paid 3 billion dollars for nothing.", "section": 0, "start": 0.0, "end": 3.0, "match": 1.0,
         "exact_edges": True},
        {"text": "Why would anyone pay that much?", "section": 0, "start": 3.5, "end": 6.0, "match": 1.0,
         "exact_edges": True},
        {"text": "Because the patents paid back 3 billion dollars.", "section": 0, "start": 6.5, "end": 30.0,
         "match": 1.0, "exact_edges": True},
        {"text": "But the market moved.", "section": 0, "start": 30.5, "end": 32.0, "match": 1.0,
         "exact_edges": True},
    ]
    result = sp.score_window(sentences, 0, 2)
    names = {s["name"] for s in result["signals"]}
    assert {"number_first", "question_answered", "payoff_short", "loop", "turn_after", "length"} <= names
    assert result["pocket"] is True and result["duration"] == 30.0
    opened_late = sp.score_window(sentences, 3, 3)
    assert opened_late["pocket"] is False and "backref_start" in {s["name"] for s in opened_late["signals"]}
    sentences[0]["text"] = "Welcome back to the channel, today we look at 3 deals."
    assert sp.score_window(sentences, 0, 2)["pocket"] is False


def test_length_points_peak_in_the_middle_band():
    assert sp.length_points(35) == sp.POINTS["length"]
    assert sp.length_points(20) < sp.length_points(25)
    assert sp.length_points(60) < sp.length_points(45)
    assert sp.length_points(170) == 0


def test_retention_adjust_rewards_rewatch_and_punishes_cliffs():
    retention = {"judged": True, "rewatch_bumps": [{"start": 40, "end": 44, "rise_pp": 2.0}],
                 "steepest_drops": [{"start": 100, "end": 110, "loss_pp": 6.0}]}
    up, notes = sp.retention_adjust(30, 60, retention)
    assert up > 0 and "rewatch bump" in notes[0]
    down, notes = sp.retention_adjust(98, 130, retention)
    assert down == -20 and "opens inside a steep drop" in notes[0]
    inside, _ = sp.retention_adjust(80, 120, retention)
    assert inside == -10
    assert sp.retention_adjust(30, 60, dict(retention, judged=False)) == (0.0, [])
    assert sp.retention_adjust(30, 60, None) == (0.0, [])


def test_slug_is_file_safe_and_unique():
    taken = set()
    assert sp.slug("Acme paid $3 billion!", taken) == "acme-paid-3-billion"
    assert sp.slug("Acme paid $3 billion!", taken) == "acme-paid-3-billion-2"
    assert sp.slug("The", set()) == "short"


# --- end to end -----------------------------------------------------------------------------------------------------

def test_picks_self_contained_windows_on_word_and_sentence_boundaries(tmp_path, capsys):
    project = make_project(tmp_path)
    code, out = run(capsys, project, "--min", "5", "--max", "25")
    assert code == 0, out
    report = report_of(project)
    assert report["verdict"] == "PASS" and report["sentences_from"] == "script" and report["match_ratio"] == 1.0
    words = spoken_words(SCRIPT_MD)
    starts, ends = {w["start"] for w in words}, {w["end"] for w in words}
    sentence_ends = {w["end"] for w in words if w["word"].endswith((".", "?", "!"))}
    for cand in report["candidates"]:
        assert cand["pocket"] is True and sp.backref_start(cand["first_line"]) is None
        assert cand["start"] in starts and cand["end"] in sentence_ends and cand["end"] in ends
        assert cand["cut_start"] <= cand["start"] and cand["cut_end"] >= cand["end"]
        assert cand["cut_start"] >= cand["start"] - sp.PAD_START_S - 1e-6
        assert cand["cut_end"] <= cand["end"] + sp.PAD_END_S + 1e-6
        nxt = [w["start"] for w in words if w["start"] > cand["end"]]
        if nxt:
            assert cand["cut_end"] <= (cand["end"] + nxt[0]) / 2 + 1e-6   # never into the next word
    best = report["candidates"][0]
    assert best["rank"] == 1 and best["first_line"].startswith("Acme paid 3 billion")
    assert "VERDICT: PASS" in out and "qa/shorts-candidates.json" in out
    assert str(tmp_path) not in (project / "qa" / "shorts-candidates.json").read_text(encoding="utf-8")
    spans = [(c["start"], c["end"]) for c in report["candidates"]]
    for a in spans:
        for b in spans:
            if a != b:
                assert sp.overlap(a, b) <= sp.OVERLAP_MAX


def test_words_offset_moves_every_time(tmp_path, capsys):
    project = make_project(tmp_path)
    run(capsys, project, "--min", "5", "--max", "25")
    plain = report_of(project)["candidates"][0]
    run(capsys, project, "--min", "5", "--max", "25", "--words-offset", "10")
    shifted = report_of(project)["candidates"][0]
    assert shifted["start"] == pytest.approx(plain["start"] + 10)


def test_no_window_passes_when_every_sentence_leans_on_context(tmp_path, capsys):
    script = "And then it broke. So they paid again. But nobody noticed. This was the end. It never came back."
    project = make_project(tmp_path, script=script)
    code, out = run(capsys, project, "--min", "1", "--max", "10")
    assert code == 1 and "No window passes the pocket rule" in out
    report = report_of(project)
    assert report["verdict"] == "FAIL" and report["candidates"] == [] and report["near_misses"]
    assert all(c["rank"] is None for c in report["near_misses"])


def test_falls_back_to_transcript_punctuation_without_a_script(tmp_path, capsys):
    project = make_project(tmp_path, with_script=False)
    code, out = run(capsys, project, "--min", "5", "--max", "25")
    assert code == 0, out
    assert report_of(project)["sentences_from"] == "transcript"


def test_retention_report_reranks_the_candidates(tmp_path, capsys):
    project = make_project(tmp_path)
    run(capsys, project, "--min", "5", "--max", "25")
    before = report_of(project)["candidates"]
    best = before[0]
    retention = {"video_id": "abcdefghijk", "judged": True, "duration_s": 50,
                 "steepest_drops": [{"start": best["cut_start"] + 1, "end": best["cut_start"] + 4, "loss_pp": 9}],
                 "rewatch_bumps": []}
    path = tmp_path / "retention.json"
    path.write_text(json.dumps(retention), encoding="utf-8")
    code, out = run(capsys, project, "--min", "5", "--max", "25", "--retention", path)
    assert code == 0 and "retention: used" in out
    after = report_of(project)
    assert after["candidates"][0]["start"] != best["start"]   # the window that opens in the cliff lost the top
    for cand in after["candidates"]:
        if cand["start"] == best["start"]:
            assert cand["retention_adjust"] == -20 and "steep drop" in cand["retention_notes"][0]
    assert after["retention"]["used"] is True
    path.write_text(json.dumps({"video_id": "abcdefghijk", "judged": False, "reason": "sample"}), encoding="utf-8")
    run(capsys, project, "--min", "5", "--max", "25", "--retention", path)
    assert report_of(project)["retention"]["used"] is False
    assert report_of(project)["candidates"][0]["retention_adjust"] == 0


@pytest.mark.parametrize("args, message", [
    (["--min", "30", "--max", "20"], "--min and --max"),
    (["--max", "200"], "--min and --max"),
    (["--words", "missing.json"], "transcript not found"),
    (["--script", "missing.md"], "script not found"),
])
def test_usage_errors_exit_2(tmp_path, capsys, args, message):
    project = make_project(tmp_path)
    code = sp.main([str(project)] + args)
    assert code == 2 and message in capsys.readouterr().err


def test_transcript_of_another_script_exits_2(tmp_path, capsys):
    other = spoken_words("Completely different words about gardening and tomatoes in the spring.")
    project = make_project(tmp_path, words=other)
    assert sp.main([str(project)]) == 2
    assert "different script or audio version" in capsys.readouterr().err


def test_uncompiled_script_and_untimed_transcript_exit_2(tmp_path, capsys):
    project = make_project(tmp_path, script="Acme paid {amount} for nothing. It was a lot.")
    assert sp.main([str(project)]) == 2 and "placeholder" in capsys.readouterr().err
    project = make_project(tmp_path / "b", words=[])
    (project / "qa" / "voice-check-transcript.json").write_text(json.dumps({"text": "no word timings"}),
                                                               encoding="utf-8")
    assert sp.main([str(project)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"something": 1}), encoding="utf-8")
    assert sp.main([str(make_project(tmp_path / "c")), "--retention", str(bad)]) == 2
