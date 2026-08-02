# Backlog format

The backlog is a **plain JSON file** you edit by hand. It lives wherever
`backlog:` points in [factory.yaml](configuration.md) — by default
`backlog.json` at the project root, and `.factory/backlog.json` when generated
by `factory init`. Keep it outside the repository if you can so the agent never
touches it.

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

## Task schema

Each entry in `tasks` is a task object:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `id` | string | — | Unique task id (e.g. `TASK-001`). Duplicate ids are rejected. |
| `title` | string | — | Short title; shown in logs, commit messages and status. |
| `description` | string | `""` | Longer description handed to the agent. |
| `status` | string | `OPEN` | One of `OPEN`, `BLOCKED`, `COMPLETED`, `FAILED`. |
| `created_at` | ISO-8601 datetime | now (UTC) | When the task was created; used for oldest-first ordering. |
| `updated_at` | ISO-8601 datetime | now (UTC) | Bumped whenever the status changes. |
| `dependencies` | list[string] | `[]` | Task ids this task depends on (informational; not enforced). |
| `acceptance_criteria` | list[string] | `[]` | Rendered into the `FACTORY_TASK` instruction under an "Acceptance criteria:" heading. |
| `files_to_modify` | list[string] | `[]` | Informational; hints for the agent. |

Only `id`, `title`, and `status` (optionally) are required; every other field
is optional.

## Statuses

| Status | Meaning |
| --- | --- |
| `OPEN` | To be picked by the factory. |
| `BLOCKED` | Waiting on a human decision; the factory pauses while any task is blocked. |
| `COMPLETED` | The agent finished and the work was committed (and pushed). |
| `FAILED` | The agent errored; changes were discarded. |

You add, remove, or reopen tasks by editing the file directly. To retry a
`BLOCKED` task, set its status back to `OPEN` — the factory picks it up on the
next scheduled run.

## Oldest-first ordering

The factory picks the **oldest `OPEN` task**, i.e. the `OPEN` task with the
smallest `created_at`. Tasks in other states are ignored for picking:

- `BLOCKED` tasks do not get picked, but their presence pauses the factory.
- `COMPLETED` and `FAILED` tasks are skipped.

Set `created_at` deliberately (e.g. back-date a task) if you want to control
the order in which tasks are processed.

## How a task is executed

1. The factory ensures the configured branch exists and the working tree is
   clean.
2. The agent runs with the repository as its working directory; the task
   (title, description, acceptance criteria) arrives as `FACTORY_TASK`.
3. Exit `0` → the work is committed as `factory: <title> (#<id>)`, pushed if a
   remote is set, and the task becomes `COMPLETED`.
4. Exit `blocked_exit_code` → partial work is committed as
   `factory: <title> (#<id>) [partial]`, `BLOCKER.md` explains what you must
   do, optionally Telegram is notified, and the task becomes `BLOCKED`.
5. Any other exit code → changes are discarded (`git reset --hard` +
   `git clean -fd`), the failure is logged, and the task becomes `FAILED`.

## Corruption tolerance

The backlog reader is defensive:

- a missing file is treated as an empty backlog (and is created on first
  write);
- a corrupt file is renamed to `backlog.json.corrupt-<timestamp>` and the
  factory starts from an empty store — nothing is silently discarded;
- an unparsable task row is kept as a `FAILED` task rather than killing the
  whole store.

## Generating an initial backlog

There is no backlog editor in the CLI — the backlog is a hand-edited file.
Tip: hand this spec to your favorite LLM along with a description of your
application to generate the initial `tasks` array.
