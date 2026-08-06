# Web console & HTTP API

The **central dashboard** (`factory web`) is the one and only web interface
for every factory instance. Daemons themselves bind no ports: `factory start`
just schedules cycles and writes its live state to `daemon.state.json`. The
dashboard reads every registered instance's data straight from its files
(`backlog.json`, `runs.jsonl`, `factory.log`, `BLOCKER.md`,
`daemon.state.json`), so it works whether or not each instance's daemon is
running.

```bash
factory web               # default 0.0.0.0:8790
factory web --port 9000   # pick a different port
factory web --host 127.0.0.1
```

It runs in the foreground like `factory start`. By default it binds
**`0.0.0.0`** so you can open it from any machine on your LAN — use
`--host 127.0.0.1` to restrict it to the local machine (open the port in your
firewall too).

![Forgeo web console](img/console.png)

The server is implemented with the standard library (`factory.central`);
static files in `src/factory/web/` are served at their URL paths.

## Pages

- `GET /` — home page listing every registered instance: name, repository,
  daemon state (lock held), last outcome, next run, and per-status backlog
  counts, each linking to its instance page.
- `GET /instances/<name>/` — one instance's page: a kanban backlog, a
  **Create** tab with a form to add tasks, plus tabs for **logs**, **runs**,
  **blocker** and **config**. Clicking a task card opens a modal with the full
  task details (description, acceptance criteria, dependencies, files to
  modify, agent command, timestamps); it closes via the close button, the
  backdrop, or Escape.
- `GET /style.css`, `/central/central.js`, `/central/central.css` — the
  shared dark theme and dashboard scripts (no frameworks).

## Endpoints

All endpoints return JSON with `Content-Type: application/json` and
`Cache-Control: no-store`. Every per-instance endpoint lives under
`/api/instances/<name>/`.

### `GET /api/instances`

A JSON list of every registered instance with its repo, daemon state, last
outcome, next run, and backlog counts.

```bash
curl http://127.0.0.1:8790/api/instances
```

### `GET /api/instances/<name>/tasks`

List every task in that instance's backlog, in creation order.

```bash
curl http://127.0.0.1:8790/api/instances/my-repo/tasks
```

```json
[
  {
    "id": "TASK-001",
    "title": "Implement fibonacci module",
    "description": "Write a fibonacci module with memoization and tests.",
    "status": "OPEN",
    "created_at": "2026-07-31T10:00:00Z",
    "updated_at": "2026-07-31T10:00:00Z",
    "dependencies": [],
    "acceptance_criteria": [],
    "files_to_modify": []
  }
]
```

### `GET /api/instances/<name>/tasks/{id}`

Fetch a single task by id.

```bash
curl http://127.0.0.1:8790/api/instances/my-repo/tasks/TASK-001
```

Returns `404` with `{"error": "not found"}` for an unknown id.

### `POST /api/instances/<name>/tasks`

Create a new task in that instance's backlog. The request body must be a JSON
object with a non-blank `title`; `description` (string) and
`acceptance_criteria` (array of strings) are optional. The server generates
the id as the next free `WEB-###` id and stamps `created_at`/`updated_at`.

```bash
curl -X POST http://127.0.0.1:8790/api/instances/my-repo/tasks \
  -H 'Content-Type: application/json' \
  -d '{"title": "Implement fibonacci module", "description": "With tests.", "acceptance_criteria": ["passes pytest"]}'
```

```json
{
  "id": "WEB-001",
  "title": "Implement fibonacci module",
  "description": "With tests.",
  "status": "OPEN",
  "created_at": "2026-08-01T12:00:00Z",
  "updated_at": "2026-08-01T12:00:00Z",
  "dependencies": [],
  "acceptance_criteria": ["passes pytest"],
  "files_to_modify": []
}
```

Returns `201` with the created task. The write is atomic (temp file +
rename), so it is safe even while that instance's daemon is mid-cycle.
Errors:

- `400` with `{"error": "..."}` — missing/blank `title`, unparseable or
  non-object body, or a field of the wrong type.
- `404` with `{"error": "unknown instance"}` — the instance is not
  registered.
- `409` with `{"error": "..."}` — the generated id already exists in the
  backlog (e.g. two concurrent requests raced).

### `GET /api/instances/<name>/status`

Daemon status: name, repo, interval, `daemon_running` (whether the instance's
lock is held), the recorded PID, `last_outcome`, and the `next_run_at`.

```bash
curl http://127.0.0.1:8790/api/instances/my-repo/status
```

```json
{
  "name": "my-repo",
  "repo": "/home/me/projects/site-a",
  "interval_minutes": 30,
  "daemon_running": true,
  "pid": 4242,
  "last_outcome": "task",
  "next_run_at": "2026-08-01T12:00:00+00:00"
}
```

`pid`, `last_outcome` and `next_run_at` come from the daemon's
`daemon.state.json` (written after every cycle). When no state file exists,
`last_outcome` falls back to `runs.jsonl` and `next_run_at` to an estimate
(the last run's finish plus the interval) — only while the daemon is running.
`next_run_at` is `null` when the daemon is not running.

### `GET /api/instances/<name>/config`

The resolved `factory.yaml` as JSON.

```bash
curl http://127.0.0.1:8790/api/instances/my-repo/config
```

### `GET /api/instances/<name>/logs?lines=N`

The last `N` lines of that instance's `factory.log` (`N` defaults to `100`,
max `10000`).

```bash
curl http://127.0.0.1:8790/api/instances/my-repo/logs
curl "http://127.0.0.1:8790/api/instances/my-repo/logs?lines=50"
```

### `GET /api/instances/<name>/runs?limit=N`

That instance's durable run history from `runs.jsonl`, newest first (`limit`
defaults to `10`, max `10000`). Each record has started/finished timestamps,
the run kind (`task` or `refactor`), the task id and title when applicable,
the outcome (`SUCCESS` / `BLOCKED` / `ERROR`), the agent exit code, the commit
SHA when a commit was created, and the duration in seconds.

```bash
curl http://127.0.0.1:8790/api/instances/my-repo/runs
curl "http://127.0.0.1:8790/api/instances/my-repo/runs?limit=5"
```

### `GET /api/instances/<name>/blocker`

The instance's `BLOCKER.md` contents, or `{"content": null}` when none
exists.

```bash
curl http://127.0.0.1:8790/api/instances/my-repo/blocker
```

## Behavior

- An unknown instance name returns `404` (`{"error": "unknown instance"}`).
- A registered instance with missing data files renders with empty data and
  `daemon_running=false` instead of erroring — the instance page and every
  API endpoint still return `200`.
- The only write endpoint is `POST /api/instances/<name>/tasks`, which
  appends to the instance's backlog.

## Errors

- `400` — malformed `POST` body (missing/blank title, unparseable body,
  wrong field types).
- `404` — unknown API path, unknown instance, unknown task, or missing
  static file.
- `409` — `POST` id collision (a concurrent request won the race).
- `500` — an unexpected handler error (logged server-side).

## Example: a status one-liner

```bash
curl -s http://127.0.0.1:8790/api/instances/my-repo/status
```

## Security notes

- The dashboard binds `0.0.0.0` by default so every factory on the host is
  visible from your LAN. Exposing it publicly (`--host 0.0.0.0` on a public
  interface) makes every instance's backlog, logs, and config visible to
  every host that can reach the port — only do that on a trusted network.
- The only write endpoint is `POST /api/instances/<name>/tasks`, which
  appends to an instance's backlog. A machine that can reach the port can add
  tasks to any instance's queue.
