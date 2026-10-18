# Tool catalog by station

tubecrew checks the output of every station the same way, whichever tool produced it. This page lists the tools
that fit each station, what they cost, and how the line uses them. Licenses were checked on 2026-10-18; confirm
current terms on each project's page.

## Research

| Tool | Cost | How tubecrew uses it |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Free (Unlicense) | `outliers.py` pulls public channel catalogs to compare each video with its channel's median; `demand.py` reads search results for a reachable step; `transcripts.py` pulls competitor captions as text |
| [Wikimedia pageviews](https://wikimedia.org/api/rest_v1/) | Free, no key | `demand.py` measures curiosity as a multiple of a topic that already worked for the creator |
| vidIQ | Subscription | Optional: keyword volume and outlier search through its own tools; not required |

## Second model for checks (`verifier`)

| Tool | Cost | How tubecrew uses it |
|---|---|---|
| `fact-checker` agent | Included | Default: an independent agent audits every claim against primary sources |
| [Codex CLI](https://github.com/openai/codex) | ChatGPT plan or OpenAI API key (Apache-2.0 CLI) | Runs the same audit with a different model family, read-only |

Codex cross-check, from the video project folder:

```bash
codex exec --sandbox read-only --search --skip-git-repo-check -o qa/FACT-CHECK-codex-<date>.md \
  "Audit every number and claim in script.md against primary sources. facts.json lists the sources found so far; \
treat it as a lead, not proof. Return VERDICT: PASS or FAIL, then a table: location, claim, status \
(OK, WRONG, MISLEADING, UNSOURCED, STALE), source URL with an exact quote, fix hint."
```

Compare its table with the `fact-checker` agent's. A claim either one marks WRONG is fixed; a disagreement goes to
the creator.

## Voice

| Tool | Cost | How tubecrew uses it |
|---|---|---|
| [ElevenLabs](https://elevenlabs.io) | Subscription, credits per character | Narration with a cloned or stock voice, generated paragraph by paragraph |
| [CosyVoice](https://github.com/FunAudioLLM/CosyVoice) | Free (Apache-2.0), local NVIDIA GPU | Local narration and voice cloning without a subscription |
| Your own recording | Free | The most trusted voice; `check-voiceover` compares a take with the script just the same |

Whatever the source, `check-voiceover` runs before visuals are synced to the narration. For generated voices,
`pick_takes.py` keeps the most faithful take per paragraph and `trim_silence.py` shortens long pauses without
stretching speech.

## Transcription

| Tool | Cost | How tubecrew uses it |
|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Free (MIT), CPU or GPU | Word timings for `check-voiceover`, captions and chapter timestamps |
| Your editor's transcript | Included in the editor | Any SRT, VTT or Whisper JSON works |

## Motion design and editing in code (`motion`)

| Tool | Cost | How tubecrew uses it |
|---|---|---|
| [Remotion](https://github.com/remotion-dev/remotion) | Free for individuals, non-profits and for-profit companies with up to 3 employees; a company license otherwise | Videos written in React; install its [agent skills](https://github.com/remotion-dev/skills) with `npx skills add remotion-dev/skills` |
| [HyperFrames](https://github.com/heygen-com/hyperframes) | Free (Apache-2.0) | Videos written in HTML and CSS, built for agents; Claude Code plugin or `npx skills add heygen-com/hyperframes` |
| `morph-clips` | Included | Motion-graphic B-roll timed to the narration: one shape that morphs and never cuts |
| Your video editor | Your editor's license | Final assembly by hand; the render gate still applies |

Pick one engine per project. Prefer Remotion if the creator reads React, HyperFrames if they read HTML. Both export
an MP4 that `check-render` measures. With [RapidOCR](https://github.com/RapidAI/RapidOCR) installed
(`pip install rapidocr-onnxruntime`, Apache-2.0), `text_dose.py` also measures how much of the runtime is text cards.

## Generated clips (`clips`)

| Tool | Cost | How tubecrew uses it |
|---|---|---|
| [Wan 2.2](https://github.com/Wan-Video/Wan2.2) | Free (Apache-2.0), local NVIDIA GPU | Image-to-video and text-to-video clips on the creator's own GPU |
| [ComfyUI](https://github.com/comfyanonymous/ComfyUI) | Free (GPL-3.0) | The usual way to run Wan locally; [Wan 2.2 workflow guide](https://docs.comfy.org/tutorials/video/wan/wan2_2) |

The Wan README runs the 5B model on 24 GB cards (RTX 4090 class); 12-16 GB cards work through ComfyUI with fp8
weights or offloading, more slowly. Give every generated frame the same style reference. Realistic generated
footage of real people, places or events needs `synthetic_media: true` in `upload.json`.

## Dubbing

| Tool | Cost | How tubecrew uses it |
|---|---|---|
| CosyVoice | Free, local GPU | Cross-language narration in the creator's voice; translate the script first, then run `check-voiceover` per language |
| ElevenLabs | Subscription | Dubbing and translated narration with credits |

The `localize` skill runs the translation, a native-listener audit, `check-voiceover` per language and
`dub_fit.py`, which checks that every dubbed paragraph fits the original timing. Translated titles and descriptions
go into `upload.json` `localizations`. YouTube's API cannot attach extra audio tracks: add dubbed tracks in
YouTube Studio.

## Music

ElevenLabs Music (subscription) or the free YouTube Audio Library. Keep music under the narration;
`normalize_audio.py` brings the final mix to -14 LUFS and `check-render` measures it.

## Upload and analytics

| Tool | Cost | How tubecrew uses it |
|---|---|---|
| YouTube Data API and Analytics API | Free daily quota, your own Google Cloud project | `upload.py` (private uploads, optional `--schedule`), `retention.py` and `pulse.py`; setup in `upload-private/references/oauth-setup.md` |
| YouTube Studio | Free | Publishing, end screens, audio tracks, impressions and click-through rate (not in the API) |
