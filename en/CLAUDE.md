# Channel rules

Claude Code reads this file in every session. Adapt it to your own channel.

## Accuracy
- Every number has its source written in `numbers.json`. A number without a link is not used.
- No made-up evidence, quotes or sources.
- No advice: explain, don't steer.

## Production
- Every step writes its output to a file; nothing moves on past a step that hasn't been checked.
- The model that wrote a text doesn't review it; an agent that didn't do the work does.
- Before uploading, an agent that didn't make the video watches it end to end.
- Videos are uploaded as private first; a human decides when to make them public.

## Lessons
Add one line here after every mistake. Example:
- The number highlighted on screen didn't match the number being said. The render review checks number-to-speech matches.
