# Changelog

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
