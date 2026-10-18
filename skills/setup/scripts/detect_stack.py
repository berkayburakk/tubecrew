#!/usr/bin/env python3
"""detect_stack.py - find out which production tools this machine already has, and suggest a stack.

tubecrew does not bundle Remotion, HyperFrames, Wan, CosyVoice, ElevenLabs or Codex. This script looks for them
(and for the basics: Python, ffmpeg, yt-dlp, Node.js, a GPU) so the agent can recommend, for every station of the
production line, a tool the creator can actually use. It never prints secrets, never installs anything, and only
touches the network to check whether a local ComfyUI server answers on this machine.

    python detect_stack.py                       # what is installed, and a suggested stack
    python detect_stack.py --json                # the same, machine-readable
    python detect_stack.py --save                # also store the result in ~/.tubecrew/stack.json
    python detect_stack.py --set voice=cosyvoice --set verifier=codex   # record the creator's choices
    python detect_stack.py --show                # print the saved stack

Subscriptions cannot be detected (an ElevenLabs plan without an API key in the environment, a ChatGPT plan for
Codex, vidIQ). The agent asks the creator and records the answer with --set.

Exit codes: 0 always for detection (a missing tool is information, not a failure); 2 for usage errors.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

STATIONS = ("research", "verifier", "voice", "transcription", "motion", "clips", "dubbing", "upload")
ALLOWED = {
    "research": {"yt-dlp", "vidiq", "manual"},
    "verifier": {"codex", "claude-agent"},
    "voice": {"elevenlabs", "cosyvoice", "recording", "other-tts"},
    "transcription": {"faster-whisper", "editor"},
    "motion": {"remotion", "hyperframes", "morph-clips", "editor"},
    "clips": {"wan", "none"},
    "dubbing": {"cosyvoice", "elevenlabs", "none"},
    "upload": {"youtube-api", "studio"},
}
WAN_COMFORT_MB = 24000   # Wan 2.2 TI2V-5B: the Wan README targets 24 GB cards (RTX 4090 class)
WAN_MINIMUM_MB = 12000   # 12-24 GB cards run it through ComfyUI with fp8 weights or offloading; below, it is painful
COMMAND_TIMEOUT_S = 10

INSTALL = {
    "node": "Install Node.js 18+ from https://nodejs.org",
    "ffmpeg": "Install FFmpeg from https://ffmpeg.org/download.html (winget install Gyan.FFmpeg / brew install ffmpeg)",
    "yt-dlp": "pip install yt-dlp",
    "faster-whisper": "pip install faster-whisper",
    "google": "pip install google-auth google-auth-oauthlib requests",
    "remotion": "npx skills add remotion-dev/skills   (new project: npx create-video@latest)",
    "hyperframes": ("claude plugin marketplace add heygen-com/hyperframes && "
                    "claude plugin install hyperframes@hyperframes   (other agents: npx skills add heygen-com/hyperframes)"),
    "codex": "npm install -g @openai/codex   (needs a ChatGPT plan or an OpenAI API key)",
    "comfyui": "ComfyUI with the Wan 2.2 workflow: https://docs.comfy.org/tutorials/video/wan/wan2_2",
    "cosyvoice": "https://github.com/FunAudioLLM/CosyVoice (local GPU, open source)",
    "oauth": "Create your own OAuth client: skills/upload-private/references/oauth-setup.md",
    "ocr": "pip install rapidocr-onnxruntime   (optional: the on-screen text dose check, text_dose.py)",
}


def tubecrew_home() -> Path:
    return Path(os.environ.get("TUBECREW_HOME") or Path.home() / ".tubecrew")


def run_version(cmd: list[str]) -> str | None:
    """First line of `<tool> --version`, or None if the tool does not run."""
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        out = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S,
                             encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError):
        return None
    text = (out.stdout or out.stderr or "").strip().splitlines()
    if not text:
        return "installed"
    m = re.search(r"\bversion\s+(\S+)", text[0])
    return (m.group(1) if m else text[0])[:60]


def detect_gpu() -> dict | None:
    """NVIDIA GPU name and memory via nvidia-smi; None when there is none (or no driver)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    line = (out.stdout or "").strip().splitlines()
    if out.returncode != 0 or not line:
        return None
    name, _, mem = line[0].rpartition(",")
    try:
        return {"name": name.strip(), "memory_mb": int(float(mem.strip()))}
    except ValueError:
        return {"name": line[0].strip(), "memory_mb": None}


def gb(mb: int) -> int:
    """nvidia-smi reports MiB; a "16 GB" card reads about 16303."""
    return round(mb / 1024)


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def skill_roots(cwd: Path, home: Path) -> list[Path]:
    """Folders where agents keep skills: Claude Code, Codex and the shared .agents convention."""
    return [home / ".claude" / "skills", home / ".agents" / "skills", home / ".codex" / "skills",
            cwd / ".claude" / "skills", cwd / ".agents" / "skills"]


def find_skills(prefix: str, roots: list[Path]) -> bool:
    for root in roots:
        try:
            if any(p.is_dir() and p.name.lower().startswith(prefix) for p in root.iterdir()):
                return True
        except OSError:
            continue
    return False


def plugin_installed(name: str, home: Path) -> bool:
    """Claude Code keeps installed plugins under ~/.claude/plugins; look for the name in its registry files."""
    base = home / ".claude" / "plugins"
    for registry in (base / "installed_plugins.json", base / "known_marketplaces.json"):
        try:
            if re.search(rf'"{re.escape(name)}(@|")', registry.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def package_uses(cwd: Path, dependency: str) -> bool:
    try:
        data = json.loads((cwd / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
    return any(k == dependency or k.startswith(dependency + "/") or k.startswith("@" + dependency + "/")
               for k in deps)


def comfyui_running(url: str | None = None) -> bool:
    """True when something listens on the ComfyUI port of this machine (default 127.0.0.1:8188)."""
    m = re.match(r"^(?:https?://)?([^:/]+)(?::(\d+))?", url or "127.0.0.1:8188")
    host, port = (m.group(1), int(m.group(2) or 8188)) if m else ("127.0.0.1", 8188)
    if host not in ("127.0.0.1", "localhost", "::1"):
        return False  # only local servers; this script never probes the network
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def detect(cwd: Path | None = None, home: Path | None = None) -> dict:
    cwd, home = cwd or Path.cwd(), home or Path.home()
    roots = skill_roots(cwd, home)
    return {
        "python": sys.version.split()[0],
        "ffmpeg": run_version(["ffmpeg", "-version"]),
        "ffprobe": bool(shutil.which("ffprobe")),
        "yt_dlp": run_version(["yt-dlp", "--version"]) or (has_module("yt_dlp") and "python module" or None),
        "node": run_version(["node", "--version"]),
        "npx": bool(shutil.which("npx")),
        "git": bool(shutil.which("git")),
        "codex": run_version(["codex", "--version"]),
        "claude": bool(shutil.which("claude")),
        "gpu": detect_gpu(),
        "faster_whisper": has_module("faster_whisper"),
        "google_libs": all(has_module(m) for m in ("google.auth", "google_auth_oauthlib", "requests")),
        "oauth_client": (tubecrew_home() / "client_secret.json").is_file(),
        "elevenlabs_key": bool(os.environ.get("ELEVENLABS_API_KEY")),
        "remotion": find_skills("remotion", roots) or package_uses(cwd, "remotion"),
        "hyperframes": (find_skills("hyperframes", roots) or plugin_installed("hyperframes", home)
                        or package_uses(cwd, "hyperframes")),
        "comfyui": comfyui_running(os.environ.get("COMFYUI_URL")),
        "cosyvoice": bool(os.environ.get("COSYVOICE_HOME")) or has_module("cosyvoice"),
        "ocr": has_module("rapidocr_onnxruntime"),
    }


def recommend(f: dict) -> dict:
    """Suggested tool per station, with the reason. Pure: depends only on the facts."""
    gpu_mb = (f.get("gpu") or {}).get("memory_mb") or 0
    rec = {}
    rec["research"] = ("yt-dlp", "free channel catalogs") if f.get("yt_dlp") else \
        ("yt-dlp", "missing: " + INSTALL["yt-dlp"])
    rec["verifier"] = ("codex", "Codex CLI found: a second model family checks the facts") if f.get("codex") else \
        ("claude-agent", "an independent fact-checker agent (install Codex for a second model family)")
    if f.get("elevenlabs_key"):
        rec["voice"] = ("elevenlabs", "ELEVENLABS_API_KEY is set")
    elif f.get("cosyvoice"):
        rec["voice"] = ("cosyvoice", "CosyVoice found: local voice on your GPU, no subscription")
    elif gpu_mb:
        rec["voice"] = ("recording", "record your own voice, or install CosyVoice for local TTS on this GPU")
    else:
        rec["voice"] = ("recording", "record your own voice, or use a TTS service you subscribe to")
    rec["transcription"] = ("faster-whisper", "local, free") if f.get("faster_whisper") else \
        ("editor", "use your editor's transcript, or " + INSTALL["faster-whisper"])
    if f.get("remotion") and f.get("hyperframes"):
        rec["motion"] = ("remotion", "Remotion and HyperFrames both found; pick one per project (Remotion: React, "
                                     "HyperFrames: HTML)")
    elif f.get("remotion"):
        rec["motion"] = ("remotion", "Remotion skills found")
    elif f.get("hyperframes"):
        rec["motion"] = ("hyperframes", "HyperFrames found")
    elif f.get("node") and f.get("npx"):
        rec["motion"] = ("morph-clips", "built in; install Remotion or HyperFrames for full motion design")
    else:
        rec["motion"] = ("editor", "your video editor; install Node.js to use Remotion, HyperFrames or morph-clips")
    if f.get("comfyui"):
        rec["clips"] = ("wan", "ComfyUI is running on this machine: Wan 2.2 clips locally")
    elif gpu_mb >= WAN_COMFORT_MB:
        rec["clips"] = ("wan", f"{gb(gpu_mb)} GB GPU: Wan 2.2 runs locally (" + INSTALL["comfyui"] + ")")
    elif gpu_mb >= WAN_MINIMUM_MB:
        rec["clips"] = ("wan", f"{gb(gpu_mb)} GB GPU: Wan 2.2 through ComfyUI with fp8 weights or offloading "
                               "(" + INSTALL["comfyui"] + ")")
    else:
        rec["clips"] = ("none", "no suitable GPU: use motion design, screen recordings and real footage")
    if f.get("cosyvoice"):
        rec["dubbing"] = ("cosyvoice", "local dubbing with CosyVoice")
    elif f.get("elevenlabs_key"):
        rec["dubbing"] = ("elevenlabs", "ElevenLabs dubbing (uses your plan's credits)")
    else:
        rec["dubbing"] = ("none", "no dubbing tool found")
    if f.get("google_libs") and f.get("oauth_client"):
        rec["upload"] = ("youtube-api", "OAuth client found: private uploads through upload.py")
    else:
        missing = [] if f.get("google_libs") else [INSTALL["google"]]
        missing += [] if f.get("oauth_client") else [INSTALL["oauth"]]
        rec["upload"] = ("studio", "upload in YouTube Studio for now; for upload.py: " + "; ".join(missing))
    return rec


def missing_free(f: dict) -> list[str]:
    """Install commands for free pieces that are missing, most useful first."""
    out = []
    if not f.get("ffmpeg"):
        out.append(INSTALL["ffmpeg"])
    if not f.get("node"):
        out.append(INSTALL["node"])
    if not f.get("yt_dlp"):
        out.append(INSTALL["yt-dlp"])
    if not f.get("remotion"):
        out.append(INSTALL["remotion"])
    if not f.get("hyperframes"):
        out.append(INSTALL["hyperframes"])
    if not f.get("faster_whisper"):
        out.append(INSTALL["faster-whisper"])
    if not f.get("ocr"):
        out.append(INSTALL["ocr"])
    return out


def load_stack(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_stack(path: Path, facts: dict | None, choices: dict) -> dict:
    data = load_stack(path)
    data["version"] = 1
    if facts is not None:
        data["facts"] = facts
        data["detected_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data["choices"] = {**(data.get("choices") or {}), **choices}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def parse_sets(items: list[str]) -> dict:
    choices = {}
    for item in items:
        key, sep, value = item.partition("=")
        key, value = key.strip().lower(), value.strip().lower()
        if not sep or key not in ALLOWED:
            raise ValueError(f"--set {item!r}: use <station>=<tool>, stations: {', '.join(STATIONS)}")
        if value not in ALLOWED[key]:
            raise ValueError(f"--set {item!r}: {key} can be {', '.join(sorted(ALLOWED[key]))}")
        choices[key] = value
    return choices


def _status(value) -> str:
    if value in (None, False, ""):
        return "missing"
    return "found" if value is True else str(value)


def render(f: dict, rec: dict, choices: dict) -> str:
    gpu = f.get("gpu")
    gpu_text = f"{gpu['name']} ({gb(gpu['memory_mb'])} GB)" if gpu and gpu.get("memory_mb") else \
        (gpu["name"] if gpu else "none found")
    rows = [("Python", f["python"]), ("FFmpeg", _status(f["ffmpeg"])), ("yt-dlp", _status(f["yt_dlp"])),
            ("Node.js", _status(f["node"])), ("Codex CLI", _status(f["codex"])), ("GPU", gpu_text),
            ("faster-whisper", _status(f["faster_whisper"])), ("Remotion", _status(f["remotion"])),
            ("HyperFrames", _status(f["hyperframes"])), ("ComfyUI (local)", "running" if f["comfyui"] else
                                                            "not running"),
            ("CosyVoice", "found" if f["cosyvoice"] else "not detected (ask)"),
            ("OCR (text dose)", "found" if f.get("ocr") else "missing (optional)"),
            ("ElevenLabs key", "set" if f["elevenlabs_key"] else "not set (ask about a plan)"),
            ("YouTube OAuth", "ready" if f["google_libs"] and f["oauth_client"] else "not set up")]
    width = max(len(r[0]) for r in rows)
    lines = ["Installed", *[f"  {k.ljust(width)}  {v}" for k, v in rows], "", "Suggested stack"]
    for station in STATIONS:
        tool, why = rec[station]
        chosen = choices.get(station)
        mark = f"{chosen} (chosen)" if chosen else tool
        lines.append(f"  {station.ljust(13)} {mark.ljust(24)} {why}")
    todo = missing_free(f)
    if todo:
        lines += ["", "Free pieces you can add (ask the creator before installing):", *[f"  - {t}" for t in todo]]
    lines += ["", "Not detectable: subscriptions (ElevenLabs, ChatGPT/Codex, vidIQ) and whether Remotion's company "
                  "license applies (for-profit companies with more than 3 employees)."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Detect production tools and suggest a tubecrew stack.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--save", action="store_true", help="store facts and choices in ~/.tubecrew/stack.json")
    ap.add_argument("--set", action="append", default=[], metavar="STATION=TOOL",
                    help="record a choice, e.g. voice=cosyvoice (implies --save)")
    ap.add_argument("--show", action="store_true", help="print the saved stack and exit")
    a = ap.parse_args(argv)
    path = tubecrew_home() / "stack.json"
    if a.show:
        print(json.dumps(load_stack(path), indent=2, ensure_ascii=False))
        return 0
    try:
        choices = parse_sets(a.set)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    facts = detect()
    rec = recommend(facts)
    saved = load_stack(path).get("choices") or {}
    if a.save or choices:
        saved = save_stack(path, facts, choices)["choices"]
    if a.json:
        print(json.dumps({"facts": facts, "suggested": {k: {"tool": t, "why": w} for k, (t, w) in rec.items()},
                          "choices": saved, "free_to_add": missing_free(facts)}, indent=2, ensure_ascii=False))
    else:
        print(render(facts, rec, saved))
        if a.save or choices:
            print(f"\nSaved to {path.name} in your tubecrew folder ({'TUBECREW_HOME' if os.environ.get('TUBECREW_HOME') else '~/.tubecrew'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
