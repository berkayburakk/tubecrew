# Reading transcripts

`scripts/transcripts.py` fetches the captions of competitor videos with yt-dlp, for free and without an API key,
and writes one clean text file per video. Use it for two jobs: checking an evidence video's opening, and a sector
learning loop every couple of weeks. Reading a transcript is faster than watching, but it shows only what is said.

```bash
python transcripts.py <video URL or id> --first 60                 # the opening of one evidence video
python transcripts.py @channelA @channelB --last 5                 # the newest long videos of some channels
python transcripts.py --from-json catalog.json --min-multiple 2    # only the outliers of an outliers.py catalog
python transcripts.py <id> --lang es --lang en --refresh --json    # other languages, fetch again, machine output
```

## What it writes

The output folder (`--out`, default `transcripts/`) holds:

- `<id>_<title-slug>.<lang>.txt`: three `#` lines (title, URL, then channel, language, kind and date) and the text
  in paragraphs, each starting with an `[mm:ss]` stamp about every 30 seconds. Automatic captions repeat every line
  while the next one builds up; the repeats are removed.
- `vtt/<id>.<track>.vtt`: the caption file as downloaded, so `--first` can give exact line times later without
  the network.
- `manifest.json`: video id -> id, title, channel, url, language, kind (`manual` or `auto`), choice (`requested`,
  `original` or `first available`), fetched date, file, vtt. A re-run skips every video in it whose file is still
  there; `--refresh` fetches again.

**Language:** the `--lang` languages first (default `en`), then the video's original language, then the first track
available. A manual track beats an automatic one in the same language. YouTube also offers machine translations of
the automatic track; they are never taken as a requested language, because a translated transcript loses the
original wording. The `choice` field says which rule picked the track.

**Exit codes:** 0 at least one transcript was written or already present, 1 none could be fetched, 2 usage or
environment error (yt-dlp missing, bad catalog, no channel could be listed). `--max` (default 20) caps new videos
per run; the rest are listed as deferred and come next run.

## The first 60 seconds of an evidence video

For every evidence video at 2x or more of its channel's median, run `--first 60` and write three lines:

1. **First sentence:** what the viewer hears first. Is it a question, a claim, a number, a scene?
2. **First number:** the script prints the first line that holds a number (digits or an English number word). Note
   its second. A first number at 0:05 and one at 0:40 are different openings.
3. **First cut:** a transcript cannot show it. Open the video at that second and watch the first 60 seconds once.

Caption times are approximate (automatic captions follow the speech; manual ones are as exact as their author).
Check a second in the video before you quote it. Copy the mechanics (structure and timing), never the words.

## The sector learning loop

Every couple of weeks, read what worked in your niche:

1. Refresh the catalog with `outliers.py ... --save catalog.json`, then fetch the new outliers:
   `transcripts.py --from-json catalog.json --min-multiple 2`. The manifest skips what you read last time.
2. Read each transcript for **lessons that change what you do**: a rule, a threshold, an opening pattern, a change
   of plan. Motivation, filler and product plugs are not lessons. "No lesson" is a valid result for a video.
3. **Never turn one video's claim into a rule.** A creator saying "the algorithm now favours X" is a lead. A claim
   about policy, monetization or moderation needs YouTube's own documentation (quote the definition) or it stays
   labelled "unverified". A pattern needs at least two independent channels, the same bar as a double-sourced
   topic.
4. Write the lessons you keep into the channel's `lessons.md`, dated, with the video ids they came from. Keep the
   raw transcripts out of your notes and out of version control: they are working material, the lesson is what
   lasts.

## Limits

- Some videos have no captions (turned off, members-only, age-restricted, very new). They fail with a reason and
  are tried again next run.
- If YouTube answers "Sign in to confirm you're not a bot", your network is rate-limited or flagged. Wait, or try
  another network. yt-dlp reads its own config file, so options such as cookies go there, not into this script.
- Automatic captions have no reliable punctuation and mishear names and numbers. Open the video before you quote
  a number from a transcript.
- Transcripts are for research. Never republish the words.
