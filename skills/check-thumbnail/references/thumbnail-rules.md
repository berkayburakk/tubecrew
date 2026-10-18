# Thumbnail rules

Each rule comes from a real mistake. The reason sits next to the rule so you can tell when it applies.
`thumb_check.py` measures the parts marked *(measured)*; everything else is judgement.

## Judge at feed size

- **Compare candidates only at feed size** (about 210-320 px wide), side by side, never at full size. At full
  size, candidates that look alike can be far apart in the feed, and the reverse. A detailed background (a
  ticker, a chart, a crowd) that "sets the scene" at 1280 px often turns to mud at 320 px. All that is left then
  is small text and a distant figure. *(the preview)*
- **Look on both backgrounds.** A dark thumbnail can vanish on YouTube's dark theme, and a pale one on the light
  theme. *(the preview)*
- **One subject wins.** Nine small elements at 210 px means none of them is visible. A face, an object or one
  number should dominate. *(clutter, measured as a proxy: many separate colour areas)*
- **Empty space can be measured.** When more than half the frame is near-black (luma under 25), half the
  thumbnail is empty. A plain black text column often causes it. Put a scene behind the text instead.
  *(dark area, measured)*
- Keep key text and faces out of the bottom-right corner. The video length badge sits there.

## Text

- **The longest line sets the font size, not the number of lines.** Text fits the width of its longest line, so
  one extra line costs nothing and one long line shrinks everything. To make text bigger, shorten the longest
  line. *(line length, measured)*
- **Keep the lines balanced.** When the longest line is more than about 2x the shortest, the short line ends up
  tiny. That hurts most when the short line is the coloured emphasis. *(line balance, measured)*
- **At most 3 lines, about 12 characters each** with a heavy display font. Fewer lines are simpler, but they do
  not give a bigger font. *(measured)*
- **No abbreviations a viewer must decode.** A thumbnail can win every measurement and still fail because the
  creator could not read `$6BN`. If the channel owner cannot decode it at a glance, a scrolling viewer never will.
  `K`, `M` and `B` after a number are common enough to read. `BN`, `MN`, `TN`, `YOY` and similar are not.
  *(abbreviations, measured)*
- **A number needs context.** "11 DAYS LATER" alone means nothing. A number works when the title or the
  picture tells you what it counts.
- **Draw the text with code in the channel's font**, so it is sharp and the same in every video. If an image
  model draws it, check every letter.

## Title and thumbnail are one pairing

- **They say different things.** The thumbnail adds to the title or poses its question. It never repeats it. Of
  two candidates, prefer the one whose text carries the tension (two numbers that contradict each other) over the
  one that repeats the title's number. *(title overlap, measured: shared content words)*
- **The opening delivers the thumbnail.** The first 10-15 seconds of the video show what the thumbnail promised.
  If the thumbnail shows a price tag, the price tag appears early. A matching set of title, thumbnail and opening
  beats three pieces that are each better on their own.
- **Check the chosen concept was built.** If the topic decision named a thumbnail concept, the uploaded
  thumbnail uses it, not a quicker substitute.

## Honesty

- **Never fabricate proof.** No invented headline, newspaper, screenshot, chart, quote or result, even as
  decoration. If a thumbnail needs a document, find the real one (a public-domain front page, the company's own
  filing) and crop it. A real document carries the scale and the credibility a fake one only pretends to.
- **Every number on a thumbnail is in `facts.json` and agrees in two sources.** For prices, use the official
  series from the exchange where the trade happened. Data aggregators sometimes disagree on a single day.
  Thumbnails go out before anyone reads the description, so a wrong number there spreads fastest.
- Mark a draft built on an unconfirmed number with a visible "DRAFT" until the number is checked.

## Scores from tools

- **Third-party thumbnail scores and critiques are a data point, not an authority.** Take the measurements,
  not the verdict. A tool may say "low contrast" when your contrast measures higher than its own example. The
  real difference may be something else, such as saturation or warmth. Its advice ("add a NEW badge") may also
  not fit the channel at all.
- **A low score does not mean the scorer is broken.** First check that it scored the real image. Right after a
  thumbnail is set, YouTube's image servers can return a blank or old frame for a while. Then look for a second
  independent scorer before you decide.

## Testing after publishing

- **Never A/B the title and the thumbnail at the same time.** If a combination wins, you cannot tell which part
  won. A small channel also lacks the impressions to split traffic three ways. Test the thumbnail first with
  the title fixed, keep the winner, then test the title.
- **Change one thing per arm.** For example, the same text on a different picture, then the same picture with
  different text.
- **Do not start a test on a brand-new video, or on one with too few impressions.** A test is decided by the
  impressions it gets while it runs, not by the video's lifetime total. Before you start, read the last 7 days of
  daily impressions. Without hundreds a day, run two arms for longer, or do not test.
- **Check which version is live before you set up a test.** Look in Studio or the API, not in your notes. Notes
  about "the current thumbnail" can be wrong for weeks.
- **Leave the arms of an open test alone.** A cleanup that re-uploads, retitles or deletes videos can quietly
  destroy a running test. Before you delay other work to protect a test, check that the test still exists.

## API limits

- **`thumbnails.set` can refuse after a few changes a day on the same video.** It answers 403 or 429 even with
  a token that worked minutes earlier. That is a daily limit, not a permission problem. Set the thumbnail in
  YouTube Studio instead.
- **Custom thumbnails need a verified channel.** A first 403 on a new channel usually means phone verification
  is missing.
