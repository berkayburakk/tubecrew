---
name: localize
description: Translate and dub a finished video into other languages - a meaning-first translation that keeps every number from facts.json, a native-listener audit, the creator's dubbing voice, a back-transcription check and a timing check that each dubbed paragraph fits the original picture. Use for "dub this video", "translate the narration", "make a Spanish version" or "add audio tracks in other languages".
---

# Localize a video

The viewer should hear a narrator who talks the way people in their country talk, saying exactly the same facts,
in time with the same pictures. Start from the final script (after the fact-check) and the final render: a later
change to the source means re-translating and re-voicing every changed paragraph.

`$SKILL` below is this skill's folder.

## Files

```
dub/<lang>/script.md       translation, same paragraph ids (P01, P02, ...) as script.md
dub/<lang>/tts.md          the text sent to the voice engine (numbers spelled out), if it differs
dub/<lang>/voice/P01.wav   one audio file per paragraph, named by its id
dub/<lang>/captions.srt    captions timed to the dub
qa/voice-check-<lang>.json, qa/dub-fit-<lang>.json
upload.json                "localizations": {"<lang>": {"title": ..., "description": ...}}
```

## 1. Translate for meaning, not word by word

- Rebuild each sentence the way a native narrator would say it: word order, idiom, form of address and rhythm of
  the target language. Keep the creator's questions, doubts and emphasis; never invent experiences or evidence.
- Keep exactly: every number, unit and date in `facts.json`; the currency (a US company's dollars stay dollars);
  the country and institutions of the events; hedges ("about", "reportedly", "the company says"); product names
  and commands.
- Keep each sentence in its own paragraph: every paragraph has to fit its own slot in the picture.
- Length targets per language, the number table and the regional variant: `$SKILL/references/translation.md`.
  Shorten by cutting filler and repetition, never a number, a name, a direction (up/down, profit/loss) or a hedge.
- Title and description say the same promise in the target language, with the same chapter times and links.

## 2. Native-listener audit

Read every paragraph twice, first against the source for meaning, then aloud for naturalness:

- Hedges kept: "can keep" did not become "keeps".
- Time direction right: "years ahead" did not become "years ago".
- No subject drift: who sells, who pays, who owns is still clear.
- No translationese: calqued idioms, source word order, bureaucratic verbs.
- Names, terms and numbers read the way this language says them.

A model-only naturalness check is not human review. Unless a native speaker listened, write "model audit, not a
native speaker" in the report.

## 3. Voice

Read the `dubbing` station from `~/.tubecrew/stack.json` (run the `setup` skill if it is missing):

- `cosyvoice`: local, free, one job at a time on the GPU. Use a clean reference clip of the creator's own voice.
- `elevenlabs`: costs credits. Estimate them first (about one credit per character of `tts.md`), tell the creator
  the number and the remaining balance, and generate only after a yes.
- `none`: stop after translated captions, title and description.

Only clone a voice whose owner agreed. Detail and retry rules: `$SKILL/references/voice-and-timing.md`.

## 4. Check each language

Back-transcribe the dub with the check-voiceover skill, then check the timing:

```bash
python <check-voiceover>/scripts/voice_check.py dub/es/script.md dub/es/narration.wav --language es \
  --out qa/voice-check-es.json
python $SKILL/scripts/dub_fit.py script.md qa/voice-check-transcript.json dub/es/voice --lang es
```

`voice_check.py` takes the paragraph files joined in order (or the assembled track). `dub_fit.py` needs the
original narration's word-timed transcript (voice_check.py saves one when it transcribes audio). It FAILs a
paragraph that needs more than 1.25x speed-up or starts more than 4 s from where the original paragraph started,
and says about how much shorter to rewrite it. Fix by rewriting the translation, never by speeding the voice past
the cap; then re-voice only the changed paragraphs and run both checks again. A high back-transcription score
does not prove natural delivery: list the lines that need a human listen.

## 5. Package

- Captions follow the dubbed timing; a number stays on one line with its unit.
- Write each language's title (at most 100 characters) and description (at most 5000 bytes) into
  `upload.json` `localizations`; `upload-private` sends them with the video.
- YouTube's API cannot attach extra audio tracks: the creator adds each dubbed track in YouTube Studio. Nothing
  here publishes anything.

## Report back

Per language: status (text ready, audio ready or blocked), the voice_check and dub_fit verdict lines, the
paragraphs re-voiced, lines that need a human listen, credits spent, and who did the native audit.
