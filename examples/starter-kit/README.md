# Starter kit

The smallest possible version of the tubecrew line: one skill and one rules file you copy into a video project.
Start here if you want to see the idea before installing the plugin. *Türkçesi aşağıda.*

| Folder | What is inside |
|---|---|
| [`en/`](en/) | `CLAUDE.md` (a rules file: no advice, no made-up evidence, every number has a source, every mistake becomes a lesson) and `.claude/skills/video-produce/` (nine steps from topic to a private upload) |
| [`tr/`](tr/) | The same files in Turkish, plus `ornek/rakamlar.json`, an example of how each number is tracked (fictional values) |

**Use it:** install [Claude Code](https://code.claude.com/docs/en/quickstart), copy `en/.claude/` and
`en/CLAUDE.md` into your video project, open Claude Code there and type "produce a video: <topic>". Then edit the
skill for your own tools, voice and rules, and add a line to `CLAUDE.md` after every mistake.

When you outgrow it, install the full plugin (see the [main README](../../README.md)): the same steps, with scripts
that check each one.

## Tools that fit this line

| Step | Tool |
|---|---|
| Setup, script, edit | Claude Code: <https://code.claude.com/docs/en/quickstart> |
| A second model for checking research | Codex CLI: <https://developers.openai.com/codex/cli> |
| Voice cloning and narration | ElevenLabs: <https://elevenlabs.io> |
| Checking the voiceover | faster-whisper: <https://github.com/SYSTRAN/faster-whisper> |
| Image to video clips on your own GPU | Wan: <https://github.com/Wan-Video/Wan2.2> |
| Editing in code | Remotion: <https://github.com/remotion-dev/remotion> |
| Mixing, ducking music, loudness | FFmpeg: <https://ffmpeg.org> |
| Local dubbing | CosyVoice: <https://github.com/FunAudioLLM/CosyVoice> |
| Uploading | YouTube Data API: <https://developers.google.com/youtube/v3> |

## Seven habits that make the biggest difference

1. If you explain the same thing to the agent every time, turn it into a skill.
2. Ask for a number next to every topic candidate.
3. Ask for a link next to every number; do not use a number without one.
4. Have a different model or agent check the text the first one wrote.
5. After generating the voice, transcribe it and compare it with the script.
6. Give image generators a style reference, or every image comes out different.
7. Before uploading, have an agent that did not make the video review it.

---

## Türkçe

tubecrew hattının en sade hâli: bir video projesine kopyaladığınız tek skill ve tek kural dosyası. Plugin'i
kurmadan önce fikri görmek istiyorsanız buradan başlayın.

- [`tr/CLAUDE.md`](tr/CLAUDE.md): kural dosyası örneği (tavsiye yok, uydurma kanıt yok, her rakamın kaynağı var;
  her hata bir ders olarak eklenir).
- [`tr/.claude/skills/video-uret/`](tr/.claude/skills/video-uret/SKILL.md): konudan gizli yüklemeye dokuz adım.
- [`tr/ornek/rakamlar.json`](tr/ornek/rakamlar.json): araştırmada her rakamın nasıl tutulduğuna örnek
  (değerler kurmaca).

**Kullanım:** Claude Code'u kurun, `tr/.claude/` klasörünü ve `tr/CLAUDE.md` dosyasını video projenizin klasörüne
kopyalayın, Claude Code'u orada açıp "video üret: <konu>" yazın. Skill'i kendi araçlarınıza, sesinize ve
kurallarınıza göre düzenleyin; her hatadan sonra `CLAUDE.md`'ye bir ders ekleyin. Yukarıdaki araç tablosu ve yedi
alışkanlık Türkçe sürüm için de geçerli.
