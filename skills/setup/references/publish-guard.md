# Publish guard (opt-in hook)

`scripts/publish_guard.py` is a Claude Code `PreToolUse` hook. It stops the agent from uploading or publishing
on its own. When a shell command would upload or publish, Claude Code asks the creator first. When nobody is
there to ask, the command is refused.

## What it guards

| Command | Answer |
|---|---|
| `python .../upload.py <video> --upload` (any unquoted `--upload` or `--schedule` flag) | ask |
| a script named `*upload*` or `*publish*` run by an interpreter (`python yt_publish.py`, `bash publish.sh`) | ask |
| uploader programs `youtube-upload`, `youtubeuploader`, and anything you add in the config | ask |
| a quoted command handed to a shell (`bash -c "..."`, `powershell -Command "..."`, `Start-Process -ArgumentList "..."`) | checked like the rest |
| the same commands in an unattended session | deny, with the reason shown to the agent |
| tubecrew's `upload.py <video>` dry run, `--login`, `--help`, `--version`, `--dry-run` | no answer |
| pytest, read-only programs (`grep`, `cat`, `git`, `echo`, `Select-String`, ...) | no answer |
| text inside quotes: `grep -n "--upload" upload.py`, `python -c "...--upload..."` | no answer |

"No answer" means the hook stays out of the way and Claude Code's normal permission rules decide. The hook never
answers "allow", so it can only add a question, never remove one.

## Why a hook and not a prompt rule

"Never publish without asking" in `CLAUDE.md` or a skill is a request. The model can forget it in a long
session, decide a case is an exception, or follow an older instruction. A broad `allow` rule added for
convenience can also open the door without anyone noticing. A hook runs on every shell call, whatever the model
remembers.

The upload script stays the real gate: it uploads as private only and checks the review hashes. The guard
adds the one thing a script cannot know: whether a person agreed to run it now.

## Why it is opt-in

It is not shipped as an always-on plugin hook on purpose:

- It would run on every shell call of every user of the plugin, including users who never upload from the agent.
- It needs a working Python. An always-on hook with a broken interpreter path disturbs every shell call.
- The answer to "who may publish" belongs to the creator, so the creator turns it on.

## Install

1. Copy the hook to a stable place and print its settings block:

   ```bash
   python <setup skill>/scripts/publish_guard.py --install-copy
   ```

   This copies the script to `~/.tubecrew/hooks/publish_guard.py` (or `$TUBECREW_HOME/hooks/`). Point the hook at
   the copy, not at the plugin folder, because the plugin folder can move or disappear when the plugin updates.
   If the hook points at a missing script, `python` exits with code 2. Claude Code reads exit code 2 as "block",
   so every shell call would be refused. Run `--install-copy` again after a plugin update to refresh the copy.

2. Paste the printed block into a settings file, merging it into any existing `"hooks"` key:

   - `~/.claude/settings.json`: every project on this machine (recommended).
   - `<project>/.claude/settings.local.json`: one project, not committed.
   - `<project>/.claude/settings.json`: one project, shared with the repository. The block holds absolute paths
     of this machine, so do not commit it to a public repository.

   The block looks like this, with the absolute paths of your Python and of the copy:

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash|PowerShell",
           "hooks": [
             {
               "type": "command",
               "command": "/usr/bin/python3",
               "args": ["/home/you/.tubecrew/hooks/publish_guard.py"],
               "timeout": 10
             }
           ]
         }
       ]
     }
   }
   ```

   `command` plus `args` is the exec form. Claude Code starts Python directly without a shell, so paths with
   spaces need no quoting. On Windows, `command` must be a real `.exe`, such as the full path to `python.exe`.
   For a Claude Code version without the `args` field, use `--install-copy --shell-form`. It prints one quoted
   command string instead.

3. Restart Claude Code and check that `/hooks` lists the PreToolUse entry.

4. Test it without uploading anything:

   ```bash
   python ~/.tubecrew/hooks/publish_guard.py --check "python upload.py my-video --upload"
   # ASK: Publish guard: `--upload` uploads or schedules a video. ...
   ```

## Unattended sessions

In a headless run (`claude -p`, a scheduled job, CI), an "ask" has no one to answer it. So the guard answers
"deny" instead. The reason is shown to the agent, which then tells the creator the command is ready to run.
The guard treats a session as unattended when:

- `TUBECREW_HEADLESS=1` is set. Set it in your own job runner; it is the reliable switch.
  `TUBECREW_HEADLESS=0` forces "ask" and overrides everything below.
- a variable named in the config's `headless_env` is set (for example the job id your runner exports).
- `CI` is set to a true value.
- the session runs in `dontAsk` permission mode.
- Claude Code reports an unattended session. This is a best-effort signal that is not part of the documented
  hook contract; it can only turn "ask" into "deny".

## Config

Optional: `~/.tubecrew/publish-guard.json`. A missing or broken file keeps the defaults on and prints one line on
stderr.

```json
{
  "extra_flags": ["--publish"],
  "extra_programs": ["acme-uploader"],
  "extra_patterns": ["\\bgo-live\\b"],
  "dry_by_default": ["upload.py"],
  "headless_env": ["MY_RUNNER_JOB"]
}
```

`extra_*` and `headless_env` add to the defaults. `dry_by_default` replaces the list of scripts that upload only
with a flag. List your own uploader there if it is a dry run by default.

## Limits

- It reads command lines, not code. An upload built inside `python -c "..."`, or a script with an innocent name
  that uploads, is not caught.
- It watches shell calls only. An MCP tool that writes to the channel (upload, update a video, set a
  thumbnail) is a different tool call. Put those tool names in `permissions.ask` in the same settings file, for
  example `"permissions": {"ask": ["mcp__<server>__<tool>"]}`.
- If the hook itself crashes or gets unreadable input, it prints one line on stderr and lets the call through.
  A guard that broke every shell call would simply get switched off.

## Remove

Delete the PreToolUse entry from the settings file, delete `~/.tubecrew/hooks/publish_guard.py` and the optional
config, then restart Claude Code.
