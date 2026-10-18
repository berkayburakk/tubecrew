---
name: check-voiceover
description: Check that a generated or recorded voiceover actually says the script - skipped phrases, wrong numbers, stray repeats and garbled names, with timestamps. Use right after TTS or recording, for "check the voiceover", "did the AI voice skip anything", "compare audio to script", or before syncing visuals to narration.
---

# Check the voiceover

Text-to-speech skips words, reads "12.5" as "125", and repeats phrases. Listening for that across ten minutes is
slow and unreliable; comparing a transcript with the script is fast and exact.

`$SKILL` below is this skill's folder.

## Run

```bash
python $SKILL/scripts/voice_check.py script.md voice/narration.srt
```

The second argument is a transcript (SRT, VTT or Whisper JSON) or the audio file itself. Audio needs
`pip install faster-whisper`; any transcript from your editor, Descript or Whisper works too. A template script
with `{placeholders}` must be compiled to plain text first.

## Read the result

- **FAIL**: a number that differs, or a skipped run of three or more words. Regenerate or re-record that
  paragraph; do not "fix it in the edit".
- **WARN**: other differences (no number involved, fewer than three words skipped), often the recognizer
  mishearing a name. Listen to just those timestamps.
- Word accuracy near 100% with only name warnings is a clean take.

The report goes to `qa/voice-check.json`. Exit 1 blocks the next station.

## After a fix

Only the changed paragraphs need new audio. Timings after them shift, so re-run this check and anything that
depends on word timings (captions, animations tied to words, chapter timestamps).
