# Voice and timing

## The voice engine (`dubbing` in `~/.tubecrew/stack.json`)

### CosyVoice (local, free)

- Give the instruction prompt the language and the tone, for example "speak in fluent, natural Spanish with the
  calm tone of a documentary narrator" or "... like a friendly YouTuber talking to a friend".
- The reference clip is 10-15 s of the creator's own clean recorded voice, not the output of another clone.
- Generate sentence by sentence and keep every take. When a take fails the back-transcription check below,
  generate another with a new seed; after about five takes, change the generation mode or rewrite the sentence.
- Try one sentence before a whole video: some languages come out unintelligible in some setups. Use another
  engine for those.
- The GPU runs one synthesis job at a time. Translations can run in parallel; voices cannot.

### ElevenLabs (credits)

- About one credit per character of the TTS text. Before generating, count the characters, read the remaining
  balance (`GET /v1/user/subscription`), give the creator both numbers and wait for a yes. Many scripts print the
  total only after spending it.
- Generate paragraph by paragraph, so a fix costs one paragraph. Record the credits spent.

### Consent

Clone only a voice whose owner agreed, and never present a cloned voice as someone else.

## Choosing among takes

The gate is `voice_check.py` (check-voiceover skill) on the whole dub. To pick the best take of a sentence before
that, back-transcribe each take with Whisper, normalize both sides (Unicode NFKC, lower case, letters and digits
only) and compare characters with the caption text and with the TTS text; keep the better ratio.

- Below about 0.80 for a sentence (0.85 for a whole paragraph): generate another take.
- Whisper mixes Traditional and Simplified Chinese; an initial prompt in Simplified Chinese keeps it consistent.
- For Japanese, compare readings (kana) instead of kanji when a converter is available.
- A low score caused only by digits written differently is not an error: check by ear before regenerating.
- A high score proves the words, not the delivery. List lines with odd stress or rhythm for a human listen.

## Placement: what `dub_fit.py` checks

| Rule | Value | Why |
|---|---|---|
| Anchor | the second the original paragraph started | the dub stays with its pictures |
| Early start | up to 1 s before the anchor, only when needed | a borrowed pause is not noticed |
| Late end | up to 1 s past the next anchor | same |
| Gap | 0.6 s between paragraphs | the dub does not sound breathless |
| Speed-up | up to 1.25x (ffmpeg `atempo`) | faster speech sounds rushed |
| Drift | FAIL beyond 4 s from the anchor | the narration no longer matches the screen |
| End | the last paragraph ends 0.3 s before `--end` | no clipped last word |
| Edge silence | below -42 dB, not counted | engines pad their output |

A FAIL comes with "rewrite about N% shorter". The first FAIL is usually the cause: a paragraph that does not fit
pushes the next ones late, so fix the earliest one and run the check again.

## Assembling the track

1. Trim edge silence from each take and join a paragraph's sentences with about 0.3 s pauses.
2. Place paragraphs by the rules above; speed up only the paragraphs that need it, never past 1.25x.
3. Mix music and sound effects again under the new voice (duck them under the dub). Do not lay the dub over a
   mix that still contains the original narration.
4. Normalize to about -14 LUFS integrated with true peak at most -1.5 dB, at the full length of the video
   (end screen included), AAC 48 kHz stereo.
5. Build the captions from the placed sentence times.

## Publishing the tracks

The YouTube API can upload captions and set localized titles and descriptions, but it cannot attach extra audio
tracks. The creator adds each language's track in YouTube Studio (Languages). The video stays private until the
creator publishes it.

## When the source changes

Changed source paragraphs mean new translations, new voices and both checks again for those paragraphs, in every
language. If chapter times moved, update the chapter lines in every localized description. Keep a list of the
changed paragraph ids for whoever runs the dubs: the tools do not notice a source change on their own.
