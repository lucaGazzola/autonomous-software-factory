# Web console & HTTP API

While the daemon is running, it serves a **local web dashboard and HTTP API**
at `http://<web_host>:<web_port>` (default `127.0.0.1:8787`). By default the
server binds **`127.0.0.1` only** — it is not reachable from other machines.
Set `web_host: 0.0.0.0` in `factory.yaml` to make it reachable from your local
network (open the port in your firewall too).

- Disable it entirely with `web_port: 0` in `factory.yaml`.
- A bind failure is logged and the daemon continues without the API.
- The API is read-only except for `POST /api/tasks`, which adds a new task to
  the backlog (see below). It never modifies the repository or the daemon.

![Forgeo web console](img/console.png)

The API is served by `factory.server` using the standard library; any static
files placed in `src/factory/web/` are served at `/` so the same server can
host a small dashboard.

## Endpoints

All endpoints return JSON with `Content-Type: application/json` and
`Cache-Control: no-store`.

### `GET /api/tasks`

List every task in the backlog, in creation order.

```bash
curl http://127.0.0.1:8787/api/tasks
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

### `GET /api/tasks/{id}`

Fetch a single task by id.

```bash
curl http://127.0.0.1:8787/api/tasks/TASK-001
```

Returns `404` with `{"error": "not found"}` for an unknown id.

### `POST /api/tasks`

Create a new task. The request body must be a JSON object with a non-blank
`title`; `description` (string) and `acceptance_criteria` (array of strings)
are optional. The server generates the id as the next free `WEB-###` id and
stamps `created_at`/`updated_at`.

```bash
curl -X POST http://127.0.0.1:8787/api/tasks \
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

Returns `201` with the created task. Errors:

- `400` with `{"error": "..."}` — missing/blank `title`, unparseable or
  non-object body, or a field of the wrong type.
- `409` with `{"error": "..."}` — the generated id already exists in the
  backlog (e.g. two concurrent requests raced).

### `GET /api/status`

Daemon status: PID, name, interval, last outcome, and the next scheduled run.

```bash
curl http://127.0.0.1:8787/api/status
```

```json
{
  "pid": 4242,
  "name": "my-factory",
  "interval_minutes": 30,
  "last_outcome": "task",
  "next_run_at": "2026-08-01T12:00:00+00:00"
}
```

`next_run_at` is `null` when the daemon is not available.

### `GET /api/config`

The resolved factory config as JSON.

```bash
curl http://127.0.0.1:8787/api/config
```

### `GET /api/logs?lines=N`

The last `N` lines of the factory log (`N` defaults to `100`, max `10000`).

```bash
curl http://127.0.0.1:8787/api/logs
curl "http://127.0.0.1:8787/api/logs?lines=50"
```

### `GET /api/runs?limit=N`

The durable run history from `runs.jsonl`, newest first (`limit` defaults to
`10`, max `10000`). Each record has started/finished timestamps, the run kind
(`task` or `refactor`), the task id and title when applicable, the outcome
(`SUCCESS` / `BLOCKED` / `ERROR`), the agent exit code, the commit SHA when a
commit was created, and the duration in seconds.

```bash
curl http://127.0.0.1:8787/api/runs
curl "http://127.0.0.1:8787/api/runs?limit=5"
```

### `GET /api/blocker`

The current `BLOCKER.md` contents, or `{"content": null}` when no blocker file
exists.

```bash
curl http://127.0.0.1:8787/api/blocker
```

## Static files

Any other path is served from `src/factory/web/` (a path traversal attempt is
rejected and returns `404`). The web console UI lives there:

- `index.html` — the dashboard page: a header with the factory name, pid,
  interval, next run, and last outcome (from `/api/status`), a small form to
  add a task to the backlog, the backlog as a card list grouped under **OPEN /
  BLOCKED / COMPLETED / FAILED** headings (id, title, description,
  created/updated timestamps), and a footer with the fetch time.
- `style.css` — dark theme, single accent color, system fonts only.
- `app.js` — fetches `/api/tasks` and `/api/status`, re-renders in place every
  30 seconds (never a full page reload), shows an empty state when the backlog
  has no tasks, a discreet notice when the daemon is unreachable, and posts
  the new-task form to `/api/tasks` (appending the created task to the board
  and clearing the form on success, showing an inline error otherwise).

The page is fully self-contained: plain HTML/CSS/JS, no framework, no build
step, no external assets or CDNs. It is served at `/` as `text/html`.

## Errors

- `400` — malformed `POST /api/tasks` body (missing/blank title, unparseable
  body, wrong field types).
- `404` — unknown API path, unknown task, or missing static file.
- `409` — `POST /api/tasks` id collision (a concurrent request won the race).
- `500` — an unexpected handler error (logged server-side).

## Example: a status one-liner

```bash
curl -s http://127.0.0.1:8787/api/status
```

## Security notes

- The server binds `web_host` (default `127.0.0.1`). Exposing it on a
  network (`web_host: 0.0.0.0`) makes the API visible to every host that can
  reach the port — only do that on a trusted LAN.
- The only write endpoint is `POST /api/tasks`, which appends to the local
  backlog. A machine that can reach the port can add tasks to the queue.
- Set `web_port: 0` to disable it if you do not need it.

---

# Central dashboard (`factory web`)

With several factories registered in the instance registry (each
`factory start`/`factory stop` registers its config automatically, or use
`factory instance add NAME --config PATH`), each daemon still serves its own
embedded dashboard on its own `web_port` — but there is no overview, and
every daemon defaults to the same port `8787`. Run the **central dashboard**
to get one aggregate view of every instance:

```bash
factory web               # default 0.0.0.0:8790
factory web --port 9000   # pick a different port
factory web --host 127.0.0.1
```

It runs in the foreground like `factory start`. Unlike the embedded
dashboard, it does **not** talk to the daemons: it reads every registered
instance's data straight from its files (`backlog.json`, `runs.jsonl`,
`factory.log`, `BLOCKER.md`), so it works whether or not each instance's
daemon is running.

## Pages

- `GET /` — home page listing every registered instance: name, repository,
  daemon state (lock held), last outcome, next run, and per-status backlog
  counts, each linking to its instance page.
- `GET /instances/<name>/` — one instance's page: the same kanban backlog
  view as the embedded dashboard plus its daemon status, with tabs for
  **logs**, **runs**, **blocker** and **config**.
- `GET /style.css`, `/central/central.js`, `/central/central.css` — the
  shared dark theme and dashboard scripts (no frameworks).

## Central API

All per-instance endpoints are read-only and mirror the embedded daemon's
endpoints, but are served under `/api/instances/<name>/`.

### `GET /api/instances`

A JSON list of every registered instance with its repo, daemon state, last
outcome, next run, and backlog counts.

```bash
curl http://127.0.0.1:8790/api/instances
```

### `GET /api/instances/<name>/tasks`

List every task in that instance's backlog, in creation order.

### `GET /api/instances/<name>/tasks/{id}`

Fetch a single task; `404` for an unknown id.

### `GET /api/instances/<name>/status`

Daemon status read from the files: name, repo, interval, `daemon_running`,
the recorded lock PID, `last_outcome` (from `runs.jsonl`), and an estimated
`next_run_at` (the last run's finish plus the interval, only when the daemon
is running).

```bash
curl http://127.0.0.1:8790/api/instances/my-repo/status
```

### `GET /api/instances/<name>/config`

The resolved `factory.yaml` as JSON.

### `GET /api/instances/<name>/logs?lines=N`

The last `N` lines of that instance's `factory.log` (`N` defaults to `100`,
max `10000`).

### `GET /api/instances/<name>/runs?limit=N`

That instance's run history from `runs.jsonl`, newest first (`limit`
defaults to `10`, max `10000`).

### `GET /api/instances/<name>/blocker`

The instance's `BLOCKER.md` contents, or `{"content": null}` when none
exists.

## Behavior

- An unknown instance name returns `404` (`{"error": "unknown instance"}`).
- A registered instance with missing data files renders with empty data and
  `daemon_running=false` instead of erroring — the instance page and every
  API endpoint still return `200`.
- Everything stays read-only: the central dashboard never writes to any
  instance's files.
- The embedded per-daemon dashboard keeps working unchanged on each
  instance's own `web_port`.
