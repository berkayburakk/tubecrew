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

## The line

| # | Station | Skill / agent | Gate (do not move on until) |
|---|---|---|---|
| 1 | Topic | `find-topic`, then the `topic-skeptic` agent | at least one candidate is ALIVE with evidence |
| 2 | Research | `research-facts` | `facts_check.py facts.json` exits 0 |
| 3 | Script | `write-script`, then the `script-auditor` agent | lint gate passes, A/B verdict merged |
| 4 | Number check | `facts_check.py --script`, then the `fact-checker` agent | 0 unsourced numbers, fact-checker PASS |
| 5 | First slice | you + the creator | the creator approved the first 60-90 seconds |
| 6 | Voiceover | your TTS or the creator's voice, then `check-voiceover` | `voice_check.py` exits 0 |
| 7 | Visuals and edit | your editor or code (`morph-clips` for motion graphics) | text on screen follows the dose rule below |
| 8 | Render check | `check-render`, then the `render-auditor` agent | `render-check.json` PASS and `RENDER-AUDIT` PASS for the same file |
| 9 | Package | title, thumbnail, description into `upload.json` | thumbnail text does not repeat the title |
| 10 | Upload | the `upload-checker` agent, then `upload-private` | `UPLOAD-GATE` GO, uploaded as **private** |
| 11 | Publish | the creator, in YouTube Studio | a human pressed publish |
| 12 | Learn | `retention-by-scene` after 3+ days | one lesson written down |

Station 5 exists because the most expensive mistake is a whole video built on an opening nobody liked. Render
or mock the first 60-90 seconds and ask before you build the rest.

## Rules that hold at every station

- **Never invent a number, quote, result or source.** If a figure is missing, ask for it or write the line
  without it. Every number on screen or in the narration lives in `facts.json` with a link.
- **The maker never reviews their own work.** Reviews go to the agents above, with the files, not a summary.
- **Reviewers are for what ships.** Drafts, tests and v1 experiments skip the review agents; the final render
  and anything published do not.
- **Measured problems are fixed, not argued.** A script that exits 1 means fix the work. If a check is wrong
  for this video, the creator decides, and you write down why.
- **On-screen text is punctuation.** Full-screen text cards stay under ~20% of the runtime, never two in a row,
  and never repeat what the narration is saying. Big text is for the hook question, a chapter turn, or the one
  number to remember.
- **Nothing publishes itself.** Uploads are private. Publishing, scheduling and anything irreversible is the
  creator's click.
- **No automated engagement on your own videos.** No script or browser agent watches, likes or comments on
  your public videos. Fake engagement breaks YouTube's rules and can cost the monetization account that all of
  the creator's channels share.

## Lessons become checks

Keep a `Lessons` section in the channel's `CLAUDE.md`. After every mistake, add one line: what happened and how
it is now caught. When the same lesson shows up twice, turn it into a script check instead of a longer rule.
When a check has caught nothing for a month, ask whether it is still worth its time.

## What to report back

After each station: what was produced (path), the gate result (exit code or verdict line), and the single next
action. Do not hand over a list of twenty things.
