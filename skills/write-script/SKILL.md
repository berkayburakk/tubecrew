---
name: write-script
description: Write a spoken YouTube script that sounds like a person - an opening built for the first 30 seconds, numbers only from facts.json, a humanizing rewrite against 26 AI-writing patterns, a mechanical lint, then an independent A/B review. Use for "write the script", "write my hook", "make this sound less like AI", "rewrite this narration", or before any voiceover.
---

# Write the script

Voice is the most expensive thing to redo, so the script is finished before any audio exists.

`$SKILL` below is this skill's folder.

## 1. Draft

- Write `script.md` in numbered paragraphs (`P01`, `P02`, ...). Numbers come only from `facts.json`; if a
  number you want is not there, go back to research instead of typing it.
- Write for the ear: one idea per sentence, nothing over 30 words, signposts the listener needs ("three numbers
  will tell you...") because they cannot see the page.
- Mark what is on screen next to each beat (`[ON SCREEN: ...]`). A narration with nothing to look at is a podcast.

## 2. The opening (most viewers decide in the first 8-25 seconds)

- The first 5 seconds say what the title and thumbnail promised. Someone watching muted should still know.
- Show the promised thing (a real result, page, demo or number) by about 25 seconds. A text card does not count.
- If the video targets a search phrase, say it within the first 30 seconds.
- Open a question the viewer cannot close yet, and answer it explicitly near the end.
- No channel intro, no "before we start", no subscribe pitch at the top.
- End by naming the next video to watch, by its title.

## 3. Humanize (the writer does this)

Copy the draft to `script-v1.md`, then rewrite `script.md` against the 26 patterns in
`$SKILL/references/ai-patterns.md`, strongest first. Keep paragraph ids, every number, every attribution
("the company says", "reportedly", "about"), and the spoken-script exceptions listed there. Add a change table
at the bottom: paragraph, what changed, which pattern. A rewrite alone is not safe; it tends to drop hedges and
signposts, which is why step 5 exists.

## 4. Lint (mechanical)

```bash
python $SKILL/scripts/script_lint.py script.md --phrase "<search phrase>" --gate
```

It fails on sentences over 30 words, AI-vocabulary hits and a search phrase that arrives after 30 seconds. The
pattern counts it prints are guidance for the reviewer, not a verdict.

## 5. Independent A/B review

Give the `script-auditor` agent both files (`script-v1.md` as A, `script.md` as B) and `facts.json`. It never
wrote either version. It returns A, B or "B with fix" per paragraph and a must-fix list. Merge its verdicts into
`script.md`, lint again.

## 6. Numbers, then voice

Run `facts_check.py facts.json --script script.md` (research-facts skill), then the `fact-checker` agent. Only
after both pass does anyone record or generate the voiceover.

## Report back

The opening as it will be spoken, the lint result line, the review verdict, and what is still open.
