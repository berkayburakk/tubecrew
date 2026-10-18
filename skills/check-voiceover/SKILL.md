---
name: check-voiceover
description: Check that a generated or recorded voiceover actually says the script - skipped phrases, wrong numbers, stray repeats, garbled names and paragraphs read at an impossible pace, with timestamps - then pick the best generated take per paragraph and shorten long pauses without stretching speech. Use right after TTS or recording, for "check the voiceover", "did the AI voice skip anything", "compare audio to script", "pick the best take", "the narration has long pauses", or before syncing visuals to narration.
---

# Check the voiceover

Text-to-speech skips words, reads "12.5" as "125", repeats phrases and sometimes reads a paragraph over and over.
Listening for that across ten minutes is slow and unreliable; comparing a transcript with the script is fast and
exact.

`$SKILL` below is this skill's folder.

## Run

```bash
python $SKILL/scripts/voice_check.py script.md voice/narration.srt
```

The second argument is a transcript (SRT, VTT or Whisper JSON) or the audio file itself. Audio needs
`pip install faster-whisper`; any transcript from your editor, Descript or Whisper works too. A template script
with `{placeholders}` must be compiled to plain text first. Write one paragraph per line in `script.md`: every
line is a paragraph, and all three scripts here number paragraphs the same way.

## Read the result

- **FAIL**: a number that differs, a skipped run of three or more words, or a paragraph paced outside
  100-300 words per minute: too slow is a repeat loop, too fast a swallowed sentence. Regenerate or re-record
  that paragraph; do not "fix it in the edit".
- **WARN**: other differences (often the recognizer mishearing a name), or a pace outside 150-215 wpm. A slow
  WARN is a question, not a verdict: a dense paragraph can honestly read slowly. Listen to those timestamps.
- Pace needs word timings (Whisper JSON with words, or the audio). The bands are tuned for generated voices;
  for a recorded voice pass `--wpm-band 130,190`.
- The report (`qa/voice-check.json`) lists every paragraph; `failing_paragraphs` is what to regenerate.
  Exit 1 blocks the next station.

## Generated voice

1. Generate paragraph by paragraph into `voice/paragraphs/p001.wav`, `p002.wav`, ...
   (`python $SKILL/scripts/pick_takes.py script.md --list` prints the numbered paragraphs). Follow the TTS input
   rules in `references/failure-modes.md`. Join the files in order without adding generated silence.
2. Run `voice_check.py` on the joined audio. Note the failing paragraphs.
3. Regenerate only those, three or four takes each with **new seeds** (a fixed seed repeats the same mistake),
   into `voice/candidates/<paragraph number>/<any name>.wav`.
4. Pick:
   ```bash
   python $SKILL/scripts/pick_takes.py script.md voice/candidates --apply voice/paragraphs
   ```
   Takes are ranked by word fidelity; a take only counts when every number and name of the paragraph is heard
   exactly and its pace is plausible. Exit 1 names the paragraphs with no usable take: back to step 3. Three
   rounds is normal. If one name fails in every take, change the TTS input instead of adding takes.
5. Join again, then shorten long pauses (speech is never stretched; a new file is written):
   ```bash
   python $SKILL/scripts/trim_silence.py voice/narration.wav --measure
   python $SKILL/scripts/trim_silence.py voice/narration.wav
   ```
6. Final `voice_check.py` on the trimmed file. Trim **before** the final transcription and alignment: every
   timing after the first cut moves.

## After a fix

Only the changed paragraphs need new audio. Timings after them shift, so re-run this check and anything that
depends on word timings (captions, animations tied to words, chapter timestamps).

When the check and your ears disagree, or before you build captions, read `references/failure-modes.md`: the
recognizer inventing words at seams, cutting and patching audio, TTS input rules, and captions.
