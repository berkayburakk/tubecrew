# Decisions ledger

What we decided, what we expected, and what it brought. Lessons record what went wrong; this ledger records
whether our choices worked. How to use it: the `produce-video` skill, `references/learning-loop.md`.

## Rules

- **Append-only.** A filled row is never edited; a correction is a new row below it that says what it corrects.
- **Check rows are filled only from tool output** (`channel-pulse`: `pulse-<date>.json`; `retention-by-scene`).
  Name the file and the date. Write the platform with every view count. Studio-only numbers (CTR, impressions)
  are labelled "Studio, read by the creator on <date>".
- **Not measured stays `_open_`.** Never estimate. A video under 72 hours or 100 engaged views at the due date
  keeps its row open with that reason.
- **The alternative not chosen is required.** It is the real comparison, not "doing nothing".
- **"Same decision with what I knew then?"** judges the process, not the result: yes or no, with one sentence why.
- Due rows are filled by whoever runs `channel-pulse` after the due date, without asking. Rows not yet due stay
  untouched.

## Entry template

```
## D-YYYY-MM-DD - <decision title>

- **Decision:** <one sentence> (details: <file, link or commit>)
- **Alternative not chosen:** <the concrete runner-up>
- **Expectation:** <the result that would mean it worked, with numbers>

| Check | Due | Views (platform) | Avg view % | Retention at 30 s | Subscribers | Source (file, date) | Notes | Same decision with what I knew then? |
|---|---|---|---|---|---|---|---|---|
| T+30 | YYYY-MM-DD | _open_ | | | | | | |
| T+90 | YYYY-MM-DD | _open_ | | | | | | |
```

---

<!-- New entries go below this line, oldest first. -->
