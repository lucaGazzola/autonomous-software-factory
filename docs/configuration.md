# Configuration reference

The factory reads `factory.yaml` from the current directory (pass
`--config <file>` to any command to use a different one). The file is loaded
and validated on every invocation; relative paths resolve against the config
file's own directory, so a config can live anywhere and still point at sibling
directories.

The daemon reads `factory.yaml` **only at startup** — after editing the config
use `factory restart` so it re-reads the file.

## Keys

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | `forgeo` | Display name (logs, commit messages, Telegram notifications). |
| `repo` | `.` | The git repository the factory works on. |
| `interval_minutes` | `60` | How often the factory runs (≥ 1). |
| `branch` | `main` | The single branch everything is committed to. |
| `remote` | — | Remote to push to (e.g. `origin`); omit to only commit locally. |
| `backlog` | `backlog.json` | The task backlog JSON. Keep it outside the repo if you can. |
| `blocker_file` | `BLOCKER.md` | Where `BLOCKER.md` is written. Keep it outside the repo so it is never committed. |
| `agent_command` | — | The coding agent: any shell command (string) or argv list. **Required.** |
| `agent_timeout_seconds` | — | Optional: kill the agent after this many seconds (`null` = never). |
| `agent_env` | `{}` | Extra environment variables for the agent process. |
| `blocked_exit_code` | `2` | Exit code meaning "needs human input". |
| `refactor_prompt` | default refactor prompt | Instruction used when the backlog is empty. |
| `log_file` | `factory.log` | Where the daemon writes its log. |
| `web_port` | `8787` | Local web dashboard / HTTP API port (`0` disables the server). |
| `git_timeout_seconds` | `120` | Kill a git subprocess after this many seconds. |
| `telegram_bot_token` | — | Telegram bot token for blocked-run notifications (disabled unless `telegram_chat_id` is also set). |
| `telegram_chat_id` | — | Chat ID that receives blocked-run notifications (disabled unless `telegram_bot_token` is also set). |

## Minimal example

```yaml
name: my-project
repo: .
interval_minutes: 30
branch: main

backlog: .factory/backlog.json
blocker_file: .factory/BLOCKER.md

agent_command: "claude -p \"$FACTORY_TASK\""
refactor_prompt: >
  Review the codebase for improvement opportunities that do not change
  behavior, run the test suite, and apply safe changes.
```

## Key details

### `agent_command`

Any shell command (string) or argv list. It is run with the repository as its
working directory and the task delivered via the `FACTORY_TASK` environment
variable. A string is executed with `sh -c`; an argv list is executed directly
without a shell. See [Agent contract](agent-contract.md).

```yaml
agent_command: "claude -p \"$FACTORY_TASK\""
# or, as an argv list (no shell involved):
agent_command: ["aider", "--message", "$FACTORY_TASK"]
```

### `agent_timeout_seconds`

When set, the agent process is killed after this many seconds and the task
fails. When `null` (the default) the agent runs to completion. A run that
overruns `interval_minutes` never kills anything — the next iteration simply
skips while the previous run is still active.

### `agent_env`

Extra environment variables merged into the agent process environment. They
are merged *over* the process environment but *under* the `FACTORY_*`
variables (which are set unconditionally).

```yaml
agent_env:
  OPENAI_API_KEY: sk-...
  MODEL: claude-sonnet-4
```

### `blocked_exit_code`

The exit code the agent uses to signal "I need a human decision". On this
exit code the factory commits the agent's partial work, writes `BLOCKER.md`,
optionally notifies Telegram, and marks the task `BLOCKED`. Default `2`.

### `blocker_file`

Where the blocker file is written. Keep it **outside the repository** (the
factory pauses while this file exists, and it should not be committed).
Relative paths resolve against the config file's directory.

### `remote`

When set, successful commits are pushed to `<remote> <branch>`. When omitted,
the factory only commits locally. A push failure never discards the commit —
the work stays committed locally and the error is logged.

### `web_port`

Port for the local web dashboard / HTTP API served while the daemon is
running. Binds `127.0.0.1` only. Set to `0` to disable the server entirely. A
bind failure is logged and the daemon continues without the API.

### Telegram notifications

Both `telegram_bot_token` **and** `telegram_chat_id` must be set for blocked
run notifications. A notification failure never changes the outcome of a
cycle — it is logged as a warning.

## Default refactor prompt

When `refactor_prompt` is omitted, the factory uses:

> Review the codebase for improvement opportunities that do not change
> behavior: dead code, duplication, overly complex functions, missing tests,
> outdated comments. Apply the safe improvements you find and run the test
> suite to verify nothing broke. If nothing needs refactoring, make no
> changes.
