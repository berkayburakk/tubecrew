# Shorts rules

Platform facts are checked against YouTube's own help pages (links below; checked October 2026). Pages change:
when a rule here and the help page disagree, the help page wins, and the constant in the script should follow.
Everything else is craft advice and says so.

## What YouTube counts as a Short

| Fact | Source |
|---|---|
| A square or vertical upload of up to **three minutes** is a Short (uploads since 15 October 2024; earlier uploads keep their old category). A landscape video is a regular video at any length. | [Understand three-minute YouTube Shorts](https://support.google.com/youtube/answer/15424877) |
| A Short longer than one minute with an active **Content ID claim** is blocked, whatever the claim's policy, until the claim is resolved. | same page |
| Songs from the Shorts audio library: most can be used for up to 90 seconds in a three-minute Short; some are limited to 60 or 30 seconds. | same page |
| Custom Shorts **thumbnails** are documented only in YouTube Studio on a computer, for verified accounts, ideally 9:16. The upload API's `thumbnails.set` page does not mention Shorts, so `upload.py` sends none for a Short. | [Add video thumbnails](https://support.google.com/youtube/answer/72431), [thumbnails.set](https://developers.google.com/youtube/v3/docs/thumbnails/set) |
| The **related video** link from a Short to another video is added in YouTube Studio (it needs advanced feature access; the target must be your own public or unlisted video). The help page describes no API for it. | [Add a related video to your Shorts](https://support.google.com/youtube/answer/14075157) |
| **Reused content**: repurposing content from YouTube or elsewhere without significant original commentary, substantive changes or educational or entertainment value is not monetizable, even with the owner's permission; violations can affect the whole channel. **Inauthentic content** (renamed from "repetitious" on 15 July 2025): mass-produced or templated videos with minimal variation. | [YouTube channel monetization policies](https://support.google.com/youtube/answer/1311392) |
| Shorts revenue sharing excludes views of non-original Shorts: unedited clips of others' films or shows, re-uploads of other creators' content, compilations with nothing original added. | [YouTube Shorts monetization policies](https://support.google.com/youtube/answer/12504220) |

There is no "Short" switch in the upload API: shape and length decide. `"short": true` in `upload.json` only makes
`upload.py` check them.

## A Short from a long video

- **Start from a finished long video**, after its script, voiceover and render passed their gates. The Short reuses
  checked words and numbers; it does not create new claims.
- **Not a trailer.** A Short that promises and points elsewhere ("the answer is in the full video") gives a stranger
  nothing and teaches the feed that your Shorts disappoint. The Short pays off on its own; the long video is for
  the viewer who wants more.
- **The pocket rule.** A stranger understands the first line in the first two seconds, with no context. Back-
  references ("And", "So", "This is why", "As we saw", "It") fail it; so does any intro. If no sentence in the
  script works cold, write one and record it, rather than cutting mid-thought.
- **One idea.** Setup, turn, payoff. A window that crosses a chapter heading usually holds two ideas.
- **Hook in the first one or two seconds.** Lead with the result or the stake, then how it happened: a number, a
  named thing, a question the Short answers. Not a date ("In 1987 ..."), not "hi", not "subscribe".
- **Loop ending.** A short last line (about a dozen words) that lands on something concrete and leads back into the
  first line makes a rewatch feel natural. A cut on a question whose answer lies outside the Short is an open
  loop, not a payoff.
- **Length.** Shorter loops are easier to finish; 25-45 s is the middle band `shorts_pick.py` prefers, as a
  starting point. Calibrate it on your own retention data instead of copying anyone's number.
- **Picture.** The opening must move or show text. A still frame with no text in the first two seconds is the
  cheapest swipe. Use `fit` for charts and slides (nothing cut off), `crop` when one face or object carries the shot.

## Text and safe zones

YouTube draws the title, description, channel name and audio line over roughly the **bottom quarter** of a Short and
the like, comment, share and remix buttons down the **right edge** (about 15%). These are working margins, not
an official specification: the overlay changes by app version and device, so look at the result on a phone.

- Keep burned captions and any text in the middle band. `shorts_cut.py --burn` places captions at 30% above the
  bottom with side margins that clear the right-hand buttons.
- A plain SRT burned with default settings lands at the bottom, inside the zone. `shorts_check.py --burned` fails it.
- Captions built from a transcript inherit its mistakes (names, numbers, homophones). Read them before burning.
- On-screen text does not repeat the title, and never says "subscribe" or "link in bio".

## Originality

- Cut from your own long video and transform it: new framing, captions, a hook line written for the cold viewer.
- Do not re-upload other creators' clips, film or TV scenes, or compilations. Footage you did not make needs your
  own commentary or a real change in meaning, and a license where one is required. Permission alone does not make
  reused content eligible for monetization.
- Avoid templated mass production: many Shorts with the same structure and minimal variation read as inauthentic.
  Make fewer, each with its own idea.
- Music: a licence for the long video may not cover a Short. Over one minute, an active Content ID claim blocks the
  Short, so use music you own or have cleared, or keep the claimed part out.
- AI-generated realistic people, places or events still need the altered or synthetic content disclosure
  (`synthetic_media` in `upload.json`).

## Title and description

- The title stands on its own for a stranger: no "Part 2", no long video's title pasted in, no clickbait the Short
  does not pay off. Same limits as any upload (100 characters; about 60 show on a phone).
- The description's first line repeats the main keyword in plain words. Leave out chapter timestamps: they belong to
  the long video.
- Hashtags are optional; they do not make a video a Short.
- Link the long video with the **related video** in Studio after the upload; it is the link viewers see on the Short.

## Cadence

- **Never dump several Shorts on one day.** Spread them, for example one a day or every other day, so each gets its
  own test in the feed and a short stock lasts until the next long video.
- **A Short cut from a long video goes public only after that long video** is public, or is scheduled before the
  Short. A Short is often the long video's strongest moment; released first, it spends the story and leaves the
  long video's first audience with nothing new. Uploading it privately early is fine.
- **Testing two versions** (two framings, two hooks): rotate them across days and times of day, so the time slot
  does not pass for the effect, and judge only with enough views.
- Scheduling stays the creator's decision: `publish_at` plus `--schedule` in `upload-private`.
- Reading a weak Short: if YouTube barely showed it in the Shorts feed, wait or improve the next one; if it was shown
  but viewers swiped, the first two seconds are the problem. Look at the impressions in Studio before touching the
  hook.
- A re-upload is a new video: views, comments, the Studio thumbnail and the related-video link do not carry over.
  Fix before uploading.

## The review

The `render-auditor` reviews the final Short with its usual contract (`VERDICT:` and `Video:` lines, saved as
`qa/RENDER-AUDIT-<date>.md` in the Short's own folder). Give it the Short, `qa/shorts-check-<name>.json`, the
captions and the window's text, and say what to judge: the first two seconds muted and with sound, the payoff and
the loop, text inside the UI zones, captions against the words, and no end screen expected. The `upload-checker`
then reviews `upload.json` (with `"short": true`) and writes `UPLOAD-GATE`. `upload.py` binds both to the Short's
bytes.
