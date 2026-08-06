# CLI reference

All commands read `factory.yaml` from the current directory; pass
`--config <file>` to use a different one. `start`, `once`, `status`, `stop`
and `restart` also accept `--name <instance>` to resolve the config from the
**instance registry** — see [`factory instance`](#factory-instance) below.

```
factory --version
factory <command> --help
```

Bare `factory` (no subcommand) starts the guided wizard when no config exists,
and prints the CLI help once a config is present.

## `factory init`

Guided first-time setup: interactively write a `factory.yaml`.

| Flag | Description |
| --- | --- |
| `--config <file>` | Where to write the config (default `factory.yaml`). |
| `--force` | Overwrite an existing config file. |

Exit codes:

- `0` — config written.
- `2` — a config already exists and `--force` was not given.
- `130` — setup aborted; nothing was written.

See [Getting Started](getting-started.md) for what the wizard asks.

## `factory start`

Start the scheduled factory daemon for a repository. Runs in the foreground;
interrupt with Ctrl-C or stop from another terminal with `factory stop`.

| Flag | Description |
| --- | --- |
| `--config <file>` | Factory YAML file (default `factory.yaml`). Mutually exclusive with `--name`. |
| `--name <name>` | Registered instance name resolved from the registry. Mutually exclusive with `--config`. |
| `--interval-minutes <n>` | Override the schedule interval from the config for this run. |

The daemon wakes every `interval_minutes` and runs one cycle. When no config
exists, `factory start` offers the guided setup. A second `start` (or `once`)
is refused while the per-factory lock is held.

While running it serves a local web dashboard at `http://<web_host>:<web_port>`
(see [Web console & HTTP API](web-console-api.md)) and logs to `log_file`.

## `factory once`

Run exactly **one cycle** and exit; no daemon needed.

| Flag | Description |
| --- | --- |
| `--config <file>` | Factory YAML file (default `factory.yaml`). Mutually exclusive with `--name`. |
| `--name <name>` | Registered instance name resolved from the registry. Mutually exclusive with `--config`. |

`factory once` shares the run lock with the daemon, so it never overlaps a
running `factory start` — useful to test a config or process a backlog without
leaving a daemon up. On success it prints `Cycle finished: <outcome>`.

Outcomes a cycle can produce:

| Outcome | Meaning |
| --- | --- |
| `task` | A task ran and finished. |
| `refactor` | A refactoring pass ran (backlog was empty). |
| `blocked` | A `BLOCKED` task exists; `BLOCKER.md` was rewritten; paused. |
| `paused` | A blocker file exists; nothing ran. |
| `dirty` | The working tree was dirty; the task was not started. |
| `skipped` | A previous run was still in progress (daemon only). |
| `error` | A cycle crashed (daemon only). |

## `factory status`

Print a read-only summary of the factory. Never starts an agent.

| Flag | Description |
| --- | --- |
| `--config <file>` | Factory YAML file (default `factory.yaml`). Mutually exclusive with `--name`. |
| `--name <name>` | Registered instance name resolved from the registry. Mutually exclusive with `--config`. |

Output:

```
name: my-factory
repo: /path/to/repo
interval: 30 min
branch: main
backlog: OPEN=2 BLOCKED=1 COMPLETED=5 FAILED=0
next: TASK-001 — First open
daemon: not running
last outcome: task
```

- `backlog` — per-status task counts.
- `next` — the oldest `OPEN` task (the one the factory will pick next).
- `daemon` — whether the per-factory lock is currently held.
- `last outcome` — the most recent run recorded in `runs.jsonl`.

## `factory stop`

Stop a running daemon gracefully (SIGTERM; a cycle in progress finishes
first).

| Flag | Description |
| --- | --- |
| `--config <file>` | Factory YAML file (default `factory.yaml`). Mutually exclusive with `--name`. |
| `--name <name>` | Registered instance name resolved from the registry. Mutually exclusive with `--config`. |
| `--timeout <seconds>` | How long to wait for the daemon to exit (default `600`). |

Exit code is `0` on success, `1` when the factory is not running, the lock
records a dead PID, or the daemon did not exit within the timeout.

## `factory restart`

Stop the daemon when running, then start it again **in the background**
(detached), re-reading `factory.yaml`.

| Flag | Description |
| --- | --- |
| `--config <file>` | Factory YAML file (default `factory.yaml`). Mutually exclusive with `--name`. |
| `--name <name>` | Registered instance name resolved from the registry. Mutually exclusive with `--config`. |
| `--timeout <seconds>` | How long to wait for the old daemon to exit (default `600`). |

On success it prints the new daemon PID and interval.

### `--config` vs `--name`

On `start`, `once`, `status`, `stop` and `restart`, `--name` resolves the
`factory.yaml` from the instance registry instead of reading `--config`. The
two flags are mutually exclusive — passing both is an argparse error. An
unknown instance name prints a clear error and exits non-zero.

## `factory instance`

Register, list, and unregister named factory instances. Instances live in a
registry file — `$FORGEO_REGISTRY` or `~/.config/forgeo/instances.yaml` — that
maps each name to the absolute path of its `factory.yaml` (see
[Configuration](configuration.md#instance-registry)).

### `factory instance add <name> --config <file>`

Register an existing `factory.yaml` under a stable name.

| Flag | Description |
| --- | --- |
| `--config <file>` | Path to the `factory.yaml` to register. **Required.** |

- The name must match `^[a-zA-Z0-9._-]+$`; invalid or duplicate names are
  rejected with a clear error (exit `1`).
- The config is validated (it must load) before registering.
- Relative config paths are stored as absolute paths.

### `factory instance rm <name>`

Unregister an instance. Never touches the config file or the repository; a
missing instance prints an error and exits `1`.

### `factory instance list` / `factory list`

List every registered instance as a table: name, config path, repository,
daemon state (running/stopped), last outcome (from `runs.jsonl`), and backlog
counts. `factory list` is a direct alias for `factory instance list`. With no
registered instances it prints a hint and exits `0`.

## `factory web`

Serve the **central multi-instance dashboard** in the foreground (like
`factory start`): one read-only page that aggregates every registered
instance. It reads each instance's data straight from its files
(`backlog.json`, `runs.jsonl`, `factory.log`, `BLOCKER.md`), so it works
whether or not each instance's daemon is running.

| Flag | Description |
| --- | --- |
| `--host <address>` | Bind address (default `0.0.0.0`). |
| `--port <port>` | Bind port (default `8790`). |

- `GET /` — home page listing every registered instance (daemon state, last
  outcome, next run, backlog counts).
- `GET /instances/<name>/` — one instance's page: its kanban backlog plus
  tabs for logs, runs, blocker, and config.

See [Web console & HTTP API](web-console-api.md) for the full API and the
per-daemon dashboard served by `factory start`.

## Process checks

```bash
pgrep -af factory    # process check; empty output = not running
```
