# Software Factory

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

```bash
pip install -e ".[dev]"
factory init          # guided setup: folder, agent command, refactor prompt
factory start
```

`factory init` writes a `factory.yaml` (re-run with `--force` to overwrite;
bare `factory` also starts the wizard when no config exists). `factory start`
runs the schedule forever — interrupt it with Ctrl-C. Logs go to `factory.log`.

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
pytest   # backlog, git, agent, factory cycles, daemon
```
