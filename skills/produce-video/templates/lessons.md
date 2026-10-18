# Lessons

Every mistake (and every surprise that worked) once, with numbers, and the rule it left behind.
How to use it: the `produce-video` skill, `references/learning-loop.md`.

## Rules

- **Format:** `NNN. **Title.** What happened, with the measured numbers. Rule: the rule.`
- **Next number** = the highest number in the whole file plus one (search for lines starting with a number and
  `. **`; sections are not always in order).
- **Search for the same failure before adding.** If it is already here, add a dated addendum to that lesson.
- **Append-only.** Never renumber or rewrite; a replaced lesson gets "Replaced by NNN."
- **Seen twice, or checkable by a script?** Turn it into a script check with a test, then end the lesson with
  "Now checked by: <script and flag>."
- **Preflight:** at the start of each video, copy the numbers of the lessons that apply into
  `<video>/preflight.md` (stage | check | lesson number), at most 25 lines.

## Example (made up; delete when you add your first lesson)

1. **Chart number did not match the narration.** The narration said 42% while the chart on screen showed 40%,
   because the chart was typed from an older draft of `facts.json`; the render auditor caught it on the final
   render, which cost a second render. Rule: charts take their numbers from `facts.json`, never typed by hand.

## Lessons

<!-- Group by video or by date: "## <video> (YYYY-MM-DD)". Append below. -->
