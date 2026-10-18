# tubecrew

**A YouTube production crew for your coding agent.** tubecrew turns Claude Code (or Codex) into a production
line for videos: topics chosen from evidence, every number sourced, scripts that sound like a person, voiceover
and render checked in code, independent reviewers, private uploads, and a retention post-mortem that names the
scene where viewers left.

*Türkçe: [README.tr.md](README.tr.md)*

## How it works

A video moves through stations. Each station writes a file, and the next one starts only after a gate passes.

```
topic ──> research ──> script ──> numbers ──> first 60-90 s ──> voice ──> edit ──> render ──> package ──> upload ──> learn
  │          │           │          │              │              │                  │                     │          │
skeptic  facts_check  lint +    facts_check +    creator OK    voice_check      render_check +       upload-checker  retention
 agent                A/B agent  fact-checker                                    render-auditor       + upload.py    by scene
```

Three kinds of work, kept apart on purpose:

- **Thinking** (which topic, what story, what looks good) stays with the model and the creator.
- **Repetition** (counting words, measuring loudness, matching numbers to sources, checking metadata limits) is done
  by small scripts with exit codes. A script that exits 1 means "fix the work", not "argue with the check".
- **Reviewing** is done by an agent that did not make the thing: a fact-checker, a script auditor, a render
  auditor, an upload checker.

## What makes it different

- **Gates, not paragraphs.** When a mistake repeats, it becomes a check in a script instead of a longer
  instruction. Skills stay short; the agent is not asked to remember 40 rules.
- **Verdicts bind to bytes.** A render review is valid only for the exact file it judged (SHA-256). Re-render the
  video and the upload script refuses the old approval.
- **Every number has a source.** The script may only contain numbers that exist in `facts.json` with a link; a
  script gate finds the ones that do not.
- **Nothing publishes itself.** Uploads are private, always. Publishing and scheduling are a human click in
  YouTube Studio. Your own OAuth client, stored outside the project; no shared keys.
- **Measures after publishing.** Retention comes straight from the YouTube Analytics API and is laid over your
  chapters or script, so "people left at 2:14" becomes "people left during the setup paragraph".
- **Light on context.** No session hooks and nothing injected at startup. All nine skills and six agents add
  about 1.8k tokens to a session (`claude plugin details tubecrew`); a skill's body loads only when it is used,
  and most are under 1.5k tokens.

## Installation

### Claude Code

```
/plugin marketplace add berkayburakk/tubecrew
/plugin install tubecrew@tubecrew
```

Skills appear as `tubecrew:<skill>` and agents as `tubecrew:<agent>`. Update later with
`/plugin update tubecrew`.

### Codex

Copy the skills and the agents into your Codex folders:

```bash
git clone https://github.com/berkayburakk/tubecrew.git
cp -r tubecrew/skills/* ~/.codex/skills/          # or your repo's .agents/skills/
cp tubecrew/codex/agents/*.toml ~/.codex/agents/
```

A Codex plugin manifest (`.codex-plugin/plugin.json`) is included for plugin-aware Codex builds.

### Any other agent

Every skill is a folder with a `SKILL.md` and its scripts. Copy the folders your agent can read, or paste a
`SKILL.md` into a chat; you lose the scripts, which are most of the point.

### Requirements

| For | You need |
|---|---|
| All scripts | Python 3.9+ (`python` on Windows, `python3` on macOS/Linux) |
| Render and voice checks | `ffmpeg` and `ffprobe` on PATH |
| Topic research | [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) |
| Upload and retention | `pip install google-auth google-auth-oauthlib requests` and your own Google Cloud OAuth client ([setup guide](skills/upload-private/references/oauth-setup.md)) |
| Transcribing audio | `pip install faster-whisper` (optional; any SRT/VTT/Whisper JSON transcript also works) |
| Motion graphics | Node 18+ (`morph-clips` installs its own Playwright) |

## Quick start

Open your agent in an empty folder and try:

- "Find my next video topic. My niche is space exploration explainers; here are five channels I compete with: ..."
- "Start a new video project about how a space telescope unfolds."
- "Check this render before I upload it: render/final.mp4"
- "Why did people stop watching my video `<video id>`?"

## What's inside

### Skills

| Skill | What it does | Script |
|---|---|---|
| `produce-video` | Keeps the order and the gates from idea to private upload | - |
| `find-topic` | Competitor outliers vs each channel's own median, search intent, reachable step | `outliers.py` |
| `research-facts` | One `facts.json` with every number's source, type and date; gates the script | `facts_check.py` |
| `write-script` | Opening built for the first 30 s, humanizing pass against 26 AI-writing patterns | `script_lint.py` |
| `check-voiceover` | Compares what the voice actually said with the script | `voice_check.py` |
| `check-render` | Loudness, silent tail, black frames, frozen hook, subtitle timing | `render_check.py` |
| `upload-private` | Metadata limits, chapter rules, hash-bound verdicts, private resumable upload | `upload.py` |
| `retention-by-scene` | Opening, cliffs, slide and rewatch bumps per chapter or script section | `retention.py` |
| `morph-clips` | Motion-graphic B-roll timed to your words: one shape that morphs and never cuts | `morph.mjs` |

### Review agents

| Agent | Reviews | Returns |
|---|---|---|
| `topic-skeptic` | Topic candidates and their evidence | ALIVE / DEAD per candidate |
| `fact-checker` | Every number and claim against primary sources | PASS / FAIL table |
| `script-auditor` | Original vs humanized script, paragraph by paragraph | A / B / fix + must-fix list |
| `render-auditor` | The final render, frame by frame | PASS / FAIL with timestamps |
| `upload-checker` | Title, description, chapters, links, disclosure, channel | GO / NO-GO file |
| `privacy-scanner` | Anything about to leave your machine | CLEAN / LEAK |

The maker never reviews its own work. Reviews are for what ships; drafts and experiments skip them.

## The project folder

One folder per video; every gate writes into `qa/`:

```
my-video/
  facts.json  script.md  upload.json
  voice/  render/  qa/
```

The exact file contract (verdict lines, hashes, where credentials live) is in [CONTRIBUTING.md](CONTRIBUTING.md).

## What it will never do

- Make a video public, schedule it, or delete anything.
- Upload without the render check, the render review and the upload review approving the exact same file.
- Store tokens or keys in your project, or use anyone's credentials but yours.
- Watch, like or comment on your own videos. Automated engagement breaks YouTube's rules and can cost the
  monetization account behind all of your channels.
- Invent a number, a quote or a result.

## Philosophy

The rules here came from shipping real videos and writing down every mistake. Most of them were caught the
second time by a script, not by a better prompt. If you add to tubecrew, keep that direction: when something
goes wrong twice, write the check.

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first; run `python -m pytest` and
`claude plugin validate . --strict` before opening a PR.

## Starter kit

New to all of this? [`examples/starter-kit`](examples/starter-kit) is the one-skill version (English and
Turkish) you can copy into a project in a minute.

## License

MIT. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md) for third-party credits.
