---
name: research-facts
description: Build the sourced fact pack for a video - every number with its primary source, link, type and date in one facts.json that the script and visuals read from, plus a do-not-use list. Use at the start of research, when a new filing or event lands, or when someone asks "where does this number come from" or "check my numbers".
---

# Research facts

The script never contains a number that is not in `facts.json`. That one rule removes most of the corrections
a factual channel ever has to pin.

`$SKILL` below is this skill's folder.

## 1. Collect

For every number you might use, add an entry (start from `$SKILL/templates/facts.example.json`):

```json
"revenue_2025": {
  "value": 12.5, "unit": "billion $", "type": "primary",
  "source": "Acme Corp annual report 2025, page 41",
  "url": "https://example.com/acme-annual-report-2025",
  "accessed": "2026-10-01", "as_of": "fiscal year 2025",
  "quote": "Total revenue was $12.5 billion."
}
```

- `type` is one of `primary` (the company, regulator or official dataset said it), `news` (a report about it),
  `estimate` (an analyst or model), `market` (exchange or market data: a price, an index level, a yield),
  `own-calculation` (yours; write the formula in `calc` using other keys).
- A `market` fact needs `as_of` with the date and the session or time zone (`"2026-03-04 close, ET"`). Take a
  closing price from the exchange's own historical series, not an aggregator; they can differ on a single day.
- Prefer the primary source. A news article is a pointer to the primary source, not the end of the search.
- Keep three dates apart: when you accessed it, what period it describes (`as_of`), and when the event happened.
- Do not mix similar-sounding things: revenue, annualized revenue, profit, valuation and money raised are five
  different numbers; announced, signed and completed are three different states.
- Anything you found but must not say (unverifiable, contradicted, too old) goes into `do_not_use` with the reason,
  so nobody "rediscovers" it later.

## 2. Validate

```bash
python $SKILL/scripts/facts_check.py facts.json
```

It fails on missing links, bad dates, unknown types, `market` entries without a dated session or time zone, and
`own-calculation` entries whose formula does not recompute to the stated value. Fix until it exits 0.

## 3. Gate the script

After the script is written:

```bash
python $SKILL/scripts/facts_check.py facts.json --script script.md
```

Every number in the script must match a fact. A number may be rounded to the precision it is written with
(12.52 billion as "12.5 billion"); anything looser needs a hedge word ("about", "more than", "up to") in the
direction that keeps it true. Dates and times are reported, not checked. A `do_not_use` claim in the script fails
the gate. Exit 1 means fix the script, not the fact file.

## How numbers are said

- Without "about" or "around", a number is exact; do not shorten 176.52 to 176 silently.
- "Up to" for ceilings, "reportedly" for news, "estimated" for estimates, "percentage points" when two
  percentages are subtracted.
- Superlatives ("the biggest", "the first", "the only") need their own source.
- Hook and thumbnail numbers need two independent sources that agree.
- Nothing that was not reported is drawn as fact (someone's cost basis, an implied share, a made-up chart).
- Rounded parts add up to the total shown with them; otherwise show the residual as its own "≈" term. A label
  ("non-cash") belongs only to the item it describes.
- No advice phrasing (buy, sell, you should invest). Explain; do not recommend.

## Hand-off

The `fact-checker` agent audits the final script against primary sources before any voiceover is made. Give it
the script path and `facts.json`; the pack is a lead for it, not proof.

When the creator's stack (`~/.tubecrew/stack.json`) has `verifier: codex`, also run the same audit through Codex,
a different model family, with the read-only command in the setup skill's `references/tools.md`. A claim either
checker marks WRONG gets fixed; a disagreement goes to the creator.
