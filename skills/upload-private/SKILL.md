---
name: upload-private
description: Upload a finished video to YouTube as PRIVATE through a script that refuses to run until the render check, the independent reviews and the metadata limits all pass for the exact file - with the creator's own OAuth client, never a shared key. Use for "upload this video", "prepare the YouTube upload", "check my title and description", or setting up YouTube API access.
---

# Upload privately

The upload script is a gate, not a convenience. By default it is a dry run that uploads nothing. With
`--upload` it uploads as **private** only; publishing, scheduling and visibility are the creator's decision in
YouTube Studio.

`$SKILL` below is this skill's folder.

## One-time setup

Each creator uses their own Google Cloud OAuth client; nothing is shared and nothing is stored in the project.
Follow `$SKILL/references/oauth-setup.md`, then:

```bash
pip install google-auth google-auth-oauthlib requests
python $SKILL/scripts/upload.py --login --profile mychannel
```

## 1. Write `upload.json`

```json
{
  "channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
  "title": "How Acme Lost $3 Billion in One Week",
  "description": "First two lines are the search snippet...\n\n0:00 Intro\n0:45 The deal\n3:10 What went wrong",
  "tags": ["acme", "acme corp", "business history"],
  "category_id": "27",
  "default_language": "en",
  "made_for_kids": false,
  "synthetic_media": false,
  "thumbnail": "thumbnail.jpg"
}
```

- `synthetic_media` is YouTube's altered or synthetic content disclosure. Set it to `true` when the video shows
  realistic AI-generated or altered footage of real people, places or events. Animation, AI script help and a
  creator's own voice clone usually do not need it. The script makes you answer; there is no default.
- The title and the thumbnail are one pairing: the thumbnail text must not repeat the title. Look at the
  thumbnail at feed size (about 320 px wide) before deciding.
- The first two lines of the description are what people see; put the search words there.

## 2. Dry run

```bash
python $SKILL/scripts/upload.py <video-project>
```

It checks title length, description bytes, tag weight, chapter rules, the thumbnail, a duplicate upload, and that
`qa/render-check.json`, `qa/RENDER-AUDIT-*.md` and `qa/UPLOAD-GATE-*.md` all approve **this exact file**.

## 3. Independent go/no-go

Give the `upload-checker` agent the project folder. It writes `qa/UPLOAD-GATE-<date>.md` (GO or NO-GO) bound to
the video and `upload.json` hashes. Any later change to either means a new review.

## 4. Upload

```bash
python $SKILL/scripts/upload.py <video-project> --upload --profile mychannel
```

It confirms the token belongs to `channel_id`, uploads resumably, sets the thumbnail, logs the upload in
`qa/upload-log.json` and prints the Studio link. Then tell the creator what is left for them in Studio: end
screen, cards, audio tracks, publish or schedule.
