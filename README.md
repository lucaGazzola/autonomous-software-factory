# Forgeo

[![CI](https://github.com/lucaGazzola/forgeo/actions/workflows/ci.yml/badge.svg)](https://github.com/lucaGazzola/forgeo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Forgeo is an autonomous software factory for people with ideas, not teams.**
You have a product idea — an app, a website, an internal automation — but no
developers on staff. With Forgeo you don't need any: you write down what needs
to be built as a simple list of tasks, and an AI coding agent works through the
list on its own, writing the code and committing it to your repository. No
branches, no pull requests, no developer to hire.

All you need is basic comfort with a terminal, a git repository, and any coding
agent CLI — Claude Code, aider, opencode, or your own script. Forgeo works with
all of them.

Forgeo decides what to do next on its own: while tasks are left it implements
the oldest one and commits the result, and when the backlog is empty it reviews
the codebase and cleans it up. It only interrupts you when a decision is
genuinely yours to make — everything else happens autonomously.

## Quickstart

### 1. Install

Requires Python 3.11+:

```bash
curl -fsSL https://forgeo.org/install.sh | bash
```

The installer prefers `pipx`, falls back to `pip install --user`, never needs
root, and re-running it upgrades the install.

### 2. Create your factory

Run the guided wizard from your project root:

```bash
factory init
```

It asks for your factory folder, your coding agent command, and the refactor
prompt — then writes `factory.yaml` and a `.factory/` folder for the backlog
and logs, gitignored for you.

Or write `factory.yaml` by hand — this is all it takes:

```yaml
name: my-project
repo: .
interval_minutes: 30
backlog: .factory/backlog.json

agent_command: "claude -p \"$FACTORY_TASK\""
refactor_prompt: >
  Review the codebase for improvement opportunities that do not change
  behavior, run the test suite, and apply safe changes.
```

`agent_command` is the heart of it: any CLI agent that can work in a
repository, with the task delivered in the `FACTORY_TASK` environment
variable. Every other key — branch, remote, sandboxing, notifications — is
documented in the [configuration reference](docs/configuration.md).

All commands read `factory.yaml` from the current directory; pass
`--config <file>` to point at a different one.

### 3. Write your backlog

The backlog is a plain JSON file — create the one `factory.yaml` points at:

```json
{
  "tasks": [
    {
      "id": "TASK-001",
      "title": "Implement fibonacci module",
      "description": "Write a fibonacci module with memoization and tests.",
      "status": "OPEN",
      "created_at": "2026-07-31T10:00:00Z"
    },
    {
      "id": "TASK-002",
      "title": "Add docstrings to the public API",
      "description": "Small, mechanical change.",
      "agent_command": "claude -p \"$FACTORY_TASK\" --model claude-3-haiku",
      "agent_timeout_seconds": 120,
      "status": "OPEN",
      "created_at": "2026-07-31T10:01:00Z"
    }
  ]
}
```

Statuses: `OPEN` (next up), `BLOCKED` (waiting on you), `COMPLETED`, `FAILED`.
Add, remove, or reopen tasks by editing the file — no tool needed. A task can
also override the factory's agent with its own `agent_command`: use a
cheap/fast model for trivial tasks and a frontier one for the hard cases. Full
task schema in the [backlog format](docs/backlog.md).

Tip: hand this file to your favorite LLM together with a description of your
product to generate the initial backlog.

### 4. Run the factory

```bash
factory start     # run forever: every interval_minutes, implement the oldest OPEN task
factory once      # run a single cycle and exit — useful to test your setup
factory status    # config, backlog counts, next task, daemon running?
factory stop      # graceful shutdown (from another terminal)
factory restart   # re-read factory.yaml after editing it
```

`factory start` runs in the foreground (Ctrl-C to stop) and serves a local
dashboard at <http://127.0.0.1:8787> while it runs (bind to `0.0.0.0` via
`web_host` in `factory.yaml` to reach it from other hosts on your LAN).
Everything is stored in plain files: the backlog, `factory.log`, and
`BLOCKER.md` whenever a decision is pending. Every command is detailed in the
[CLI reference](docs/cli-reference.md).

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

## Running the agent in a Docker sandbox (opt-in)

By default the agent runs directly on the host with your full privileges. To
contain a misbehaving agent, set `agent_sandbox: docker` in `factory.yaml`:

```yaml
agent_sandbox: docker
agent_sandbox_image: forgeo-agent
```

The factory then runs `docker run --rm` instead of executing the command on
the host:

- the repository is bind-mounted into the container at the same absolute
  path, so the agent's edits land on your checkout as usual;
- `FACTORY_TASK` (and the other `FACTORY_*` variables) plus `agent_env` are
  passed through as container environment variables;
- networking is disabled (`--network none`) by default. Set
  `agent_sandbox_network` (e.g. `bridge`, `host`) to re-enable it;
- nothing of yours is visible inside the container unless you list it in
  `agent_sandbox_mounts`, which mounts host paths (agent credentials/config
  such as `~/.claude`) read-only at the same path.

**Image expectations:** `agent_sandbox_image` must be an image that already
contains the agent CLI your `agent_command` uses (e.g. `claude`, `opencode`)
and a POSIX shell (`sh`) for string commands. No packages are installed at run
time. The exit-code contract is unchanged: `0`, `blocked_exit_code`, or any
other code map exactly as they do on the host.

Requires a working `docker` binary on the factory host. If it is missing,
`factory start` / `factory once` fail fast with a clear error (and a cycle that
somehow reaches docker without it is reported as a task error).

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
