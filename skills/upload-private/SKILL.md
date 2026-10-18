---
name: upload-private
description: Upload a finished video to YouTube as PRIVATE through a script that refuses to run until the render check, the independent reviews and the metadata limits all pass for the exact file - with the creator's own OAuth client, never a shared key. Translated titles and an opt-in publish time are supported. Use for "upload this video", "prepare the YouTube upload", "check my title and description", "is my YouTube login still valid", or setting up YouTube API access.
---

# Upload privately

The upload script is a gate, not a convenience. By default it is a dry run that uploads nothing. With
`--upload` it uploads as **private** only. Publishing stays the creator's decision: the only way a video goes
public by itself is a `publish_at` time the creator wrote in `upload.json` **and** the `--schedule` flag.

`$SKILL` below is this skill's folder.

## One-time setup

Each creator uses their own Google Cloud OAuth client; nothing is shared and nothing is stored in the project.
Follow `$SKILL/references/oauth-setup.md`, then log in with the project folder, so a login for the wrong channel is
refused before it is saved:

```bash
pip install google-auth google-auth-oauthlib requests
python $SKILL/scripts/upload.py <video-project> --login --profile mychannel
```

## 1. Write `upload.json`

```json
{
  "channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
  "title": "How Acme Lost $3 Billion in One Week",
  "description": "How Acme lost $3 billion in one week, step by step...\n\n0:00 Intro\n0:45 The deal\n3:10 What went wrong",
  "tags": ["acme", "acme corp", "business history"],
  "category_id": "27",
  "default_language": "en",
  "made_for_kids": false,
  "synthetic_media": false,
  "thumbnail": "thumbnail.jpg"
}
```

Optional fields (`localizations`, `publish_at`, `default_audio_language`, `thumbnail_text`) and every rule are in
`$SKILL/references/upload-json.md`. Save the file as UTF-8; the script refuses any other encoding.

- `synthetic_media` is YouTube's altered or synthetic content disclosure. Set it to `true` when the video shows
  realistic AI-generated or altered footage of real people, places or events. Animation, AI script help and a
  creator's own voice clone usually do not need it. The script makes you answer; there is no default.
- The title and the thumbnail are one pairing: the thumbnail text must not repeat the title. Look at the
  thumbnail at feed size (about 320 px wide) before deciding.
- The first sentence of the description carries the title's main keyword; the first two lines are what people see.

## 2. Dry run

```bash
python $SKILL/scripts/upload.py <video-project>
```

It checks title length, description bytes, tag weight (warns above 460), chapter rules, each translation, broken
characters, the thumbnail, a duplicate upload, the login's age, and that `qa/render-check.json`,
`qa/RENDER-AUDIT-*.md` and `qa/UPLOAD-GATE-*.md` all approve **these exact files** (the video, `upload.json` and the
thumbnail, by hash).

## 3. Independent go/no-go

Give the `upload-checker` agent the project folder. It writes `qa/UPLOAD-GATE-<date>.md` (GO or NO-GO) bound to
the video, `upload.json` and thumbnail hashes. Any later change to one of them means a new review.

If the creator deliberately skips the two review agents, the upload needs `--skip-review "<reason>"` (at least 12
characters); the reason is written to the upload log. It stands in for a missing or outdated review, never for a
FAIL or NO-GO on the same files, and the render check cannot be skipped.

## 4. Upload

```bash
python $SKILL/scripts/upload.py <video-project> --upload --profile mychannel
```

It locks the project against a second run, confirms the token belongs to `channel_id`, refuses a title the
channel's latest uploads already hold (a lost answer may have left it there; `--again` overrides after a look in
Studio), uploads resumably, logs the video id in `qa/upload-log.json` first, then sets the thumbnail. A thumbnail
refusal (403/429) is only a warning; a rerun never uploads the same file twice. Then tell the creator what is
left in Studio: end screen, cards, audio tracks, publish or schedule.

## Scheduling (only when the creator asks)

Add `"publish_at": "2026-11-02T16:00:00+01:00"` (with a time zone) and run with `--upload --schedule`. The time
must be at least 15 minutes ahead, measured again right before the upload. The video stays private until then;
afterwards the script reads the status back from YouTube and prints it. Without `--schedule`, `publish_at` is
ignored with a warning. Never add `--schedule` on your own: a scheduled video goes public without anyone pressing
publish.

## Login health

```bash
python $SKILL/scripts/upload.py <video-project> --check-token --profile mychannel
```

Proves the login still works, shows its channel, age and expiry, and warns 48 hours before it ends. Logins from an
OAuth app in Testing mode end after about 7 days; `references/oauth-setup.md` explains how to avoid that.
