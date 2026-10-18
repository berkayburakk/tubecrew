---
name: setup
description: Set up tubecrew for this creator - detect which production tools are already installed (Remotion, HyperFrames, Wan, CosyVoice, Codex, ffmpeg, a GPU), ask about subscriptions (ElevenLabs, ChatGPT/Codex), suggest a tool for every station of the production line, install the free pieces with permission, and save the choices. Use on first run, for "set up tubecrew", "what tools can I use", "I have ElevenLabs / a GPU / Codex", or before the first video.
---

# Set up the crew

tubecrew does not force a toolset. It checks what the creator already has and pays for, then fits the production
line around it. Run this once, and again whenever the creator's tools change.

`$SKILL` below is this skill's folder.

## 1. Detect

```bash
python $SKILL/scripts/detect_stack.py
```

It reports what is installed (Python, FFmpeg, yt-dlp, Node.js, Codex CLI, the GPU and its memory, faster-whisper,
Remotion and HyperFrames skills, a running ComfyUI, CosyVoice, whether `ELEVENLABS_API_KEY` is set) and suggests a
tool for each station. It never prints secrets and never installs anything.

## 2. Ask what code cannot see

Ask in one round, skipping anything already known:

- Subscriptions: ElevenLabs (voice cloning, narration, music), a ChatGPT plan for Codex, vidIQ.
- Whether they narrate with their own recorded voice.
- For Remotion: is the work for a for-profit company with more than 3 employees? Then Remotion needs a company
  license; HyperFrames (Apache-2.0) has no such condition.
- Which they prefer to read and edit: React (Remotion) or HTML/CSS (HyperFrames). Both pass the same render gate.

## 3. Recommend, then install with permission

Present one line per station: the suggested tool, why, and the alternative. The full catalog with costs and
licenses is `$SKILL/references/tools.md`.

Offer to install the free pieces that are missing, and run each command only after the creator says yes (they
change the creator's machine or agent setup):

| Piece | Command |
|---|---|
| Remotion agent skills | `npx skills add remotion-dev/skills` (new project: `npx create-video@latest`) |
| HyperFrames (Claude Code) | `claude plugin marketplace add heygen-com/hyperframes` then `claude plugin install hyperframes@hyperframes` |
| HyperFrames (other agents) | `npx skills add heygen-com/hyperframes` |
| yt-dlp, faster-whisper | `pip install yt-dlp faster-whisper` |
| YouTube API libraries | `pip install google-auth google-auth-oauthlib requests` |

Remotion and HyperFrames are installed by default when the creator agrees, because the `Make` station relies on
code-based motion design. Paid tools (ElevenLabs, Codex) are only suggested, never signed up for. Wan and CosyVoice
need a GPU and a manual install; point to their guides in `references/tools.md`. A plugin installed now is
available after the agent restarts.

## 4. Save

Record the creator's decisions, so every later station uses the same tools:

```bash
python $SKILL/scripts/detect_stack.py --set voice=elevenlabs --set motion=remotion --set verifier=codex
```

Stations and values: `research` (yt-dlp, vidiq, manual), `verifier` (codex, claude-agent), `voice` (elevenlabs,
cosyvoice, recording, other-tts), `transcription` (faster-whisper, editor), `motion` (remotion, hyperframes,
morph-clips, editor), `clips` (wan, none), `dubbing` (cosyvoice, elevenlabs, none), `upload` (youtube-api, studio).
The file is `~/.tubecrew/stack.json`; `--show` prints it.

## Report back

The stack as a short table (station, tool, cost), what was installed in this run, and the one next step, usually
"start a video with produce-video".
