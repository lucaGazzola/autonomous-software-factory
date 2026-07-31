# Software Factory

A scheduled software factory for one repository. Every `interval_minutes` it:

1. picks the oldest `OPEN` task from the backlog, executes it with a coding
   agent, and commits + pushes the result on `main` — no branches, no PRs;
2. if the backlog has nothing runnable, runs the agent in refactoring mode
   and commits + pushes whatever it improves;
3. if the agent signals that it needs human input, writes `BLOCKER.md` with
   a detailed explanation of what you must do, and pauses until you resolve
   it.

## The backlog

A plain JSON file, editable by hand:

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
`FAILED`. Edit the file directly to add, remove, or reopen tasks.

Give this spec to your favorite llm and create a backlog for the application you want to build!

## Quick start

```bash
pip install -e ".[dev]"
factory init          # guided setup: folder, agent command, refactor prompt
factory start
```

Running `factory` or `factory start` without a `factory.yaml` triggers the
guided setup automatically. The factory wakes up every `interval_minutes`,
and logs to `factory.log`.

## First run (`factory init`)

`factory init` walks you through the three decisions the factory needs:

| Question | Meaning |
| --- | --- |
| Factory folder | Where the backlog, `BLOCKER.md` and the log live (default `.factory/`, inside the project). It is added to `.gitignore` so the factory never commits its own bookkeeping. |
| Coding agent command | Any shell command; the task arrives as the `FACTORY_TASK` environment variable, e.g. `claude -p "$FACTORY_TASK"`. |
| Refactor prompt | Instruction used when the backlog is empty. The default is offered; answer `n` to paste your own (empty line finishes). |

The result is a `factory.yaml` in the project root. Re-run with
`factory init --force` to regenerate it.

## The config (`factory.yaml`)

| Key | Meaning |
| --- | --- |
| `name` | Display name (logs, commit messages). |
| `repo` | The git repository the factory works on. |
| `interval_minutes` | How often the factory runs. |
| `branch` | The single branch everything is committed to (default `main`). |
| `remote` | Git remote to push to; omit to only commit locally. |
| `backlog` | The JSON task backlog (keep it outside the repo). |
| `blocker_file` | Where `BLOCKER.md` is written (keep it outside the repo). |
| `agent_command` | The coding agent: any shell command (string or argv list). |
| `agent_timeout_seconds` | Optional: kill the agent after this many seconds. Omit for no timeout (default) — a run that overruns the interval just skips the next iteration. |
| `agent_env` | Extra environment variables for the agent process. |
| `blocked_exit_code` | Exit code that means "needs human input" (default `2`). |
| `refactor_prompt` | Instruction used when the backlog is empty. |

Relative paths resolve against the config file's directory.

## The coding agent

`agent_command` runs with the repository as its working directory. The task
is delivered as the `FACTORY_TASK` environment variable (title, description,
acceptance criteria). Anything CLI coding tools can do works:

```yaml
agent_command: "aider --message \"$FACTORY_TASK\""
# or
agent_command: "claude -p \"$FACTORY_TASK\""
```

The exit code decides the outcome:

* `0` — success: everything is committed (`git add -A && git commit`) on
  `main` and pushed.
* `blocked_exit_code` (`2` by default) — the agent needs a human decision:
  its partial work is committed, and `BLOCKER.md` is written with the task,
  what the agent says it needs, and exactly what you must do. The factory
  pauses until the task is set back to `OPEN` (or the file is deleted for a
  refactoring block).
* anything else — error: the agent's changes are discarded and the task is
  marked `FAILED`.

### Overlapping runs

Only one agent run happens at a time per factory. A per-run lock (a `.run`
file next to the backlog) is held while a cycle runs; if the agent is still
working when the next iteration wakes up, that iteration is skipped and the
running agent is left alone — the factory never starts a second agent on the
same repository and never kills a long-running agent. Set
`agent_timeout_seconds` only if you want a stuck agent to be killed as an
escape hatch.

## BLOCKER

When the agent exits with `blocked_exit_code`, the factory writes
`blocker_file` (default `BLOCKER.md`):

* what the task was,
* what the agent says it needs (its output),
* what you must do: decide, then set the task's status back to `OPEN` in the
  backlog (or delete the task / delete the blocker file for refactoring
  blocks).

The factory stays paused while `BLOCKER.md` exists.

## Develop

```bash
pytest   # backlog, git, agent, factory cycles, daemon
```
