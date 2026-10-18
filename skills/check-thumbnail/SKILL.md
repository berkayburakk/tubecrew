---
name: check-thumbnail
description: Check thumbnail candidates before an upload - a code gate for format, 16:9, size, empty dark area, clutter, text rules and title repetition, plus a feed-size preview to choose from. Use at the Package station (before upload-checker), for "check my thumbnail", "which thumbnail is better", or when setting the thumbnail in upload.json.
---

# Check the thumbnail

The title and the thumbnail are one pairing: the viewer sees them together, so they say different things and
together make one promise that the opening of the video keeps. Thumbnails are judged at feed size, never full
size. Code measures what it can; you and the creator judge the rest from the preview.

`$SKILL` below is this skill's folder. Rules and their reasons: `$SKILL/references/thumbnail-rules.md`.

## 1. Make 2-3 real candidates

Real alternatives: a different picture or idea, not one frame with three wordings. Keep the text short (up to 3
lines, about 12 characters each) and draw it with code in the channel's font. If an image model drew the text,
check every letter. Every number on a thumbnail comes from `facts.json`, and nothing on it is made up: no fake
screenshot, headline, chart or quote.

## 2. Run the check

```bash
python $SKILL/scripts/thumb_check.py thumbs/a.jpg thumbs/b.jpg thumbs/c.jpg --project <video> \
    --text "SOLD AT|THE TOP"
```

Lines are split by `|`. Give one `--text` for all images or one per image, in order. The title comes from
`upload.json` (or `--title`). It prints the candidates side by side and writes `qa/thumbnail-check.json` and
`qa/thumbnail-preview.png`.

| Check | Fails when | Warns when |
|---|---|---|
| Format | not JPG/PNG, or the bytes do not match the extension | the PNG has transparency |
| Shape and size | over 5% off 16:9, under 640 px wide, over the upload limit | under 1280x720, slightly off 16:9 |
| Dark area | - | over 50% of the frame near-black at feed size (`--dark-max`) |
| Clutter | - | more than 14 colour clusters at feed size (`--clutter-max`) |
| Text | - | over 3 lines, a line over 12 characters (`--max-line-chars`), longest line over 2x the shortest, abbreviations like `$6BN` |
| Title overlap | - | the thumbnail text shares content words with the title |

Exit 1 means a candidate failed: fix or drop it. Warnings do not block. Each one is a question for step 3.

## 3. Judge at feed size

Open `qa/thumbnail-preview.png`. Every candidate appears at about 210, 320 and 630 px wide, first on YouTube's
dark background and then on the light one. Judge the 210 and 320 px columns:

- Can you read the text in one glance, and does it add to the title instead of repeating it?
- Is there one subject that wins, or several small things competing?
- Does a background that looked rich at full size turn into mud?
- Does the opening of the video show what this thumbnail promises in its first seconds?

Show the creator the preview and your pick with one line of reasons. The creator chooses.

## 4. Record the choice

Save the chosen file as the project's thumbnail and fill `upload.json`:

```json
"thumbnail": "thumbnail.jpg",
"thumbnail_text": ["SOLD AT", "THE TOP"]
```

Then run the check once more on the final pair:

```bash
python $SKILL/scripts/thumb_check.py --project <video>
```

`qa/thumbnail-check.json` now carries `verdict`, `thumbnail` and its `sha256`. Next comes the `upload-checker`
agent. A thumbnail changed after its `UPLOAD-GATE` review needs a new review, because the upload binds to its
hash.

After publishing, test the thumbnail first, then the title, never both at once. Read the testing rules in
`references/thumbnail-rules.md` before starting a test.

## Report back

The candidates with their verdicts and warnings in one table, the preview path, your pick and why in one line,
and the next step: "creator picks", or "run upload-checker".
