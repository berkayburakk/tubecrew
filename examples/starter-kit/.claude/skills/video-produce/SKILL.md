---
name: video-produce
description: Produces a YouTube video step by step, from choosing the topic to a private upload. Use when asked to "produce a video", "make a new video" or "prepare a video".
---

# Video production workflow (starter version)

Principle: every step writes its output to a file, and the next step starts only when the current one is done and
checked. Stop and ask whenever a decision belongs to the creator. Project folder: `projects/<video-name>/`.

## 1. Topic
- Gather 10-15 candidates from the news, from videos that outperformed on competing channels, and from search
  interest.
- Give every candidate supporting numbers: how many times the best competing video on that topic beat its own
  channel's average, whether interest is rising, and how many channels have already covered it.
- Challenge the candidates like a skeptic and rank the strongest five. The creator makes the final pick.
  Output: `topic.md`.

## 2. Research
- Record every number in `numbers.json`: value, unit, type (official, news or own calculation), link and access date.
- A number without a link is not used. Anything found but not trusted goes into `do-not-use.md`.
- When possible, have a second model verify the numbers independently.

## 3. Script
- Write two different openings; the creator picks one.
- Treat each paragraph as one scene. Mark the word where something should happen on screen: `[[marker]]word`.
- Never type numbers by hand; take them from `numbers.json`. Output: `script.md`.

## 4. Independent review
- Give the script to an agent that did not write it. It compares every claim with its source and lists anything
  wrong or misleading.
- Do not start the voiceover until both lists are empty. Output: `review.md`.

## 5. Voice
- Generate the narration paragraph by paragraph and keep the timing of every word. Keep the voice settings fixed
  in code.
- When a paragraph changes, regenerate only that paragraph.
- Transcribe the result with a tool such as Whisper. If a word was misread, fix the text and regenerate the
  paragraph.

## 6. Visuals
- Give every frame the same style reference.
- For moving clips, first try an open model that runs on your own computer; use a paid cloud video model only when
  the creator asks for it.

## 7. Edit and sound
- Build the edit in code, and tie every on-screen event to the moment its marked word is spoken.
- The number on screen must match the number being spoken at that moment.
- Duck the music under speech; the final loudness for YouTube is about -14 LUFS.

## 8. Thumbnail and render review
- Make a few thumbnails and check their text and logos one by one.
- An agent that did not make the video watches the render end to end: does every on-screen number match the
  spoken one, does any text overflow, does the audio drop out? Output: `render-review.md`.

## 9. Upload
- Upload as private first, with the title, description and chapters ready.
- The creator does the final check; making the video public is the creator's decision.

## Rules
- The rules in `CLAUDE.md` apply at every step.
- When something goes wrong, add one line to `CLAUDE.md` as a lesson so the next video does not repeat it.
