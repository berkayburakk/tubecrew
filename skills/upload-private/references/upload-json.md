# `upload.json` and `qa/upload-log.json`

`upload.json` lives in the video project folder and must be saved as UTF-8 (with or without a BOM). A file in
another encoding is refused: Windows PowerShell 5.1's `Set-Content` writes the ANSI code page, which turns accented
letters into broken characters on the way to YouTube. Any text that already holds U+FFFD (the replacement
character a bad conversion leaves behind) is a FAIL too.

The reviewer binds its verdict to the hash of the whole file, so any change, including `publish_at`, needs a new
`upload-checker` review.

## Fields

| Field | Required | YouTube field | Rule |
|---|---|---|---|
| `channel_id` | for `--upload` | - | `UC` + 22 characters; the token's channel must match it |
| `title` | yes | `snippet.title` | at most 100 characters, no `<` or `>`; a warning above 60 (cut on mobile) |
| `description` | no | `snippet.description` | at most 5000 UTF-8 bytes, no `<` or `>`; chapter lines (`0:00 Intro`) follow YouTube's chapter rules |
| `tags` | no | `snippet.tags` | non-empty strings; weight = sum of lengths (+2 for a tag with a space) + one per comma, at most 500, warning above 460 |
| `category_id` | no (`"22"`) | `snippet.categoryId` | a number as text, for example `"27"` |
| `default_language` | no, but needed for `localizations` | `snippet.defaultLanguage` | language code of the title and description (`en`, `pt-BR`) |
| `default_audio_language` | no | `snippet.defaultAudioLanguage` | language spoken in the video |
| `made_for_kids` | yes | `status.selfDeclaredMadeForKids` | `true` or `false`, no default |
| `synthetic_media` | yes | `status.containsSyntheticMedia` | `true` or `false`, no default |
| `thumbnail` | no | `thumbnails.set` | path inside the project; `.jpg`, `.jpeg` or `.png`, at most 50 MB |
| `thumbnail_text` | no | - | list of the thumbnail's text lines, for the reviewers (it must not repeat the title) |
| `localizations` | no | `localizations` | see below |
| `publish_at` | no | `status.publishAt` | used only with `--schedule`; see below |

Any other field is ignored with a warning. There is no privacy field: the upload is always private.

## Translations: `localizations`

```json
"default_language": "en",
"localizations": {
  "de": {"title": "Wie Acme in einer Woche 3 Milliarden verlor", "description": "..."},
  "es": {"title": "Cómo Acme perdió 3.000 millones en una semana", "description": "..."}
}
```

- Each key is a language code; each value has exactly `title` and `description`, under the same limits as the main
  title and description.
- `default_language` is required: YouTube refuses translations when it does not know the language of the main
  text (`defaultLanguageNotSet`). A key equal to the default language is an error; the main text already covers it.
- The script sends them with `part=snippet,status,localizations`; a part left out of that list is dropped silently.
- Code checks the limits; the `upload-checker` agent checks that every translation means the same as the source.

## Scheduling: `publish_at` and `--schedule`

```json
"publish_at": "2026-11-02T16:00:00+01:00"
```

- ISO 8601 with a time zone (`+01:00`, `-05:00` or `Z`); a time without one is refused rather than guessed.
- Used only when the command also has `--schedule`. Without it the field is a warning and nothing is scheduled.
- At least 15 minutes ahead: checked in the dry run, again right before the upload starts, and again right before
  the last chunk is sent. YouTube publishes a past `publishAt` at once, and a published video cannot be scheduled
  again, so the upload stops instead of risking that.
- The video is uploaded as private with `status.publishAt`. Afterwards the script reads the status back (a few
  times, because a read right after the write can be stale) and prints `privacyStatus` and `publishAt`. Anything
  but private is an ALARM; a schedule it cannot confirm exits 1 and tells you to check Studio.

## `qa/upload-log.json` (written by the script; do not edit)

```json
{"uploads": [
  {"video_id": "abcdefghijk", "video": "render/final.mp4", "sha256": "<64 hex>",
   "uploaded_at": "2026-11-01T09:00:00+00:00", "privacy": "private", "channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
   "title": "...", "thumbnail": "set", "thumbnail_http": 200,
   "publish_at": "2026-11-02T15:00:00Z", "schedule_confirmed": true}
]}
```

- The entry is written as soon as YouTube returns the video id, before the thumbnail or the status check, so no
  later failure can lead to a second upload of the same file.
- `thumbnail`: `none` (no thumbnail set), `pending` (the run stopped before that step), `set` or `failed`.
  `thumbnail_http` is the HTTP status of `thumbnails.set`.
- `publish_at` and `schedule_confirmed` appear only for a `--schedule` upload. `alarm` appears when YouTube reported
  anything but private. `skip_review_reason` appears when `--skip-review` was used.
- A rerun with the same video bytes is refused and names whatever the first run left undone.
