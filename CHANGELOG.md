# Changelog

## 0.3.1 - 2026-10-18

- New `transcripts.py` (free, yt-dlp): competitor captions as clean timestamped text, a manifest that skips videos
  already fetched, language fallback (requested, original, first available), outliers from a saved catalog, and
  `--first 60` for the evidence video's opening.
- New `transitions.py`: a review aid that marks one-frame overlay pops and hard screen jumps, with before/after
  frame pairs for the render-auditor.
- `render_check.py`: total silence over 1 s below -60 dB fails anywhere; `--music` for music-only soundtracks
  (dead air warns at 0.5 s, fails at 1 s); the freshness warning says "2 min newer" instead of "0.0 h".
- `pick_takes.py`: a take whose spectral centre is more than 15% from the narration's median is not usable
  (`--timbre-drift`, `--no-timbre`), so a cloned voice that drifts to another timbre is caught.
- `script_lint.py`: duplicate `[[cue]]` anchors fail `--gate`. `facts_check.py`: an unhedged rounded number must
  stay within 5% of the fact ("0.5%" for 0.45 now needs "about").
- `upload.py`: login hints name the project folder and never an absolute path; the same for `pulse.py` and
  `retention.py`.
- New `costs.md` template: every credit and fee per video, with the total next to the views at T+90.
- Docs: `morph-clips` detail moved to `reference/authoring.md`; policy claims need the platform's own help page
  (`fact-checker`); sections link with "but" or "therefore" (`write-script`); say the level of a delegated check
  (`produce-video`); clearer descriptions for `upload-private` and `localize`.

## 0.3.0 - 2026-10-18

New skills
- `check-thumbnail` and `thumb_check.py`: a code gate for format, 16:9, size, empty dark area, clutter, text rules
  and title repetition, plus a feed-size preview (210, 320 and 630 px) to compare candidates side by side.
- `localize` and `dub_fit.py`: meaning-first translation that keeps every number from `facts.json`, a
  native-listener audit, the voice from the stack's `dubbing` station, and a check that every dubbed paragraph fits
  the original timing (at most 1.25x speed-up, at most 4 s drift).
- `channel-pulse` and `pulse.py`: a channel check from your own Analytics (recent videos, traffic and discovery
  share, search terms, tested-and-cut or never-tested patterns, audience only above privacy thresholds) that ends in
  one diagnosis and writes `pulse-<date>.json`.

Gates
- `render_check.py`: a hook motion band that fails a slow zoom on a still image (which freezedetect misses),
  `--profile animated|live`, still pictures of 3 s or more fail in animated videos, subtitle cues checked against
  the spoken words (`--words`, systematic drift reported once), a freshness warning, and hash-bound
  `qa/RENDER-EXEMPTION-*.md` files for a check that is wrong for one video.
- New `text_dose.py` (OCR text-card gate, optional `rapidocr-onnxruntime`) and `normalize_audio.py` (two-pass
  loudnorm to -14 LUFS, video stream copied, reports a silent fallback to dynamic mode).
- `voice_check.py`: accuracy, fidelity and words per minute for every paragraph; repeat loops and swallowed
  sentences fail; Whisper runs with `condition_on_previous_text=False` so it stops inventing words at seams. Paragraph
  ids (`P01`) are labels, not words the voice must say.
- New `pick_takes.py` (best take per paragraph; numbers and names must be heard exactly) and `trim_silence.py`
  (caps long pauses without stretching speech), plus `check-voiceover/references/failure-modes.md`.
- `script_lint.py`: opening warnings (late number, long first sentence, "in this video", throat-clearing openers),
  a gate on vague attributions without a source, and `--triggers` so word-timed animations survive a rewrite.
- New `demand.py` (free, no keys): YouTube suggestions with intent flags, a reachable step in the search results
  (a recent video from a channel under 50k subscribers with 10k+ views), and Wikipedia pageviews as a multiple of a
  proven topic; two sources more than 3x apart are labelled "demand unclear".
- `outliers.py` flags likes under 0.35% of views as "possibly ad-pushed: not evidence"; `facts_check.py` adds a
  `market` type that needs a dated session or time zone.

Upload
- `upload.py`: a project lock, a same-title check against the channel's recent uploads (and a warning for empty
  records left by a dropped upload), a strict UTF-8 and U+FFFD gate, translated titles and descriptions
  (`default_language`, `localizations`), a tag-weight warning above 460, and no query strings in error messages.
- Opt-in scheduling: `publish_at` in `upload.json` is used only with `--schedule`; the video stays private with a
  publish time, and the status is read back to confirm it.
- Login health: `--check-token`, an issue and expiry record next to the token, a 48-hour warning in the dry run,
  and a wrong-channel login refused before the token is saved.
- Opt-in publish guard (`setup/scripts/publish_guard.py`): a Claude Code hook that asks the creator before upload
  and publish commands, and refuses them when nobody is there to answer.

Reviews and the learning loop
- `topic-skeptic`: saturation threshold, evidence caps, at most two rounds and always one recommendation.
  `fact-checker`: rounded parts must add up, labels apply only to their own item, closing prices from the exchange.
  `script-auditor`: once audio or a translation exists, B wins only for a strong pattern or a meaning problem.
  `render-auditor`: exemption files, an empty-frame scan, heavy scans only on the final render. `upload-checker`:
  token liveness, translation equivalence, schedule sanity.
- `find-topic`: calendar-event and search-pattern candidates, durability tags, the two-question gate and a check of
  the evidence video's first 60 seconds.
- `produce-video`: a decisions ledger with T+30 and T+90 rows, numbered lessons, a gate counter and per-video
  preflight lists (templates in `skills/produce-video/templates/`). `retention-by-scene` says "too early to judge"
  under 72 hours or 100 engaged views (`--anyway` to override).
- `setup` detects the optional OCR package and offers the publish guard.

## 0.2.0 - 2026-10-18

- New `setup` skill and `detect_stack.py`: detect the creator's tools (Remotion, HyperFrames, Wan with ComfyUI,
  CosyVoice, Codex CLI, FFmpeg, yt-dlp, faster-whisper, the GPU and its memory), ask about subscriptions,
  suggest a tool for every station, install the free pieces with permission, and save the choices in
  `~/.tubecrew/stack.json`. Secrets are never printed.
- `produce-video` and `research-facts` use the saved stack; with `verifier: codex` the fact-check is repeated by
  Codex, read-only, as a second model family.
- New tool catalog (`skills/setup/references/tools.md`) with costs and licenses, and a "Works with your stack"
  section in the README crediting the projects the line is built on.

## 0.1.2 - 2026-10-18

Fixes from a full review; every bug fix has a regression test.

- `render_check.py`: reads per-stream durations in MKV/WebM files (a short audio track no longer passes), accepts
  WebVTT timestamps without hours and UTF-16 subtitles, and fails when a subtitle file has no readable cues.
- `voice_check.py`: decodes WebVTT entities and passes the base language code (`en` for `en-US`) to faster-whisper.
- `upload.py`: `--skip-review` stands in for a missing or outdated review, never for a FAIL or NO-GO on the same
  files; the upload review now binds the thumbnail hash too; `-1` verdict files sort after unsuffixed ones;
  UTF-16, BOM and CRLF verdict files are read; the thumbnail limit follows the API (50 MB).
- `retention.py`: the Analytics start date uses Pacific time, so a video's first day is not lost.
- `facts_check.py`: `--ignore` matches whole values only; dates, times and filing names (10-K) are not reported
  as unsourced numbers; files with a byte-order mark load.
- `script_lint.py`: "No." and month abbreviations split sentences correctly; curly apostrophes in `--phrase` match.
- `outliers.py`: re-pulling a channel from a saved catalog replaces it instead of duplicating it; approximate
  ages count as lower bounds; `@handle/videos` input works; malformed catalogs exit 2.
- Reports store only the file name for files outside the project folder.
- Docs: the Codex user skill folder is `~/.agents/skills`; Codex can add the repository as a plugin marketplace;
  `claude plugin update tubecrew@tubecrew` updates the plugin; review agents use the session's model.
- `pyproject.toml` holds tool settings only (the repository is not a Python package).

## 0.1.1 - 2026-10-18

- English-only repository: translated copies of the README and the starter kit were removed, and test samples
  now use other languages.
- New README layout with a production-line figure that follows the light and dark theme.

## 0.1.0 - 2026-10-18

First public release.

- Skills: `produce-video`, `find-topic`, `research-facts`, `write-script`, `check-voiceover`, `check-render`,
  `upload-private`, `retention-by-scene`, `morph-clips`.
- Gate scripts: `outliers.py`, `facts_check.py`, `script_lint.py`, `voice_check.py`, `render_check.py`,
  `upload.py` (dry run by default, private-only uploads), `retention.py`.
- Review agents: `fact-checker`, `topic-skeptic`, `script-auditor`, `render-auditor`, `upload-checker`,
  `privacy-scanner`, with Codex copies in `codex/agents/`.
- `examples/starter-kit`: the minimal one-skill version.
