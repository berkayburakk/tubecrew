"""Tests for skills/setup/scripts/publish_guard.py: decisions for a table of commands, interactive vs headless,
config handling, the hook's stdin/stdout contract and fail-open behaviour. No network, no real uploads."""
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "setup" / "scripts" / "publish_guard.py"
spec = importlib.util.spec_from_file_location("publish_guard", SCRIPT)
pg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pg)

PY = ".venv/Scripts/python.exe"
UPLOAD = "skills/upload-private/scripts/upload.py"
ENV_KEYS = ("CI", "TUBECREW_HEADLESS", "CLAUDE_CODE_SESSION_ATTENDED", "MY_RUNNER_JOB")


def event(command, tool="Bash", mode="default"):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": {"command": command},
            "permission_mode": mode}


def decide(command, tool="Bash", env=None, config=None, mode="default"):
    return pg.decide(event(command, tool, mode), config or pg.default_config(), env or {})


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TUBECREW_HOME", str(tmp_path / "home"))  # never read the real user's config


# ------------------------------------------------------------------------------------------- the command table

@pytest.mark.parametrize("command", [
    f"python {UPLOAD} my-video --upload --profile main",
    f"{PY} {UPLOAD} my-video --upload",
    f"python {UPLOAD} my-video --schedule 2026-11-01T15:00:00Z",
    f"python {UPLOAD} my-video --schedule=2026-11-01T15:00:00Z",
    "cd my-video && python ../upload.py . --upload",
    "pytest -q; python upload.py my-video --upload",           # a test run before it does not excuse the upload
    f'python "/Users/me/My Videos/tubecrew/{UPLOAD}" proj --upload',
    "python yt_upload.py my-video",                             # a custom upload script asks on every run
    "python3 -u tools/publish_video.py my-video",
    "uv run scripts/upload_short.py clip.mp4",
    "bash ./publish.sh",
    "./publish.sh",
    "youtube-upload --title Demo video.mp4",
    "/usr/local/bin/youtubeuploader -filename video.mp4",
    'bash -c "python upload.py my-video --upload"',             # a quoted command handed to a shell is checked
    "FOO=1 python upload.py my-video --upload",
    "cat notes.txt | python upload.py my-video --upload",
    "(cd my-video && python upload.py . --upload)",
    "echo `python upload.py my-video --upload`",
    'python upload.py my-video "--upload"',                     # the shell strips the quotes: still an upload
    "python upload.py my-video '--upload'",
    "python upload.py my-video --upl",                          # an argparse-style prefix of the flag
    'bash -lc "python upload.py my-video --upload"',            # combined shell flags ending in c
    'sh -ec "python upload.py my-video --upload"',
    "Start-Process python -ArgumentList 'upload.py','my-video','--upload'",
])
def test_publish_commands_are_guarded(command):
    decision, reason = decide(command)
    assert decision == pg.ASK
    assert reason.startswith("Publish guard:")


@pytest.mark.parametrize("command", [
    f"python {UPLOAD} my-video",                                 # tubecrew's upload.py is a dry run by default
    f"python {UPLOAD} my-video --skip-review 'creator skipped the review on purpose'",
    f"python {UPLOAD} --login --profile main",
    f"python {UPLOAD} my-video --upload --help",
    f"python {UPLOAD} --version",
    "python publish_video.py my-video --dry-run",
    "python -m pytest tests/test_upload.py -q -k upload",
    "pytest tests/test_publish_guard.py",
    'grep -n "--upload" skills/upload-private/scripts/upload.py',
    "grep -n -- --upload skills/upload-private/scripts/upload.py",
    "rg --upload .",
    "cat skills/upload-private/scripts/upload.py",
    "git diff -- skills/upload-private/scripts/upload.py",
    'echo "python upload.py my-video --upload"',
    "echo 'run upload.py --upload later'",
    'python -c "import guard; guard.check(\'upload.py p --upload\')"',
    "ruff check skills/upload-private/scripts/upload.py tools/publish_report.py",
    "python tests/test_upload_rules.py",
    "curl --upload-file report.txt https://example.com/drop",
    "ffmpeg -i in.mp4 out.mp4",
    "ls render/",
    "",
    'python skills/setup/scripts/publish_guard.py --check "python upload.py p --upload"',   # the guard itself
    "python ~/.tubecrew/hooks/publish_guard.py --install-copy",
])
def test_reads_dry_runs_and_quoted_text_pass(command):
    assert decide(command) == (None, "")


@pytest.mark.parametrize("command", [
    f"& {PY} {UPLOAD} my-video --upload",
    '& "C:/Program Files/Python311/python.exe" "C:/My Videos/tools/yt_publish.py" proj',
    'Start-Process python -ArgumentList "upload.py my-video --upload"',
    'powershell -NoProfile -Command "python upload.py my-video --upload"',
    "Get-ChildItem *.json | ForEach-Object { python upload.py $_.Directory --upload }",
])
def test_powershell_commands_are_guarded(command):
    assert decide(command, tool="PowerShell")[0] == pg.ASK


@pytest.mark.parametrize("command", [
    f"& {PY} {UPLOAD} my-video",
    'Select-String -Path upload.py -Pattern "--upload"',
    "Get-Content skills/upload-private/scripts/upload.py",
    "Write-Output 'python upload.py p --upload'",
])
def test_powershell_reads_pass(command):
    assert decide(command, tool="PowerShell") == (None, "")


def test_other_tools_are_ignored():
    assert pg.decide({"tool_name": "Write", "tool_input": {"command": "python upload.py p --upload"}},
                     pg.default_config(), {}) == (None, "")
    assert pg.decide({"tool_name": "Bash", "tool_input": {}}, pg.default_config(), {}) == (None, "")
    assert pg.decide({"tool_name": "Bash"}, pg.default_config(), {}) == (None, "")


# ------------------------------------------------------------------------------------------- interactive vs headless

CMD = "python upload.py my-video --upload"


@pytest.mark.parametrize("env, mode", [
    ({"TUBECREW_HEADLESS": "1"}, "default"),
    ({"TUBECREW_HEADLESS": "true"}, "default"),
    ({"CI": "true"}, "default"),
    ({"CLAUDE_CODE_SESSION_ATTENDED": "0"}, "default"),
    ({}, "dontAsk"),
])
def test_headless_sessions_deny(env, mode):
    decision, reason = decide(CMD, env=env, mode=mode)
    assert decision == pg.DENY
    assert "nobody can confirm" in reason and "Do not retry" in reason


@pytest.mark.parametrize("env", [{}, {"CI": "false"}, {"CI": ""}, {"CLAUDE_CODE_SESSION_ATTENDED": "1"},
                                 {"TUBECREW_HEADLESS": "0", "CI": "true"}])
def test_interactive_sessions_ask(env):
    assert decide(CMD, env=env)[0] == pg.ASK


def test_headless_env_from_config(tmp_path):
    cfg = tmp_path / "guard.json"
    cfg.write_text(json.dumps({"headless_env": ["MY_RUNNER_JOB"]}), encoding="utf-8")
    config, warning = pg.load_config(cfg)
    assert warning is None
    assert decide(CMD, env={"MY_RUNNER_JOB": "job-7"}, config=config)[0] == pg.DENY
    assert decide(CMD, env={}, config=config)[0] == pg.ASK


# ------------------------------------------------------------------------------------------- config

def test_config_extends_the_defaults(tmp_path):
    cfg = tmp_path / "guard.json"
    cfg.write_text(json.dumps({"extra_flags": ["--publish"], "extra_programs": ["acme-uploader"],
                               "extra_patterns": [r"\bgo-live\b"], "dry_by_default": ["upload.py", "push.py"]}),
                   encoding="utf-8")
    config, warning = pg.load_config(cfg)
    assert warning is None
    assert decide("python tool.py --publish", config=config)[0] == pg.ASK
    assert decide("acme-uploader video.mp4", config=config)[0] == pg.ASK
    assert decide("python stream.py go-live", config=config)[0] == pg.ASK
    assert decide("python upload.py p --upload", config=config)[0] == pg.ASK   # defaults stay on
    assert decide("pip install acme-uploader", config=config) == (None, "")    # only as the program that runs


def test_dry_by_default_replaces_the_list(tmp_path):
    cfg = tmp_path / "guard.json"
    cfg.write_text(json.dumps({"dry_by_default": ["publish_preview.py"]}), encoding="utf-8")
    config, _ = pg.load_config(cfg)
    assert decide("python publish_preview.py p", config=config) == (None, "")
    assert decide("python publish_preview.py p --upload", config=config)[0] == pg.ASK


@pytest.mark.parametrize("content", ["{not json", "[]", '{"extra_flags": "--publish"}', '{"extra_patterns": ["("]}'])
def test_broken_config_keeps_the_default_guard(tmp_path, content):
    cfg = tmp_path / "guard.json"
    cfg.write_text(content, encoding="utf-8")
    config, warning = pg.load_config(cfg)
    assert warning and "default guard stays on" in warning
    assert config == pg.default_config()


def test_missing_config_is_fine(tmp_path):
    assert pg.load_config(tmp_path / "nope.json") == (pg.default_config(), None)


# ------------------------------------------------------------------------------------------- hook contract

def _run_hook(monkeypatch, capsys, payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8"))
    code = pg.main([])
    return code, capsys.readouterr()


def test_hook_asks_with_the_documented_json(monkeypatch, capsys):
    code, out = _run_hook(monkeypatch, capsys, event(CMD))
    assert code == 0
    data = json.loads(out.out)
    hso = data["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "ask"
    assert "--upload" in hso["permissionDecisionReason"]


def test_hook_denies_when_headless(monkeypatch, capsys):
    monkeypatch.setenv("TUBECREW_HEADLESS", "1")
    code, out = _run_hook(monkeypatch, capsys, event(CMD, tool="PowerShell"))
    assert code == 0
    assert json.loads(out.out)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_hook_stays_silent_for_ordinary_commands(monkeypatch, capsys):
    code, out = _run_hook(monkeypatch, capsys, event("python upload.py my-video"))
    assert (code, out.out) == (0, "")


@pytest.mark.parametrize("payload", [b"{broken", b"", b"[1, 2]", b"\xff\xfe"])
def test_hook_fails_open_on_bad_input(monkeypatch, capsys, payload):
    code, out = _run_hook(monkeypatch, capsys, payload)
    assert code == 0
    assert out.out == ""


def test_hook_fails_open_when_it_crashes(monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise RuntimeError("simulated bug")
    monkeypatch.setattr(pg, "decide", boom)
    code, out = _run_hook(monkeypatch, capsys, event(CMD))
    assert code == 0
    assert out.out == ""
    assert "letting the call through" in out.err and "simulated bug" in out.err


def test_hook_reports_a_broken_config_but_still_guards(monkeypatch, capsys, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "publish-guard.json").write_text("{oops", encoding="utf-8")
    code, out = _run_hook(monkeypatch, capsys, event(CMD))
    assert code == 0
    assert json.loads(out.out)["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert "default guard stays on" in out.err


def test_hook_as_a_real_process():
    """The way Claude Code runs it: a separate process, the event on stdin, JSON on stdout."""
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    proc = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(event(CMD)).encode("utf-8"),
                          capture_output=True, env=env, timeout=60)
    assert proc.returncode == 0
    assert json.loads(proc.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecision"] == "ask"


# ------------------------------------------------------------------------------------------- CLI helpers

def test_check_exit_codes(capsys):
    assert pg.main(["--check", CMD]) == 1
    assert "ASK" in capsys.readouterr().out
    assert pg.main(["--check", "python upload.py my-video"]) == 0
    assert "PASS" in capsys.readouterr().out
    assert pg.main(["--check", CMD, "--tool", "PowerShell", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["decision"] == "ask"


def test_print_settings_registers_this_file(capsys):
    assert pg.main(["--print-settings"]) == 0
    block = json.loads(capsys.readouterr().out)
    entry = block["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash|PowerShell"
    handler = entry["hooks"][0]
    assert handler["type"] == "command"
    assert Path(handler["args"][0]).resolve() == SCRIPT
    assert handler["command"] == sys.executable
    assert handler["timeout"] > 0


def test_install_copy_points_settings_at_a_stable_copy(capsys, tmp_path):
    assert pg.main(["--install-copy"]) == 0               # TUBECREW_HOME is tmp_path/home (fixture)
    copy = tmp_path / "home" / "hooks" / "publish_guard.py"
    assert copy.read_bytes() == SCRIPT.read_bytes()
    handler = json.loads(capsys.readouterr().out)["hooks"]["PreToolUse"][0]["hooks"][0]
    assert Path(handler["args"][0]).resolve() == copy.resolve()
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    proc = subprocess.run([handler["command"], *handler["args"]], input=json.dumps(event(CMD)).encode("utf-8"),
                          capture_output=True, env=env, timeout=60)
    assert json.loads(proc.stdout.decode("utf-8"))["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_print_settings_shell_form(capsys):
    assert pg.main(["--print-settings", "--shell-form"]) == 0
    handler = json.loads(capsys.readouterr().out)["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "args" not in handler
    assert handler["command"].endswith('publish_guard.py"') and handler["command"].startswith('"')
