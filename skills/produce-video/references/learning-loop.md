# The learning loop

A channel that does not write down what it expected cannot learn from what happened: memory quietly rewrites the
expectation to fit the result. The loop is three small files in the channel folder, next to the channel's
`CLAUDE.md`. Copy them from this skill's `templates/` the first time:

```
<channel>/
  CLAUDE.md          the rules, kept short; its Lessons section points to lessons.md
  decisions.md       decisions ledger: what we decided, what we expected, what it brought
  lessons.md         numbered lessons: what happened, with numbers, and the rule
  gate-counter.md    which gate caught a real problem, and when
```

The loop: decide, write the expectation with numbers, publish, measure at T+30 and T+90 with `channel-pulse` and
`retention-by-scene`, compare, write the lesson, turn repeated lessons into checks.

Each video folder also keeps `costs.md` (template in `templates/`): every credit and fee, written when it is spent.
At T+90 its total goes next to the video's views in `decisions.md`, so a paid tool has to show what it earned.

## 1. Decisions ledger (`decisions.md`)

Open an entry the day a decision is made, before any result exists: a topic choice, a package (title and
thumbnail) or a later change to it, a format or length change, a cadence change, a step added to or dropped from
the line.

Each entry has:

- **Decision** - one sentence, with a pointer to where the detail lives.
- **Alternative not chosen** - the concrete runner-up (the other topic, the other title). It is required: without
  it, every result looks like the only possible one.
- **Expectation** - the result that would mean "it worked", with numbers (for example: at T+30, more views than
  the last three videos and retention at 30 s of at least 60%).
- **Check rows** - T+30 and T+90, with views (and the platform), average view percentage, retention at 30 s and
  subscribers gained.

Rules:

- **Append-only.** A filled row is never edited. A correction is a new row below it that says what it corrects.
- **Rows are filled only from tool output.** `channel-pulse` (`pulse-<date>.json`) and `retention-by-scene`.
  Name the file and the date in the row. Write the platform with every view count; a number without its platform
  cannot be read later. Numbers from Studio or another platform's dashboard are labelled as read by the creator,
  with the date.
- **Unmeasured stays `_open_`.** If a number is not in a tool output, the cell stays `_open_` or says "not
  measured" and why. Never estimate. A video still under the sample guard (72 hours, 100 engaged views) at the due
  date keeps its row open with that reason.
- **"Would I make the same decision with what I knew then?"** is a process question, not a result question. A
  good process can bring a bad result and a lucky result can hide a bad process. Answer yes or no with one
  sentence why.
- **Due rows are filled without asking.** The session that runs `channel-pulse` after a due date fills the due
  rows and reports them in one line. It never touches a row that is not due yet.

After about ten entries, read the ledger for which kinds of expectations held (search-led topics against
feed-led ones, one thumbnail style against another). That answer is measured, not felt.

## 2. Gate counter (`gate-counter.md`)

Every gate, a script or a review agent, costs time on every video. The counter shows which ones earn it.

- **One line per real catch:** date, gate, what it caught in one line, and the version (final render or draft).
  A false alarm is not a catch; the work must have needed a fix.
- **The period is one month** from the day the counter starts. At the end, count the catches per gate.
- **A code gate that caught nothing is loosened** (a wider threshold, a faster mode, or run less often). **An
  agent gate that caught nothing is removed** from the line. The creator makes the call; write it in the
  end-of-period table. A gate added later gets its own month.

## 3. Lessons (`lessons.md`)

Format, one paragraph per lesson:

```
NNN. **Title.** What happened, with the measured numbers. Rule: the rule.
```

- **Number:** the highest existing number plus one. Find it by searching the whole file for lines that start
  with a number and `. **`, not by reading the last line; sections are not always in order.
- **Check for duplicates first.** Search for the same failure (title words, tool names). If it is already there,
  add a dated addendum to that lesson instead of a new number.
- **Append-only.** Never renumber or rewrite a lesson. One that a newer lesson replaces gets "Replaced by NNN."
- **Lessons become checks.** A lesson that shows up a second time, or that can be checked mechanically (a count,
  a limit, a file that must exist), becomes a script check with a test. The lesson then ends with "Now checked
  by: <script and flag>."
- **Preflight at the start of each video.** Pick the lessons that apply to this format into a checklist in the
  video folder (`preflight.md`: stage | check | lesson number), at most 25 lines. Point to lessons by number; do
  not copy them.
- `CLAUDE.md` holds only the current rules. The history lives in `lessons.md`, which grows past what is worth
  loading in every session; search it, or ask an agent for the lessons that apply.

## 4. Optional: drop-off flags

Goal: learn which kinds of narration actually lose viewers on this channel, then scan the next script for them.

1. **Define measurable flags** for a 10-second window of narration. Examples: *number pile* (three or more
   numbers in 10 seconds), *citation without the viewer* (source words such as "according to" or "a report says"
   and no "you"), *dry opening* (the first 5 seconds with neither "you" nor a number). Each flag is a hypothesis.
2. **Measure on videos with at least 100 engaged views.** Take the drop windows from `retention-by-scene` (the
   steepest drops) and the narration timing (captions or the voiceover transcript). For each flag, compare how
   often it appears in drop windows with how often it appears in all 10-second windows of the same video.
3. **Keep only flags that really appear more often in drop windows,** summed across videos. Remove the rest.
   Videos under the sample guard may be listed as hints but never count. With fewer than about three qualifying
   videos, every flag stays a hypothesis.
4. **Scan the next script and the final narration for the surviving flags.** Each hit is a warning: fix it or
   keep it with one sentence why. It is never a gate.
