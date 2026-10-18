# 26 AI-writing patterns for spoken scripts

Adapted from [blader/humanizer](https://github.com/blader/humanizer) (MIT) and Wikipedia's
"Signs of AI writing" guidance, rewritten for narration. Strong patterns are worth fixing on their own; weak
ones only when they pile up. In other languages, apply the patterns by meaning, not by the English words.

## A. Performance (strongest)
1. "Not X, but Y" contrasts that add no claim.
2. One-line punch endings and runs of sentence fragments.
3. Deep-sounding lines that say nothing ("All of this costs money before it makes money").
4. Setup sentences ("Here's the twist", "Now the threat", "Let's dive in").
5. Answers to objections nobody raised.

## B. Rule-bound rhythm
6. Forced lists of three.
7. Consecutive sentences with the same opening ("That's... That's...", "The company says... The company says...").
8. Dashes (weak).
9. Stacked hedges (weak).
10. Piles of hyphenated adjectives (weak).
11. Passive voice that loses who did it (weak).

## C. Inflation and borrowed authority
12. AI vocabulary ("delve", "pivotal", "landscape", "testament", "underscore").
13. Presenting an ordinary fact as a turning point.
14. Saying things are "connected" without saying how.
15. A shallow "-ing" clause tacked on the end ("..., highlighting the importance of...").
16. Sales language.
17. Unnamed experts or prestige lists.
18. "Serves as" / "boasts" instead of "is" / "has".

## D. Formatting (written text only)
19. Decorative bold.
20. Decorative headings.
21. Curly quotes (weak).

## E. Chat residue (delete outright)
22. Greetings, praise, offers ("Great question", "I hope this helps").
23. Knowledge-cutoff notes, or gaps filled with guesses.
24. A first sentence that repeats the title.
25. Sentences that describe the text itself ("In this video we will explore...").

## F. Wrong reader
26. Re-explaining what the viewer already knows, or saying the same number twice.

## Spoken-script exceptions (keep these, shorten if needed)

- Chapter turns and short roadmaps ("Let's follow the money: ..."). A listener cannot see the page.
- Open loops ("... which comes back at the end"), as long as the end really comes back to it.
- Numbered lists tied to on-screen cards ("three numbers will tell you...").
- Hook rules: the first 5 seconds state the subject; a target search phrase lands within 30 seconds; the hook's
  question is answered in one sentence near the end; the last line names the next video.
- Natural speech: "Well," openers and short callbacks ("Remember those paper gains?") are how people talk.

## Never changed by a rewrite

- Placeholders (`{key}`), cue anchors (`[[cue]]`) and verbatim quotes.
- Attribution and hedges: "the company says", "according to", "about", "more than", "up to", "reportedly",
  "estimated", "run rate".
- The `do_not_use` list in `facts.json`.
- No new claims. If shortening creates a derived claim ("almost all of the profit"), flag it in the change
  table so the reviewer and fact-checker can recompute it.

## Failure modes seen in practice

| Failure | Example | Fix |
|---|---|---|
| Over-generalizing | "the clicks haven't gone away" when only paid clicks were measured | "paid clicks haven't gone away" |
| Lost subject | "It says" where the source was the company | "The company says" |
| Meaning drift | "has changed how it makes money" contradicting "mostly the way it always has" later | "is changing" |
| Wrong association | "borrowed another" making a share sale sound like debt | "also borrowed" |
| Long sentences | merged fragments of 32-39 words | split; at most one long sentence per paragraph |
| Deleted signposts | roadmap and "three numbers" list removed | spoken-script exception, put them back |
| Broken visual triggers | words that drive on-screen animations deleted | re-anchor the trigger to a new word |
