# Voiceover failure modes

Read this when a check and your ears disagree, before patching audio, before writing TTS input, and before
building captions. Each rule is here because the failure is easy to miss and expensive to find late.

## The recognizer invents words, not the voice

- **Seams make Whisper hallucinate.** With `condition_on_previous_text` on, Whisper carries about 30 seconds of
  context across a seam in stitched or patched audio and writes words that were never said, sometimes whole
  sentences or a generic "thanks for watching". `voice_check.py` and `pick_takes.py` decode with it off. Turn it
  off in any other tool that transcribes or aligns the same audio.
- **A transcript can be wrong while the audio is fine.** Word timings and captions are derived data. Before
  trusting a transcript for timing, diff it with the script; where a region's similarity drops below 0.95,
  inspect that region (listen, or decode it again).
- **Re-transcribe a bad region alone**, not the whole file. Cut it out with ffmpeg and decode just that piece:
  decoding is deterministic, so the same full pass invents the same words again, while a short fresh context
  usually does not. Then patch the word timings of that region only.
- **The decoder's language model "corrects" toward the likely sentence**, so a context-rich decode can hide a
  real misread, and a single suspicious word cannot be settled by voting across overlapping decodes. Decode the
  region alone, or with a second model, before you regenerate or accept it.
- Several zero-length words (start equals end) close together are a sign of invented text.

## Cutting and patching audio

- **Never fill a gap with digital silence** (all-zero samples). The drop far below the recording's own noise
  floor makes Whisper invent words there, and listeners hear dead air. Use room tone: quiet stretches taken
  from the same recording, laid under the whole patched window, not only into the gap.
- **Cut inside real silence and leave about 120 ms after the last word.** A cut at the silence-detection
  threshold clips the fading end of a word ("payments" turns into "paint"). `trim_silence.py` keeps at least
  half its ceiling on each side of a cut for this reason. Generated takes may also start with a short breath or
  noise burst; trim it before the seam.
- **Replacing a take inside a finished edit:** keep the window's length exactly, spreading any difference over
  the pauses, so everything timed after it stays where it was. If you cannot, re-run alignment and every step
  that depends on word timings.
- **Separate generation calls can drift in timbre.** A take with perfect words can still sound like a different
  voice next to its neighbours. Listen across each seam before accepting it.

## TTS input (the text you send to the voice)

- **Spell out numbers, symbols and "%" in the TTS input only** ("three hundred and forty-two million",
  "twelve percent"). `script.md` stays as written, so the checks, captions and fact files keep the digits; both
  checkers fold spelled-out English numbers back to digits. Number-dense sentences are where generated voices go
  off the rails.
- Write "the first year / the second year", not fragments like "Year one / Year two": they get misread and
  misheard.
- Avoid em-dashes (some voices pronounce them or garble the words around them); use a period or a comma. Avoid
  colons in clock times: write "a quarter to ten", not "9:45".
- Very short list fragments ("Lower fees. Faster delivery. Better support.") read badly. Turn the list into one
  flowing sentence.
- Long paragraphs break more often: past roughly 40 words the model is more likely to drift into invented
  text. Split them (one paragraph per line in `script.md`).
- **A name the voice cannot say:** first try a phonetic respelling in the TTS input only. If it still fails,
  rephrase without it ("the company's own historian") and keep the name on screen and in the description. A
  garbled name is worse than a general reference.
- A misread that changes the meaning ("want" heard as "won") is fixed by changing the word in both the script
  and the TTS input, not by more takes.

## Takes

- A fixed seed with deterministic decoding produces the same mistake again. Use new seeds every round.
- A repeat loop makes a paragraph long; an invented or garbled sentence keeps the length normal. That is why
  there are two checks: pace (cheap, catches loops) and word fidelity (catches everything that changes words).
- Fidelity is computed over whole words with difflib's autojunk off. A character-level ratio, or autojunk on,
  collapses on long text and makes a perfect take look broken.
- If every take fails on the same name, the recognizer may be the one mishearing it. Listen; if the voice is
  right, pass `--waive NAME` (it is recorded in the report).

## Captions

- **Never ship raw ASR captions.** Keep Whisper's word timing and put the true script text back: numbers,
  names, spelling. Then read every proper name and every number with its unit.
- Keep a number and its unit in one caption block and on one line ("$48.2 billion", not "$48" / ".2 billion").
- ASR word ends often land a few hundred milliseconds early. Let a paragraph's last caption block run to the
  end of the speech instead of closing on the last word's end time.
- At most two lines of about 42 characters, and about 6 seconds per block.

## Joining paragraph files

Write `voice/list.txt` with one line per paragraph, in order (paths are relative to the list file):

```
file 'paragraphs/p001.wav'
file 'paragraphs/p002.wav'
```

```bash
ffmpeg -f concat -safe 0 -i voice/list.txt -c copy voice/narration.wav
```

All files must share sample rate, sample format and channel count; if they do not, drop `-c copy` so ffmpeg
re-encodes. Do not insert generated silence between paragraphs: each take carries its own pauses, and
`trim_silence.py` evens out the long ones afterwards.
