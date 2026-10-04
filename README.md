# Claude Code ile YouTube videosu: başlangıç dosyası

Berko YouTube kanalındaki **"Claude Code ile YouTube Videosu Nasıl Yapılır?"** videosunda gösterdiğim üretim
hattının sade hali. Kendi hattınızı kurarken buradan başlayabilirsiniz.

*English version below.*

## İçinde ne var

| Dosya | Ne |
|---|---|
| [`.claude/skills/video-uret/SKILL.md`](.claude/skills/video-uret/SKILL.md) | Videoda gösterdiğim video üretim skill'inin sade hali. Konudan gizli yüklemeye dokuz adım. |
| [`CLAUDE.md`](CLAUDE.md) | Kural dosyası örneği: tavsiye yok, uydurma kanıt yok, her rakamın kaynağı var; her hata bir ders olarak eklenir. |
| [`ornek/rakamlar.json`](ornek/rakamlar.json) | Araştırmada her rakamın nasıl tutulduğuna örnek (değerler kurmaca). |
| [`en/`](en/) | Aynı dosyaların İngilizcesi. |

## Nasıl kullanılır

1. Claude Code'u kurun: <https://code.claude.com/docs/en/quickstart>
2. Video projenizin klasörüne `.claude/` klasörünü ve `CLAUDE.md` dosyasını kopyalayın.
3. Claude Code'u o klasörde açın ve "video üret: <konu>" yazın.
4. Skill'i kendi kanalınıza göre düzenleyin: araçlarınızı, ses ayarınızı ve kurallarınızı yazın.
   Her hatadan sonra `CLAUDE.md`'ye bir ders ekleyin.

Skill nedir, nasıl yazılır: <https://code.claude.com/docs/en/skills>

## Videoda kullandığım araçlar

| Durak | Araç |
|---|---|
| Kurulum, senaryo, kurgu | Claude Code: <https://code.claude.com/docs/en/quickstart> |
| Araştırma doğrulaması, görsel kareler | Codex CLI: <https://learn.chatgpt.com/docs/codex/cli> |
| Ses klonu ve anlatım | ElevenLabs: <https://elevenlabs.io> |
| Sesle prompt yazma | Wispr Flow: <https://wisprflow.ai> |
| Sesi geri dinleme | Whisper: <https://github.com/openai/whisper> (yerelde faster-whisper: <https://github.com/SYSTRAN/faster-whisper>) |
| Kareden video klip (kendi ekran kartımda) | Wan: <https://github.com/Wan-Video/Wan2.2> |
| Kodla kurgu | Remotion: <https://github.com/remotion-dev/remotion> |
| Ses birleştirme, müzik kısma, ses düzeyi | FFmpeg: <https://ffmpeg.org> |
| Yerel dublaj | CosyVoice: <https://github.com/FunAudioLLM/CosyVoice> |
| Yükleme | YouTube Data API: <https://developers.google.com/youtube/v3> |

Seedance gibi kredili bulut video modellerini kullanmadım; yapay zekâyla üretilen klipler kendi ekran kartımda, Wan ile.

## Siz de deneyin (videodaki ipuçları)

1. Claude'a her seferinde aynı şeyi anlatıyorsanız, onu bir skill'e çevirin.
2. Yapay zekâdan konu isterken her adayın yanında bir sayı isteyin.
3. Araştırmada her rakamın yanında link isteyin; linki olmayan rakamı kullanmayın.
4. Yapay zekânın yazdığı metni başka bir yapay zekâya kontrol ettirin.
5. Ses üretimi bitince Whisper gibi bir araçla denetleyin.
6. Görsel üretirken referans verin; yoksa her görsel farklı çıkar.
7. Yüklemeden önce videoyu bağımsız bir ajana analiz ettirin.

---

## English

The simple version of the production pipeline from the Berko YouTube video **"How to Make a YouTube Video With Claude Code"**.

- [`en/.claude/skills/video-produce/SKILL.md`](en/.claude/skills/video-produce/SKILL.md): the video production skill, nine steps from topic to private upload.
- [`en/CLAUDE.md`](en/CLAUDE.md): a rules file example (no advice, no made-up evidence, every number has a source; every mistake becomes a lesson).
- [`ornek/rakamlar.json`](ornek/rakamlar.json): an example of how each number is tracked during research (made-up values).

How to use: install Claude Code (<https://code.claude.com/docs/en/quickstart>), copy `en/.claude/` and `en/CLAUDE.md`
into your video project, open Claude Code there and type "produce a video: <topic>". Then adapt the skill to your channel.
The tool list above applies to the English version as well.
