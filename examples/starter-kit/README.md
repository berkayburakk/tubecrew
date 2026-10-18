# Starter kit

The smallest working version of the tubecrew production line: one skill and one rules file that you copy into a
video project. Start here to see the idea before installing the plugin.

| File | Purpose |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | A channel rules file: no advice, no fabricated evidence, a source for every number, and a lesson for every mistake |
| [`.claude/skills/video-produce/`](.claude/skills/video-produce/SKILL.md) | A nine-step production skill, from topic selection to a private upload |

## Usage

1. Install [Claude Code](https://code.claude.com/docs/en/quickstart).
2. Copy `.claude/` and `CLAUDE.md` into your video project.
3. Open Claude Code in that folder and type `produce a video: <topic>`.
4. Adapt the skill to your own tools, voice and rules. After every mistake, add one line to `CLAUDE.md`.

When you outgrow it, install the full plugin from the [main README](../../README.md). It follows the same steps
and adds scripts that verify each one.

## Tools that fit this workflow

| Step | Tool |
|---|---|
| Setup, scripting, editing | [Claude Code](https://code.claude.com/docs/en/quickstart) |
| Independent research checks with a second model | [Codex CLI](https://developers.openai.com/codex/cli) |
| Voice cloning and narration | [ElevenLabs](https://elevenlabs.io) |
| Voiceover verification | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) |
| Image-to-video clips on a local GPU | [Wan](https://github.com/Wan-Video/Wan2.2) |
| Video editing in code | [Remotion](https://github.com/remotion-dev/remotion) |
| Mixing, music ducking, loudness | [FFmpeg](https://ffmpeg.org) |
| Local dubbing | [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) |
| Uploading | [YouTube Data API](https://developers.google.com/youtube/v3) |

## Seven habits with the biggest payoff

1. If you explain the same thing to your agent every session, turn it into a skill.
2. Ask for a supporting number next to every topic candidate.
3. Ask for a link next to every figure, and drop any figure without one.
4. Have a different model or agent review the text the first one wrote.
5. After generating the voiceover, transcribe it and compare it with the script.
6. Give image generators a style reference, or every image will look different.
7. Before uploading, have an agent that did not make the video review it.
