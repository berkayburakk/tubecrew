# Channel rules

Claude Code reads this file in every session. Adapt it to your own channel.

## Accuracy
- Every number has its source recorded in `numbers.json`. A number without a link is not used.
- No fabricated evidence, quotes or sources.
- No advice: explain, do not steer.

## Production
- Every step writes its output to a file; nothing moves past a step that has not been checked.
- The model that wrote a text does not review it; an agent that did not do the work does.
- Before uploading, an agent that did not make the video watches it end to end.
- Videos are uploaded as private first; a person decides when they go public.

## Lessons
Add one line here after every mistake. Example:
- The number highlighted on screen did not match the number being spoken. The render review now checks that
  on-screen and spoken numbers match.
