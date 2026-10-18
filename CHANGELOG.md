# Changelog

## 0.1.0 - 2026-10-18

First public release.

- Skills: `produce-video`, `find-topic`, `research-facts`, `write-script`, `check-voiceover`, `check-render`,
  `upload-private`, `retention-by-scene`, `morph-clips`.
- Gate scripts: `outliers.py`, `facts_check.py`, `script_lint.py`, `voice_check.py`, `render_check.py`,
  `upload.py` (dry run by default, private-only uploads), `retention.py`.
- Review agents: `fact-checker`, `topic-skeptic`, `script-auditor`, `render-auditor`, `upload-checker`,
  `privacy-scanner`, with Codex copies in `codex/agents/`.
- `examples/starter-kit`: the minimal one-skill version in English and Turkish.
