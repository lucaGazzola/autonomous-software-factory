# Web console & HTTP API

While the daemon is running, it serves a **read-only local web dashboard and
HTTP API** at `http://127.0.0.1:<web_port>` (default port `8787`). The server
binds **`127.0.0.1` only** — it is not reachable from other machines.

- Disable it entirely with `web_port: 0` in `factory.yaml`.
- A bind failure is logged and the daemon continues without the API.
- Everything is read-only: the API never modifies the backlog, the repository,
  or the daemon.

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

### `GET /api/blocker`

The current `BLOCKER.md` contents, or `{"content": null}` when no blocker file
exists.

```bash
curl http://127.0.0.1:8787/api/blocker
```

## Static files

Any other path is served from `src/factory/web/` (a path traversal attempt is
rejected and returns `404`). This is where a web console UI would live; the
directory ships empty.

## Errors

- `404` — unknown API path, unknown task, or missing static file.
- `500` — an unexpected handler error (logged server-side).

## Example: a status one-liner

```bash
curl -s http://127.0.0.1:8787/api/status
```

## Security notes

- The server binds `127.0.0.1` only.
- It is read-only — there are no write endpoints.
- Set `web_port: 0` to disable it if you do not need it.
