<div align="center">

# tubecrew

### A YouTube production crew for your coding agent.

**Topics chosen from evidence. Every number sourced. Quality gates in code. Uploads stay private until you publish.**

[![tests](https://img.shields.io/github/actions/workflow/status/berkayburakk/tubecrew/test.yml?branch=main&style=flat-square&label=tests)](https://github.com/berkayburakk/tubecrew/actions/workflows/test.yml)
[![release](https://img.shields.io/github/v/release/berkayburakk/tubecrew?style=flat-square&color=294f82)](https://github.com/berkayburakk/tubecrew/releases)
[![Claude Code plugin](https://img.shields.io/badge/Claude_Code-plugin-d97757?style=flat-square)](#install)
[![Codex plugin](https://img.shields.io/badge/Codex-plugin-10a37f?style=flat-square)](#codex)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-3776ab?style=flat-square&logo=python&logoColor=white)](#requirements)
[![MIT license](https://img.shields.io/badge/license-MIT-f4c430?style=flat-square)](LICENSE)

[Quick start](#quick-start) · [How it works](#how-it-works) · [What's inside](#whats-inside) · [Safety](#what-tubecrew-will-never-do) · [FAQ](#faq) · [Contributing](#contributing)

<code>/plugin marketplace add berkayburakk/tubecrew</code>

</div>

> [!NOTE]
> tubecrew never publishes on its own. Uploads are always private, and publishing, scheduling and anything
> irreversible remain a human decision in YouTube Studio.

---

tubecrew turns Claude Code or Codex into a production line for YouTube videos. It finds topics in what is already
outperforming in your niche, keeps every figure tied to a primary source, writes scripts meant to be spoken, checks
the voiceover and the render in code, has independent agents review the work, uploads privately, and tells you
which scene viewers left on.

Thinking stays with you and the model: which topic, what story, what looks right. Repetitive work goes to small
scripts with exit codes: counting words, measuring loudness, matching numbers to sources, checking metadata
limits. Reviews go to agents that did not make the thing they review.

## Quick start

### Install

**Claude Code**

```text
/plugin marketplace add berkayburakk/tubecrew
/plugin install tubecrew@tubecrew
```

Skills appear as `tubecrew:<skill>` and agents as `tubecrew:<agent>`. To update later, run
`claude plugin update tubecrew@tubecrew` and restart Claude Code.

<a id="codex"></a>
**Codex**

```bash
codex plugin marketplace add berkayburakk/tubecrew
```

Then open `/plugins` in Codex, install tubecrew and start a new session. The review agents are not part of the
plugin bundle; copy them once:

```bash
git clone https://github.com/berkayburakk/tubecrew.git
mkdir -p ~/.codex/agents && cp tubecrew/codex/agents/*.toml ~/.codex/agents/
```

Without plugins, copy the skills instead: `cp -r tubecrew/skills/* ~/.agents/skills/`.

**Other agents**

Each skill is a folder with a `SKILL.md` and its scripts. Copy the folders into any agent that reads skills.

### Requirements

| For | You need |
|---|---|
| All scripts | Python 3.9+ (`python` on Windows, `python3` on macOS and Linux) |
| Render check | `ffmpeg` and `ffprobe` on `PATH` |
| Topic research | [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) |
| Upload and retention | `pip install google-auth google-auth-oauthlib requests` and your own Google Cloud OAuth client ([setup guide](skills/upload-private/references/oauth-setup.md)) |
| Transcribing audio | `pip install faster-whisper` (optional; any SRT, VTT or Whisper JSON transcript also works) |
| Motion graphics | Node.js 18+ (`morph-clips` installs its own Playwright) |

### Ask your agent

```text
Find my next video topic. My niche is space exploration explainers, and these are
five channels I compete with: ...
```

```text
Start a new video project about how a space telescope unfolds.
```

```text
Check this render before I upload it: render/final.mp4
```

```text
Why did viewers stop watching my video <video id>?
```

### Run the gates yourself

Every gate is a plain script you can run from a terminal or CI:

```bash
python skills/find-topic/scripts/outliers.py @channel-one @channel-two --min-multiple 2
python skills/research-facts/scripts/facts_check.py facts.json --script script.md
python skills/check-render/scripts/render_check.py render/final.mp4 --srt captions.srt
python skills/upload-private/scripts/upload.py my-video/            # dry run, uploads nothing
```

Exit code `0` means pass, `1` means the work needs fixing, `2` means a usage or environment problem.

## How it works

Each station writes a file, and the next station starts only after its gate passes. You decide at three points:
the topic, the first 60 to 90 seconds, and publishing.

![The tubecrew production line: plan, write, make, ship and learn, each with a gate. The creator picks the topic, approves the first 60 to 90 seconds and presses publish. Gate scripts measure in code, and independent review agents never review their own work.](docs/assets/production-line.svg)

[Open the full-size figure](docs/assets/production-line.svg).

- **Gates, not paragraphs.** When a mistake repeats, it becomes a check in a script instead of a longer
  instruction. Skills stay short, so the agent spends its attention on judgement.
- **Verdicts bind to bytes.** A review is valid only for the exact file it judged (SHA-256). Re-render the video
  and the upload script rejects the old approval.
- **Every number has a source.** A script may contain only figures that exist in `facts.json` with a link; the
  script gate flags the rest.
- **The maker never reviews.** Fact-checking, script review, render review and the upload decision go to agents
  that did not produce the work. Drafts and experiments skip review; anything that ships does not.
- **Measured after publishing.** Retention comes straight from the YouTube Analytics API and is mapped onto your
  chapters or script, so "viewers left at 2:14" becomes "viewers left during the setup paragraph".
- **Light on context.** No session hooks and nothing injected at startup. All nine skills and six agents add
  about 1.8k tokens to a session (`claude plugin details tubecrew`), and a skill's body loads only when it is used.

## What's inside

### Skills

| Skill | What it does | Script |
|---|---|---|
| `produce-video` | Keeps the order and the gates from idea to private upload | - |
| `find-topic` | Ranks competitor videos by how far they beat their own channel's median; checks search intent and a reachable step for small channels | `outliers.py` |
| `research-facts` | One `facts.json` with every figure's source, type and date; gates the script against it | `facts_check.py` |
| `write-script` | An opening built for the first 30 seconds, a humanizing pass against 26 AI-writing patterns, a mechanical lint | `script_lint.py` |
| `check-voiceover` | Compares what the voiceover actually said with the script: skipped phrases, wrong numbers, repeats | `voice_check.py` |
| `check-render` | Loudness, silent tails, black frames, a frozen hook and subtitle timing in one pass | `render_check.py` |
| `upload-private` | Metadata limits, chapter rules, hash-bound verdicts and a resumable, private-only upload | `upload.py` |
| `retention-by-scene` | Opening loss, cliffs, slide and rewatch bumps, mapped to chapters or script sections | `retention.py` |
| `morph-clips` | Motion-graphic B-roll timed to your words: one shape that morphs and never cuts | `morph.mjs` |

### Review agents

| Agent | Reviews | Returns |
|---|---|---|
| `topic-skeptic` | Topic candidates and their evidence | ALIVE or DEAD for each candidate |
| `fact-checker` | Every number and claim, against primary sources | PASS or FAIL, with a table of claims |
| `script-auditor` | The original and the humanized script, paragraph by paragraph | A, B or a fix, plus a must-fix list |
| `render-auditor` | The final render, frame by frame | PASS or FAIL, with timestamps |
| `upload-checker` | Title, description, chapters, links, disclosure and target channel | A GO or NO-GO file |
| `privacy-scanner` | Anything about to leave your machine | CLEAN or LEAK |

### The project folder

One folder per video. Every gate writes its result into `qa/`:

```text
my-video/
├── facts.json      every figure with its source
├── script.md       the spoken script
├── upload.json     title, description, tags, disclosure flags
├── voice/          narration audio and its transcript
├── render/         the final render
└── qa/             gate results and review verdicts
```

The full contract (verdict lines, hashes and where credentials live) is in [CONTRIBUTING.md](CONTRIBUTING.md).

## What tubecrew will never do

- Make a video public, schedule it, or delete anything.
- Upload a file that has not passed the render check. The render review and the upload review must approve the
  same file too; skipping them takes an explicit `--skip-review "<reason>"`, and the reason is logged.
- Store tokens or keys in your project, or use any credentials other than your own.
- Watch, like or comment on your own videos. Automated engagement violates YouTube's policies and can put the
  monetization account behind all of your channels at risk.
- Invent a number, a quote or a result.

## FAQ

<details>
<summary><strong>Does tubecrew upload or publish videos for me?</strong></summary>

It uploads only when you run the upload script with `--upload`, and always as private. Publishing, scheduling and
visibility changes happen in YouTube Studio, by you.
</details>

<details>
<summary><strong>Do I need an API key or a paid service?</strong></summary>

No. Topic research, script, voiceover and render checks run locally with free tools. Upload and retention use your
own Google Cloud OAuth client, which is free to create. Voice generation is up to you: any text-to-speech service,
or your own recording.
</details>

<details>
<summary><strong>Does it work for languages other than English?</strong></summary>

Mostly, yes. The render and upload gates do not depend on language, and the voiceover check works with any
language faster-whisper supports, including number formats that use a decimal comma. The script lint's
AI-vocabulary lists and the fact check's number parsing assume English; the lint's other measurements (sentence
length, runtime, phrase timing) work in any language.
</details>

<details>
<summary><strong>Is it only for faceless channels?</strong></summary>

No. Talking-head and screen-recording channels use the same research, script, render and upload gates.
`check-voiceover` compares a recorded take with the script just as it does with synthetic speech, and `morph-clips`
adds motion graphics to recorded footage.
</details>

<details>
<summary><strong>Why scripts instead of longer instructions?</strong></summary>

Long instruction files get skimmed, and the middle gets lost. A check with an exit code cannot be skimmed. Keeping
skills short and moving the mechanical work into scripts leaves the agent's attention for decisions that need
judgement.
</details>

## Contributing

Issues and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first, then run `python -m pytest`
and `claude plugin validate . --strict` before opening a pull request.

New to agent skills? [`examples/starter-kit`](examples/starter-kit) is a single-skill version you can copy into a
project in a minute.

## License

MIT. See [LICENSE](LICENSE), and [NOTICE.md](NOTICE.md) for third-party credits.
