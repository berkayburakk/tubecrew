#!/usr/bin/env python3
"""publish_guard.py - an opt-in Claude Code PreToolUse hook that puts publish and upload commands behind a person.

What it does
    Reads the hook event (JSON) on stdin. For Bash and PowerShell tool calls it looks for a command that uploads
    or publishes: any unquoted `--upload` or `--schedule` flag (tubecrew's `upload.py <project> --upload`), a
    script whose name says upload or publish run through an interpreter, and a short list of other uploader
    programs. In an interactive session it answers "ask", so the creator confirms in the permission prompt. In an
    unattended session nobody can confirm, so it answers "deny" and tells the agent why. Every other call gets no
    answer at all, which leaves Claude Code's normal permission rules in charge: this hook never says "allow".

    Passes without a question: dry runs (tubecrew's `upload.py` without `--upload`), `--help`, `--version`,
    `--dry-run`, pytest, read-only programs (grep, cat, git, echo, ...) and anything inside a quoted string, so
    `grep -n "--upload" upload.py` or `python -c "...--upload..."` are data, not a publish. A quoted command
    handed to a shell (`bash -c "..."`, `powershell -Command "..."`, `Start-Process -ArgumentList "..."`) is
    still checked.

Why
    "Never publish without asking" written in a prompt is a request the model can forget or reason its way
    around, and an allow rule added for convenience can open the door silently. A hook runs on every shell call
    whatever the model remembers. It reads command lines, not code: an upload built inside `python -c "..."`
    or hidden in a script with an innocent name is not caught, which is why the upload script itself stays a
    gate (private uploads only, review hashes).

How to run
    As a hook: register it in settings.json (see ../references/publish-guard.md). It reads stdin, writes the
    decision as JSON on stdout and always exits 0.

    python publish_guard.py --install-copy         copy itself to ~/.tubecrew/hooks/ and print the settings.json
                                                   block for that copy (recommended: plugin folders move on update)
    python publish_guard.py --print-settings       the settings.json block for this file where it is now
    python publish_guard.py --check "python upload.py my-video --upload"
                                                   what the hook would answer for a command (--tool PowerShell)

    Optional config: ~/.tubecrew/publish-guard.json (TUBECREW_HOME moves the folder; --config names a file):
        {"extra_flags": ["--publish"], "extra_programs": ["my-uploader"], "extra_patterns": ["regex"],
         "dry_by_default": ["upload.py"], "headless_env": ["MY_RUNNER_JOB"]}
    extra_* add to the defaults; dry_by_default replaces the list of scripts that upload only with a flag.

    Unattended (deny instead of ask): TUBECREW_HEADLESS=1, a variable named in headless_env, CI=true, a session
    in dontAsk permission mode, or Claude Code reporting an unattended session. TUBECREW_HEADLESS=0 forces ask.

Exit codes
    hook mode: always 0 (a crash or unreadable input prints one line on stderr and lets the call through)
    --check:   0 the command passes without a question, 1 the hook would ask or deny, 2 usage error

Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

TOOL = "publish_guard.py"
VERSION = "0.1.0"

SHELL_TOOLS = ("Bash", "PowerShell")
ASK, DENY = "ask", "deny"

# Flags that turn a dry run into a real upload or a scheduled publish (tubecrew's upload.py and similar tools).
DEFAULT_FLAGS = ("--upload", "--schedule")
# Scripts that upload only with one of the flags above; without it they are a dry run and pass.
DEFAULT_DRY_BY_DEFAULT = ("upload.py",)
# Programs that upload to YouTube on every run (open-source uploader CLIs), matched by program name.
DEFAULT_PROGRAMS = ("youtube-upload", "youtubeuploader")
DEFAULT_HEADLESS_ENV = ("TUBECREW_HEADLESS",)

SCRIPT_EXTS = (".py", ".sh", ".ps1", ".js", ".mjs", ".cjs", ".ts", ".rb", ".bat", ".cmd")
_SCRIPT_NAME_RE = re.compile(r"upload|publish", re.I)
# A program word that runs the script after it (interpreters, runners and launch wrappers).
_RUNNER_RE = re.compile(r"^(?:python[\d.]*w?|py|pythonw|node|bash|sh|zsh|dash|pwsh|powershell|ruby|deno|bun|tsx|"
                        r"ts-node|uv|uvx|poetry|pipenv|pdm|hatch|npx|pnpm|cmd|start|start-process|nohup|sudo|time|"
                        r"xargs|env|call|run|exec|-m|-file|/c)$", re.I)
# Programs that only read or print: a segment that starts with one of them never publishes.
READ_ONLY_PROGRAMS = frozenset("""
grep egrep fgrep rg ag ack findstr select-string sls cat type get-content gc head tail less more wc echo printf
write-output write-host ls dir get-childitem gci git diff file stat test which where get-command man
""".split())
READ_ONLY_FLAGS = frozenset(("--help", "-h", "/?", "--version", "--dry-run", "--dryrun"))

_QUOTED_RE = re.compile(r'"(?:[^"\\`]|\\.|`.)*"' + r"|'[^']*'")
# Quoted text right after these is a command for a shell, so it is checked instead of dropped.
_SHELL_ARG_RE = re.compile(r"(?i)(?:^|[\s;&|(])(?:bash|sh|zsh|dash|pwsh|powershell|cmd)(?:\.exe)?"
                           r"(?:\s+-{1,2}[\w-]+)*?\s+(?:-\w*c|-command|/c)\s*$"
                           r"|start-process\b[^;&|\n]*-argumentlist\s*$")
# A quoted path to a script or program stays visible (paths with spaces are quoted on Windows).
_QUOTED_PATH_RE = re.compile(r"[^\"'|;&<>\n]*\.(?:py|sh|ps1|js|mjs|cjs|ts|rb|bat|cmd|exe)", re.I)
# A quoted string that is only a flag ("--upload") reaches the program as that flag, so it stays visible too.
_QUOTED_FLAG_RE = re.compile(r"--?[\w-]+(?:=\S*)?")
# The guard's own file never publishes (`publish_guard.py --check "..."`, `--install-copy`).
SELF_NAME = "publish_guard.py"
_SEGMENT_SPLIT_RE = re.compile(r"&&|\|\||[;|&\n\r(){}`]")
_TOKEN_STRIP = "$()`{}[],\"'"
MAX_DEPTH = 3


# ----------------------------------------------------------------------------------------------- config

def tubecrew_home() -> Path:
    return Path(os.environ.get("TUBECREW_HOME") or Path.home() / ".tubecrew")


def default_config() -> dict:
    return {"flags": list(DEFAULT_FLAGS), "programs": list(DEFAULT_PROGRAMS), "patterns": [],
            "dry_by_default": list(DEFAULT_DRY_BY_DEFAULT), "headless_env": list(DEFAULT_HEADLESS_ENV)}


def _str_list(data: dict, key: str) -> list:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{key} must be a list of non-empty strings")
    return [v.strip() for v in value]


def load_config(path: Path | None = None) -> tuple:
    """-> (config, warning or None). A broken file never weakens the guard: the defaults stay on."""
    config = default_config()
    path = Path(path) if path else tubecrew_home() / "publish-guard.json"
    if not path.is_file():
        return config, None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("the file is not a JSON object")
        config["flags"] += [f.lower() for f in _str_list(data, "extra_flags")]
        config["programs"] += [p.lower() for p in _str_list(data, "extra_programs")]
        config["patterns"] = [re.compile(p, re.I) for p in _str_list(data, "extra_patterns")]
        if "dry_by_default" in data:
            config["dry_by_default"] = [s.lower() for s in _str_list(data, "dry_by_default")]
        config["headless_env"] += _str_list(data, "headless_env")
    except (OSError, ValueError, re.error) as exc:
        return default_config(), f"{path.name} ignored ({exc}); the default guard stays on"
    return config, None


# ----------------------------------------------------------------------------------------------- matching

def prepare(command: str) -> tuple:
    """Command -> (text with quoted strings blanked, [quoted commands handed to a shell]).

    Quoted text is data (`grep "--upload"`), except a quoted script or program path, which stays as one token,
    and a quoted command passed to a shell, which is returned for its own check.
    """
    pieces, inner, pos = [], [], 0
    for m in _QUOTED_RE.finditer(command):
        pieces.append(command[pos:m.start()])
        content = m.group(0)[1:-1]
        if _SHELL_ARG_RE.search(command[:m.start()]):
            inner.append(content)
            pieces.append('""')
        elif _QUOTED_PATH_RE.fullmatch(content.strip()):
            pieces.append(content.strip().replace(" ", "_"))
        elif _QUOTED_FLAG_RE.fullmatch(content.strip()):
            pieces.append(content.strip())
        else:
            pieces.append('""')
        pos = m.end()
    pieces.append(command[pos:])
    return "".join(pieces), inner


def _base(token: str) -> str:
    return re.split(r"[\\/]", token)[-1].lower()


def _program(token: str) -> str:
    base = _base(token)
    return base[:-4] if base.endswith((".exe", ".cmd", ".bat")) and not base.startswith(".") else base


def segment_hit(segment: str, config: dict) -> str | None:
    """One simple command (no ; && || |) -> why it publishes, or None."""
    tokens = [t.strip(_TOKEN_STRIP) for t in segment.split()]
    tokens = [t for t in tokens if t]
    while tokens and (re.fullmatch(r"[A-Za-z_][\w]*=.*", tokens[0]) or tokens[0] in ("&", ".")):
        tokens = tokens[1:]  # env assignments (FOO=1 cmd) and PowerShell's call operator
    if not tokens:
        return None
    lowered = [t.lower() for t in tokens]
    if _program(tokens[0]) in READ_ONLY_PROGRAMS:
        return None
    if any(t in READ_ONLY_FLAGS for t in lowered):
        return None
    if any(_program(t) in ("pytest", "py.test") for t in tokens):
        return None
    for token in lowered:
        name = token.split("=", 1)[0]
        for flag in config["flags"]:
            # argparse-style tools accept an unambiguous prefix (--upl), so a long-flag prefix counts as the flag
            if name == flag or (name.startswith("--") and len(name) >= 3 and flag.startswith(name)):
                return f"`{flag}` uploads or schedules a video"
    runner_seen = False
    for i, token in enumerate(tokens):
        program = _program(token)
        base = _base(token)
        if (i == 0 or runner_seen) and program in config["programs"]:
            return f"`{program}` uploads to YouTube"
        if (i == 0 or runner_seen) and base.endswith(SCRIPT_EXTS) and _SCRIPT_NAME_RE.search(base) \
                and not base.startswith("test_") and base != SELF_NAME and base not in config["dry_by_default"]:
            return f"`{base}` is an upload or publish script"
        if _RUNNER_RE.match(program):
            runner_seen = True
    text = " ".join(tokens)
    for pattern in config["patterns"]:
        m = pattern.search(text)
        if m:
            return f"`{m.group(0)}` matches a guarded publish command"
    return None


def command_hit(command: str, config: dict, depth: int = 0) -> str | None:
    """A full command line (several commands, pipes, quotes) -> why it publishes, or None."""
    if depth > MAX_DEPTH:
        return None
    text, inner = prepare(command)
    for segment in _SEGMENT_SPLIT_RE.split(text):
        hit = segment_hit(segment, config)
        if hit:
            return hit
    for sub in inner:
        hit = command_hit(sub, config, depth + 1)
        if hit:
            return hit
    return None


def _truthy(value: str | None) -> bool | None:
    """'1'/'true'/'yes'/'on' -> True, '0'/'false'/'no'/'off' -> False, unset or empty -> None."""
    if value is None or not value.strip():
        return None
    return value.strip().lower() not in ("0", "false", "no", "off")


def headless_reason(event: dict, env, config: dict) -> str | None:
    """Why nobody can answer a permission prompt in this session, or None when someone can."""
    forced = _truthy(env.get("TUBECREW_HEADLESS"))
    if forced is not None:
        return "TUBECREW_HEADLESS is set (an unattended run)" if forced else None
    for name in config["headless_env"]:
        if _truthy(env.get(name)):
            return f"{name} is set (an unattended run)"
    if _truthy(env.get("CI")):
        return "CI is set (an automated run)"
    if event.get("permission_mode") == "dontAsk":
        return "the session runs in dontAsk mode"
    # Not documented by Claude Code; when present it only ever turns "ask" into "deny".
    if (env.get("CLAUDE_CODE_SESSION_ATTENDED") or "").strip() == "0":
        return "Claude Code reports an unattended session"
    return None


def decide(event: dict, config: dict, env=None) -> tuple:
    """Hook event -> (decision or None, reason). None means: no answer, normal permission rules apply."""
    env = os.environ if env is None else env
    if event.get("tool_name") not in SHELL_TOOLS:
        return None, ""
    command = (event.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return None, ""
    hit = command_hit(command, config)
    if hit is None:
        return None, ""
    why = headless_reason(event, env, config)
    if why:
        return DENY, (f"Publish guard: {hit}. Blocked because {why}, so nobody can confirm it. Do not retry or work "
                      "around this; tell the creator the command is ready for them to run.")
    return ASK, (f"Publish guard: {hit}. Uploading, scheduling and publishing are the creator's decision; confirm "
                 "only if you meant to do this now.")


def hook_output(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                   "permissionDecisionReason": reason}}


# ----------------------------------------------------------------------------------------------- settings

def install_copy(home: Path | None = None) -> Path:
    """Copy this script to <tubecrew home>/hooks/ and return the copy's path.

    The plugin's own folder can move or disappear on a plugin update, and `python missing.py` exits with code 2,
    which Claude Code reads as "block": every shell call would be refused. A copy in the tubecrew folder stays put.
    """
    target_dir = (home or tubecrew_home()) / "hooks"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(__file__).name
    target.write_bytes(Path(__file__).read_bytes())
    return target


def settings_block(shell_form: bool = False, script: Path | None = None) -> dict:
    """The settings.json block that registers this file (or `script`), with this machine's absolute paths."""
    script = str(Path(script or __file__).resolve())
    python = sys.executable or "python"
    if shell_form:
        handler = {"type": "command", "command": f'"{Path(python).as_posix()}" "{Path(script).as_posix()}"',
                   "timeout": 10}
    else:
        handler = {"type": "command", "command": python, "args": [script], "timeout": 10}
    return {"hooks": {"PreToolUse": [{"matcher": "|".join(SHELL_TOOLS), "hooks": [handler]}]}}


# ----------------------------------------------------------------------------------------------- main

def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _note(message: str) -> None:
    try:
        sys.stderr.write(f"publish_guard: {message}"[:400] + "\n")
    except Exception:  # noqa: BLE001 - a note must never fail the hook
        pass


def run_hook(config_path: Path | None) -> int:
    """Hook mode. Any failure lets the call through: a guard that breaks every shell call gets switched off."""
    try:
        raw = sys.stdin.buffer.read() if hasattr(sys.stdin, "buffer") else sys.stdin.read().encode("utf-8")
        event = json.loads(raw.decode("utf-8", "replace") or "{}")
        if not isinstance(event, dict):
            _note("hook input is not a JSON object; letting the call through")
            return 0
        config, warning = load_config(config_path)
        if warning:
            _note(warning)
        decision, reason = decide(event, config)
    except Exception as exc:  # noqa: BLE001 - fail open by design
        _note(f"internal error, letting the call through: {type(exc).__name__}: {exc}")
        return 0
    if decision:
        sys.stdout.write(json.dumps(hook_output(decision, reason), ensure_ascii=False) + "\n")
    return 0


def main(argv: list | None = None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(
        prog=TOOL, description="Opt-in Claude Code PreToolUse hook: publish and upload commands need a person "
                               "(ask when interactive, deny when unattended). Without options it reads the hook "
                               "event on stdin.")
    parser.add_argument("--check", metavar="COMMAND", help="print what the hook would answer for this command")
    parser.add_argument("--tool", default="Bash", choices=SHELL_TOOLS, help="tool name for --check (default Bash)")
    parser.add_argument("--config", type=Path, help="config file (default ~/.tubecrew/publish-guard.json)")
    parser.add_argument("--print-settings", action="store_true",
                        help="print the settings.json block that registers this hook on this machine")
    parser.add_argument("--install-copy", action="store_true",
                        help="copy this script to ~/.tubecrew/hooks/ (a path that survives plugin updates) and print "
                             "the settings.json block for the copy")
    parser.add_argument("--shell-form", action="store_true",
                        help="with --print-settings or --install-copy: a single command string, for Claude Code "
                             "versions without the hook 'args' field")
    parser.add_argument("--json", action="store_true", help="with --check: print the result as JSON")
    args = parser.parse_args(argv)

    if args.install_copy:
        try:
            copy = install_copy()
        except OSError as exc:
            print(f"publish_guard: error: cannot copy the hook: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(settings_block(args.shell_form, copy), indent=2, ensure_ascii=False))
        where = "TUBECREW_HOME" if os.environ.get("TUBECREW_HOME") else "~/.tubecrew"
        print(f"Copied to hooks/{copy.name} in your tubecrew folder ({where}). Paste the block above into "
              "settings.json.", file=sys.stderr)
        return 0
    if args.print_settings:
        print(json.dumps(settings_block(args.shell_form), indent=2, ensure_ascii=False))
        return 0
    if args.check is None:
        return run_hook(args.config)

    config, warning = load_config(args.config)
    if warning:
        _note(warning)
    event = {"tool_name": args.tool, "tool_input": {"command": args.check}, "permission_mode": "default"}
    decision, reason = decide(event, config)
    if args.json:
        print(json.dumps({"command": args.check, "decision": decision, "reason": reason}, ensure_ascii=False))
    elif decision:
        print(f"{decision.upper()}: {reason}")
    else:
        print("PASS: no publish or upload command found; normal permission rules apply")
    return 1 if decision else 0


if __name__ == "__main__":
    sys.exit(main())
