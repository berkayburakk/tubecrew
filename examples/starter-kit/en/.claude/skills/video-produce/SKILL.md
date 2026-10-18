---
name: video-produce
description: Produces a YouTube video step by step, from picking the topic to a private upload. Use when asked to "produce a video", "make a new video" or "prepare a video".
---

# Video production flow (simple version)

Principle: every step writes its output to a file. Don't move on until a step is done and checked.
Stop and ask me when a decision is needed. Project folder: `projects/<video-name>/`

## 1. Topic
- Come up with 10-15 topic candidates: scan the news, videos that worked on rival channels, and search interest.
- Put numbers next to each candidate: how many times its channel average the best rival video on that topic got,
  whether interest is rising, how many channels already covered it.
- Test the candidates like a skeptic and rank the strongest five. I make the final pick. → `topic.md`

## 2. Research
- Write every number to `numbers.json`: value, unit, type (official / news / our own calculation), link, access date.
- A number without a link is not used. Put anything you found but don't trust into `do-not-use.md`.
- If possible, have a second model verify the numbers independently.

## 3. Script
- Write two different openings; I pick one.
- Each paragraph is one scene. Mark the word where something should happen on screen: `[[marker]]word`.
- Don't type numbers by hand; take them from `numbers.json`. → `script.md`

## 4. Independent review
- Give the script to an agent that didn't write it: it compares claims with the sources and lists wrong or misleading ones.
- Don't start the voice until wrong and misleading are both zero. → `review.md`

## 5. Voice
- Generate paragraph by paragraph and keep the timing of every word. Keep the voice settings fixed in code.
- If a paragraph changes, only that paragraph is regenerated.
- Transcribe the result back with a tool like Whisper; if a word is misread, change it and regenerate the paragraph.

## 6. Visuals
- Give every frame the same style reference.
- If you need moving clips, first try an open model that runs on your own computer;
  use a paid cloud video model only if I ask for it.

## 7. Edit and sound design
- Build the edit in code. Every on-screen event is tied to the moment its marked word is spoken.
- The number on screen must match the number being said at that moment.
- Music ducks under speech; the final loudness for YouTube is about −14 LUFS.

## 8. Thumbnail and render review
- Make a few thumbnails; check the text and the logos one by one.
- An agent that didn't make the video watches the render end to end: does the on-screen number match the spoken one,
  does any text overflow, does the audio cut out. → `render-review.md`

## 9. Publishing
- Upload as private first, with title, description and chapters ready.
- I do the final check; making it public is my call.

## Rules
- The rules in `CLAUDE.md` apply at every step.
- When something goes wrong, add one line to `CLAUDE.md` as a lesson so the next video doesn't repeat it.
