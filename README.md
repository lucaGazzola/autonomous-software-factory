# Forgeo

[![CI](https://github.com/lucaGazzola/forgeo/actions/workflows/ci.yml/badge.svg)](https://github.com/lucaGazzola/forgeo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A scheduled, agent-driven software factory for one repository. Every
`interval_minutes` it:

1. picks the oldest `OPEN` task from the backlog, runs it through a coding
   agent, and commits + pushes the result directly on `main` — no branches,
   no PRs;
2. if the backlog is empty, runs the agent in refactoring mode and commits
   whatever it improves;
3. if the agent signals it needs a human decision, writes `BLOCKER.md` with
   what you must do, and pauses until you resolve it.

## Setup

Requires Python 3.11+. Install the `factory` CLI from the public GitHub
remote with the one-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/lucaGazzola/forgeo/main/install.sh | bash
factory init          # guided setup: folder, agent command, refactor prompt
factory start
```

The installer prefers `pipx` and falls back to `pip install --user`; it never
needs root, and re-running it upgrades the install.

## Running the factory

All commands below read `factory.yaml` from the current directory; pass
`--config <file>` to use a different one.

### First-time setup

```bash
factory init             # guided wizard: repo folder, agent command, refactor prompt
factory init --force     # overwrite an existing factory.yaml
```

`factory init` writes a `factory.yaml`. Bare `factory` (no subcommand) starts
the wizard when no config exists, and prints the CLI help once a config is
present.

### Start the daemon

```bash
factory start                        # run the schedule forever
factory start --interval-minutes 5   # override the interval for this run
```

The daemon wakes up every `interval_minutes` and runs one cycle (pick the
oldest `OPEN` task, or a refactoring pass when the backlog is empty). It runs
in the foreground — interrupt it with Ctrl-C, or stop it from another
terminal with `factory stop`. Logs go to `factory.log` (rotating: 5 MB × 3),
and a local web dashboard is served at <http://127.0.0.1:8787> (disable it
with `web_port: 0` in the config).

### Run a single cycle

```bash
factory once    # run exactly one cycle and exit; no daemon needed
```

`factory once` shares the run lock with the daemon, so it never overlaps a
running `factory start` — useful to test a config or process a backlog
without leaving a daemon up.

### Check status

```bash
factory status    # config, backlog counts, next OPEN task, daemon up?, last outcome
pgrep -af factory # process check; empty output = not running
```

### Stop and restart

```bash
factory stop                # graceful shutdown (SIGTERM; a cycle in progress finishes first)
factory stop --timeout 60   # wait at most 60 s for the daemon to exit
factory restart             # stop, then start again in the background, re-reading factory.yaml
```

The daemon reads `factory.yaml` only at startup, so after editing the config
use `factory restart` — it re-reads the file.

### Where state lives

- `factory.log` — daemon / cycle log (also shown in the web dashboard).
- `backlog.lock` — lock file next to the backlog, holding the daemon PID. It
  is released automatically when the process exits, even on a crash, so a
  leftover file alone does not mean the daemon is still running.

## The backlog

A plain JSON file you edit by hand:

```json
{
  "tasks": [
    {
      "id": "TASK-001",
      "title": "Implement fibonacci module",
      "description": "Write a fibonacci module with memoization and tests.",
      "status": "OPEN",
      "created_at": "2026-07-31T10:00:00Z"
    }
  ]
}
```

Statuses: `OPEN` (to be picked), `BLOCKED` (waiting on you), `COMPLETED`,
`FAILED`. Add, remove, or reopen tasks by editing the file directly. Tip: hand
this spec to your favorite LLM to generate the initial backlog for the
application you want to build.

## Config (`factory.yaml`)

The factory reads `factory.yaml` from the current directory. A commented
example config lives in [`config/factory.yaml`](config/factory.yaml) — copy it
to your factory directory and adjust. `factory init` writes one for you
interactively. The file is machine-specific, so it is not committed to this
repository.

| Key | Meaning |
| --- | --- |
| `name` | Display name (logs, commit messages). |
| `repo` | The git repository the factory works on. |
| `interval_minutes` | How often the factory runs. |
| `branch` | The single branch everything is committed to (default `main`). |
| `remote` | Remote to push to; omit to only commit locally. |
| `backlog` | The task backlog JSON (keep it outside the repo). |
| `blocker_file` | Where `BLOCKER.md` is written (default `BLOCKER.md`). |
| `agent_command` | The coding agent: any shell command (string or argv list). |
| `agent_timeout_seconds` | Optional: kill the agent after this many seconds. |
| `agent_env` | Extra environment variables for the agent process. |
| `blocked_exit_code` | Exit code meaning "needs human input" (default `2`). |
| `refactor_prompt` | Instruction used when the backlog is empty. |
| `log_file` | Where the daemon writes its log (default `factory.log`). |
| `web_port` | Local web dashboard port (default `8787`; `0` disables the server). |
| `git_timeout_seconds` | Kill a git subprocess after this many seconds (default `120`). |
| `telegram_bot_token` | Optional Telegram bot token for blocked-run notifications (disabled unless `telegram_chat_id` is also set). |
| `telegram_chat_id` | Optional chat ID that receives blocked-run notifications (disabled unless `telegram_bot_token` is also set). |

Relative paths resolve against the config file's directory.

## The coding agent

The agent runs with the repository as working directory; the task arrives in
the `FACTORY_TASK` environment variable (title, description, acceptance
criteria). Anything a CLI agent can do works:

```yaml
agent_command: "claude -p \"$FACTORY_TASK\""
```

The exit code decides the outcome:

- `0` — success: everything is committed (`git add -A && git commit`) and
  pushed.
- `blocked_exit_code` (`2` by default) — needs a human: partial work is
  committed, `BLOCKER.md` explains what you must do, and the factory pauses
  until the task is set back to `OPEN` (or the file is deleted for a
  refactoring block).
- anything else — error: changes are discarded, task marked `FAILED`.

Only one agent runs at a time; an iteration that wakes up while the agent is
still working is skipped, never killed.

## Develop

```bash
pip install -e ".[dev]"
pytest   # backlog, git, agent, factory cycles, daemon
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, quality
gates (`pytest`, `ruff check`, `mypy src/factory`), how to write a backlog
task, and the pull-request process.

## Documentation

You can find the complete docs here: **[https://forgeo.org/](https://forgeo.org/)**
