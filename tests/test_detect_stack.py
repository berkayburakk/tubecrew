"""detect_stack.py: recommendations from facts, skill detection, saved choices, no secrets in output."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "setup" / "scripts" / "detect_stack.py"
spec = importlib.util.spec_from_file_location("detect_stack", SCRIPT)
ds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds)

BARE = {"python": "3.12.0", "ffmpeg": None, "ffprobe": False, "yt_dlp": None, "node": None, "npx": False,
        "git": True, "codex": None, "claude": True, "gpu": None, "faster_whisper": False, "google_libs": False,
        "oauth_client": False, "elevenlabs_key": False, "remotion": False, "hyperframes": False, "comfyui": False,
        "cosyvoice": False}


def facts(**over):
    f = dict(BARE)
    f.update(over)
    return f


def tools(rec):
    return {station: tool for station, (tool, _why) in rec.items()}


def test_bare_machine_gets_safe_defaults():
    t = tools(ds.recommend(facts()))
    assert t == {"research": "yt-dlp", "verifier": "claude-agent", "voice": "recording",
                 "transcription": "editor", "motion": "editor", "clips": "none", "dubbing": "none",
                 "upload": "studio"}
    assert set(t) == set(ds.STATIONS)


def test_subscriptions_and_local_tools_shape_the_stack():
    t = tools(ds.recommend(facts(codex="0.50.0", elevenlabs_key=True, cosyvoice=True, faster_whisper=True,
                                 node="v22", npx=True, hyperframes=True, google_libs=True, oauth_client=True)))
    assert t["verifier"] == "codex"
    assert t["voice"] == "elevenlabs"          # a paid plan wins for narration
    assert t["dubbing"] == "cosyvoice"         # local dubbing saves the plan's credits
    assert t["motion"] == "hyperframes"
    assert t["upload"] == "youtube-api"


@pytest.mark.parametrize("memory_mb, comfy, expected", [
    (8192, False, "none"), (16303, False, "wan"), (24564, False, "wan"), (6000, True, "wan")])
def test_wan_depends_on_gpu_memory_or_a_running_comfyui(memory_mb, comfy, expected):
    rec = ds.recommend(facts(gpu={"name": "Test GPU", "memory_mb": memory_mb}, comfyui=comfy))
    assert rec["clips"][0] == expected


def test_motion_with_node_but_no_engine_suggests_installing_one():
    tool, why = ds.recommend(facts(node="v22", npx=True))["motion"]
    assert tool == "morph-clips" and "Remotion" in why and "HyperFrames" in why


def test_gb_rounds_nvidia_mib():
    assert ds.gb(16303) == 16 and ds.gb(24564) == 24


def test_find_skills_looks_in_agent_skill_folders(tmp_path):
    root = tmp_path / ".agents" / "skills"
    (root / "remotion-best-practices").mkdir(parents=True)
    roots = ds.skill_roots(tmp_path / "project", tmp_path)
    assert ds.find_skills("remotion", roots)
    assert not ds.find_skills("hyperframes", roots)


def test_package_json_dependency_counts(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"remotion": "4.0.0",
                                                                         "@remotion/cli": "4.0.0"}}))
    assert ds.package_uses(tmp_path, "remotion")
    assert not ds.package_uses(tmp_path, "hyperframes")


def test_plugin_registry_detection(tmp_path):
    plugins = tmp_path / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text('{"plugins": {"hyperframes@hyperframes": {}}}')
    assert ds.plugin_installed("hyperframes", tmp_path)
    assert not ds.plugin_installed("remotion", tmp_path)


def test_comfyui_probe_never_leaves_the_machine():
    assert ds.comfyui_running("http://example.com:8188") is False


def test_parse_sets_validates_station_and_tool():
    assert ds.parse_sets(["voice=CosyVoice", "verifier=codex"]) == {"voice": "cosyvoice", "verifier": "codex"}
    with pytest.raises(ValueError):
        ds.parse_sets(["voice"])
    with pytest.raises(ValueError):
        ds.parse_sets(["voice=banana"])
    with pytest.raises(ValueError):
        ds.parse_sets(["editor=remotion"])


def test_set_saves_choices_under_tubecrew_home(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TUBECREW_HOME", str(tmp_path))
    monkeypatch.setattr(ds, "detect", lambda: facts())
    assert ds.main(["--set", "voice=cosyvoice"]) == 0
    assert ds.main(["--set", "clips=wan"]) == 0
    saved = json.loads((tmp_path / "stack.json").read_text(encoding="utf-8"))
    assert saved["choices"] == {"voice": "cosyvoice", "clips": "wan"}
    assert saved["facts"]["python"] == "3.12.0"
    out = capsys.readouterr().out
    assert "cosyvoice (chosen)" in out


def test_bad_set_is_a_usage_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TUBECREW_HOME", str(tmp_path))
    assert ds.main(["--set", "voice=banana"]) == 2
    assert not (tmp_path / "stack.json").exists()


def test_secrets_are_never_printed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TUBECREW_HOME", str(tmp_path))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_should_never_appear_1234")
    real = ds.detect()
    assert real["elevenlabs_key"] is True
    monkeypatch.setattr(ds, "detect", lambda: real)
    ds.main(["--json"])
    ds.main([])
    assert "sk_test_should_never_appear_1234" not in capsys.readouterr().out


def test_json_output_shape(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TUBECREW_HOME", str(tmp_path))
    monkeypatch.setattr(ds, "detect", lambda: facts(node="v22", npx=True))
    assert ds.main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"facts", "suggested", "choices", "free_to_add"}
    assert set(data["suggested"]) == set(ds.STATIONS)
    assert any("remotion-dev/skills" in line for line in data["free_to_add"])
