# Forgeo

A **scheduled, agent-driven software factory** for one repository. Every
`interval_minutes` the factory wakes up and runs exactly one of three things:

1. picks the oldest `OPEN` task from the [backlog](backlog.md), runs it through
   a coding [agent](agent-contract.md), and commits + pushes the result
   directly on the single configured branch — no branches, no PRs;
2. if the backlog is empty, runs the agent in **refactoring mode** and commits
   whatever it improves;
3. if the agent signals it needs a human decision, writes a `BLOCKER.md` with
   what you must do, and pauses until you resolve it.

## What the factory is

The factory is a small Python daemon and CLI that turns your repository into a
self-maintaining codebase. You maintain a plain-JSON backlog of tasks; the
factory works through it with whatever coding agent you configure (aider,
Claude, a custom script — anything that reads the `FACTORY_TASK` environment
variable). When there is nothing left to do, the same agent switches to
refactoring mode and keeps the codebase tidy.

The factory is deliberately single-purpose:

- one repository per config;
- one branch, everything committed on `main` (or whichever `branch` you set);
- one agent at a time — an iteration that wakes up while the agent is still
  working is skipped, never killed;
- no PRs, no merge strategies, no branch juggling.

## Architecture overview

```
factory.yaml ──► factory start (daemon)
                     │
                     ├── wakes every interval_minutes
                     ▼
                 Factory.run_cycle()
                     │
                     ├── BLOCKED task exists ──► write BLOCKER.md, pause
                     │
                     ├── oldest OPEN task ──► run agent ──► commit & push ──► COMPLETED
                     │
                     └── backlog empty ──► run agent (refactor) ──► commit & push
                                                │
            exit 0                              │            exit blocked_exit_code
        commit & push ──────── ShellAgent ──────┴─────► partial work committed,
            task COMPLETED   (FACTORY_TASK env)          BLOCKER.md written,
                                                         task BLOCKED
```

### Components

| Component | Source | Responsibility |
| --- | --- | --- |
| `factory.cli` | `src/factory/cli.py` | `init`, `start`, `once`, `status`, `stop`, `restart` commands. |
| `factory.daemon` | `src/factory/daemon.py` | The scheduled worker: wakes every `interval_minutes`, holds the run locks, records `last_outcome`. |
| `factory.factory` | `src/factory/factory.py` | One cycle of work: task run, refactor pass, blocker handling, git side effects. |
| `factory.backlog` | `src/factory/backlog.py` | JSON backlog read/write; picks the oldest `OPEN` task. |
| `factory.agent` | `src/factory/agent.py` | `ShellAgent`: runs your command, maps exit codes to outcomes, delivers `FACTORY_TASK`. |
| `factory.git` | `src/factory/git.py` | Single-branch git operations: ensure branch, commit all, push, hard reset. |
| `factory.config` | `src/factory/config.py` | Loads and validates `factory.yaml`. |
| `factory.server` | `src/factory/server.py` | Local read-only HTTP API / web console served by the daemon. |
| `factory.setup` | `src/factory/setup.py` | The guided `factory init` wizard. |
| `factory.notify` | `src/factory/notify.py` | Optional Telegram notifications for blocked runs. |
| `factory.models` | `src/factory/models.py` | The data contracts: `Task`, `FactoryConfig`, `ExecutionResult`, statuses. |

### One cycle, in detail

1. The daemon acquires a per-factory lock (`backlog.lock`); a second `start` or
   `once` is refused while it is held.
2. `Factory.run_cycle()` ensures the configured branch exists and is checked
   out.
3. If any task is `BLOCKED`, the factory rewrites `BLOCKER.md` and pauses
   (`blocked` outcome) — it will not start new work until the human resolves
   the block.
4. Otherwise it fetches the oldest `OPEN` task. If the working tree is dirty
   the cycle aborts (`dirty`) rather than running over manual changes.
5. The agent runs with the repository as its working directory and the task in
   `FACTORY_TASK`.
6. The exit code decides the outcome: `0` = commit & push (task becomes
   `COMPLETED`); `blocked_exit_code` = commit partial work, write `BLOCKER.md`,
   notify (task becomes `BLOCKED`); anything else = discard changes, log the
   error (task becomes `FAILED`).
7. With no `OPEN` task and no blocker file, the agent runs in refactoring mode
   and its changes are committed the same way.
8. The daemon sleeps until the next interval. If a previous run is still in
   progress when it wakes, that iteration is skipped.

## Where state lives

- `factory.yaml` — the config (see [Configuration](configuration.md)).
- `backlog.json` (configurable) — the task backlog (see
  [Backlog format](backlog.md)).
- `BLOCKER.md` (configurable) — written when a human decision is needed; keep
  it outside the repo so it is never committed.
- `factory.log` — rotating daemon log (5 MB × 3), also served over HTTP.
- `backlog.lock` — per-factory lock holding the daemon PID; released
  automatically on exit, even on a crash.
- `backlog.run` — per-iteration lock that prevents two agents running at once.

## Next steps

- [Getting Started](getting-started.md) — install and run your first cycle.
- [CLI reference](cli-reference.md) — every command.
