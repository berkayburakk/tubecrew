# Contributing

tubecrew is built on one idea: **thinking goes to the model, repetition goes to code**. A skill is a short guide
that tells the agent what to judge and in what order. Anything mechanical (counting, measuring, checking a
limit, comparing two files) lives in a script with an exit code. When a mistake repeats, it becomes a check in a
script, not another paragraph in a skill.

## Layout

```
skills/<skill>/SKILL.md          short guide (aim for under ~5 KB)
skills/<skill>/scripts/*.py      the mechanical part, runnable on its own
skills/<skill>/references/*.md   detail the agent reads only when needed
agents/<agent>.md                independent reviewers (the one who made it never reviews it)
tests/test_<script>.py           pytest, one file per script
```

## Script rules

- Python 3.9+, standard library only. Exceptions: `upload.py`, `retention.py` and `pulse.py` talk to the YouTube
  APIs with plain HTTPS (`pip install google-auth google-auth-oauthlib requests`, no `google-api-python-client`);
  `text_dose.py` needs `rapidocr-onnxruntime` and the voice tools use `faster-whisper` for audio input. All of them
  import these lazily and print the install hint if one is missing. Audio and video checks need `ffmpeg`/`ffprobe`
  on PATH.
- One file, `argparse`, a module docstring that says what it does, why, and how to run it.
- `--json` prints a machine-readable result. Human output is plain English, no emoji.
- Exit codes: `0` pass, `1` a gate failed (the work must be fixed), `2` usage or environment error.
- Cross-platform: `pathlib`, UTF-8 stdout (`sys.stdout.reconfigure(encoding="utf-8")` when available), no shell
  tricks, works on Windows paths with spaces.
- Never touch the network unless that is the script's job, and never upload, publish or delete by default.
- Tests import scripts by file path (the skill folders contain dashes):
  `importlib.util.spec_from_file_location(...)`. Tests that need ffmpeg skip when it is not installed.

## The video project contract

Every skill and agent assumes one folder per video:

```
<video>/
  facts.json           every number with its source            (research-facts)
  script.md            the spoken script                       (write-script)
  voice/               narration audio + its transcript        (check-voiceover)
    paragraphs/pNNN.<ext>              one take per paragraph (generated voices)
    candidates/<n>/<take>.<ext>        takes for pick_takes.py
  dub/<lang>/          translated script, voice, captions      (localize)
  render/<name>.mp4    the final render
  upload.json          title, description, tags, flags         (upload-private)
  preflight.md         lessons that apply to this video        (produce-video)
  qa/                  every gate result lives here
    SCRIPT-AB-<YYYY-MM-DD>.md          script-auditor (saved by the caller)
    voice-check.json                   voice_check.py (+ voice-check-transcript.json with word timings)
    take-pick.json                     pick_takes.py
    dub-fit-<lang>.json                dub_fit.py
    render-check.json                  render_check.py: verdict + sha256 of the video
    RENDER-EXEMPTION-<YYYY-MM-DD>.md   the creator: a render check waived for this video hash, with the reason
    text-dose.json                     text_dose.py
    normalize-audio.json               normalize_audio.py
    RENDER-AUDIT-<YYYY-MM-DD>.md       render-auditor (saved by the caller)
    thumbnail-check.json               thumb_check.py (+ thumbnail-preview.png)
    UPLOAD-GATE-<YYYY-MM-DD>.md        upload-checker
    upload-log.json                    upload.py, after a real upload
```

### Verdict files bind to bytes

A verdict is only valid for the exact file it judged. Reports start with fixed lines that scripts parse:

```
VERDICT: PASS                       (render-auditor: PASS | FAIL)
Video: render/final.mp4 sha256:<first 12 hex chars>
```

```
VERDICT: GO                         (upload-checker: GO | NO-GO)
Video: render/final.mp4 sha256:<first 12 hex chars>
Metadata: upload.json sha256:<first 12 hex chars>
Thumbnail: thumbnail.jpg sha256:<first 12 hex chars>     (only when upload.json sets a thumbnail)
```

The verdict line must be within the first 10 lines. A re-review writes a new file (`-2`, `-3`), it never edits an
old one. `upload.py` reads the newest file of each kind and refuses if the hash does not match the current file.
`render-check.json` carries `"verdict": "PASS" | "FAIL"` and `"sha256"` (full hex) of the video it measured.

### Credentials never live in the project

`upload.py`, `retention.py` and `pulse.py` keep OAuth files in `~/.tubecrew/` (override with the `TUBECREW_HOME`
environment variable):

```
~/.tubecrew/client_secret.json                 your own Google Cloud OAuth client (Desktop app)
~/.tubecrew/tokens/<profile>-upload.json       youtube.upload + youtube.readonly
~/.tubecrew/tokens/<profile>-analytics.json    yt-analytics.readonly + youtube.readonly
```

`<profile>-upload.meta.json` next to the upload token records when it was issued and when Google will expire it
(never the token itself). `--profile` (default `default`) keeps several channels apart. A browser login happens
only when the user passes `--login`; otherwise a missing or dead token is an exit-2 message that says which command
to run.

The channel folder (one level above the video folders) keeps the learning loop: `decisions.md`, `lessons.md` and
`gate-counter.md` (templates in `skills/produce-video/templates/`), plus `pulse-<YYYY-MM-DD>.json` from `pulse.py`.

## Privacy

Nothing in this repository may contain a real person's local paths, user names, e-mails, channel ids, tokens or
private notes. Examples use made-up values (`example.com`, `UCxxxxxxxxxxxxxxxxxxxxxx`).

## Pull requests

Run `pytest` and `claude plugin validate . --strict` before opening one. Keep skills short: if a skill grows,
move the mechanical part into a script or the detail into `references/`.
