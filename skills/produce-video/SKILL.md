---
name: produce-video
description: Run a YouTube video from idea to a private upload as a gated production line - topic, sourced research, script, voiceover, render, review, upload, then retention follow-up. Use when someone wants to make a video end to end, asks "what's the next step for this video", or starts a new video project. Each stage has its own skill; this one keeps the order and the gates.
---

# Produce a video

A video moves through stations. Each station writes a file, and the next station starts only when the
previous one passed its gate. Thinking (topic choice, story, taste) is yours; counting and measuring is done by
the scripts in each skill; reviewing is done by an agent that did not make the thing.

## The project folder

One folder per video. Create it first:

```
<video>/
  facts.json   script.md   upload.json
  voice/       render/     qa/
```

`qa/` holds every gate result. See CONTRIBUTING.md in the plugin root for the exact file contract.

## The creator's tools

Before the first video, run the `setup` skill once; it saves the creator's tools and subscriptions in
`~/.tubecrew/stack.json`. At every station, use the tool chosen there instead of guessing. If the file is missing,
run `setup` first.

## The line

| # | Station | Skill / agent | Gate (do not move on until) |
|---|---|---|---|
| 1 | Topic | `find-topic` (`demand.py` for demand), then the `topic-skeptic` agent | at least one candidate is ALIVE with evidence |
| 2 | Research | `research-facts` | `facts_check.py facts.json` exits 0 |
| 3 | Script | `write-script`, then the `script-auditor` agent | lint gate passes, A/B verdict merged |
| 4 | Number check | `facts_check.py --script`, the `fact-checker` agent, and Codex when the stack's `verifier` is `codex` | 0 unsourced numbers, every checker PASS |
| 5 | First slice | you + the creator | the creator approved the first 60-90 seconds |
| 6 | Voiceover | the stack's `voice` (ElevenLabs, CosyVoice or a recording), then `check-voiceover` | `voice_check.py` exits 0 |
| 7 | Visuals and edit | the stack's `motion` engine (Remotion, HyperFrames or `morph-clips`) and `clips` (Wan) | text on screen follows the dose rule below |
| 8 | Render check | `check-render` (mix with `normalize_audio.py`), then the `render-auditor` agent | `render-check.json` PASS and `RENDER-AUDIT` PASS for the same file |
| 9 | Package | title, description, `check-thumbnail`, optional `localize` for other languages | `thumbnail-check.json` PASS; thumbnail text does not repeat the title |
| 10 | Upload | the `upload-checker` agent, then `upload-private` | `UPLOAD-GATE` GO, uploaded as **private** (scheduled only with `--schedule`) |
| 11 | Publish | the creator, in YouTube Studio | a human pressed publish or chose the schedule |
| 12 | Learn | `channel-pulse` weekly, `retention-by-scene` after 72 h | one lesson in `lessons.md`; due `decisions.md` rows filled from tool output |

**Shorts:** once the long video passed station 10, `make-shorts` turns it into Shorts with the same gates; each
Short goes public only after its long video.

Station 5 exists because the most expensive mistake is a whole video built on an opening nobody liked. Render
or mock the first 60-90 seconds and ask before you build the rest.

## Rules that hold at every station

- **Never invent a number, quote, result or source.** If a figure is missing, ask for it or write the line
  without it. Every number on screen or in the narration lives in `facts.json` with a link.
- **The maker never reviews their own work.** Reviews go to the agents above, with the files, not a summary.
  Say what level of check you want (every figure, or totals only) and read which level the report ran.
- **Reviewers are for what ships.** Drafts, tests and v1 experiments skip the review agents; the final render
  and anything published do not.
- **Measured problems are fixed, not argued.** A script that exits 1 means fix the work. If a check is wrong
  for this video, the creator decides, and you write down why.
- **On-screen text is punctuation.** Full-screen text cards stay under ~20% of the runtime, never two in a row,
  and never repeat what the narration is saying. Big text is for the hook question, a chapter turn, or the one
  number to remember.
- **Nothing publishes itself.** Uploads are private. Publishing, and anything irreversible, is the creator's
  decision; a schedule is used only when the creator writes `publish_at` and passes `--schedule`.
- **No automated engagement on your own videos.** No script or browser agent watches, likes or comments on
  your public videos. Fake engagement breaks YouTube's rules and can cost the monetization account that all of
  the creator's channels share.

## Lessons become checks

Copy this skill's `templates/` into the channel folder (`decisions.md`, `lessons.md`, `gate-counter.md`; `costs.md`
goes into each video folder) and follow `references/learning-loop.md`. Lessons are numbered and append-only; check for duplicates before adding one. At the
start of each video, put the lessons that apply into `<video>/preflight.md`. When the same lesson shows up twice,
turn it into a script check instead of a longer rule. Log every real catch in `gate-counter.md`; after a month, a
code gate that caught nothing is loosened and an agent gate that caught nothing is removed. The creator decides.

## What to report back

After each station: what was produced (path), the gate result (exit code or verdict line), and the single next
action. Do not hand over a list of twenty things.
