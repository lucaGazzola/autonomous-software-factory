# Configuration reference

The factory reads `factory.yaml` from the current directory (pass
`--config <file>` to any command to use a different one). The file is loaded
and validated on every invocation; relative paths resolve against the config
file's own directory, so a config can live anywhere and still point at sibling
directories.

The daemon reads `factory.yaml` **only at startup** — after editing the config
use `factory restart` so it re-reads the file.

## Keys

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | `forgeo` | Display name (logs, commit messages, Telegram notifications). |
| `repo` | `.` | The git repository the factory works on. |
| `interval_minutes` | `60` | How often the factory runs (≥ 1). |
| `branch` | `main` | The single branch everything is committed to. |
| `remote` | — | Remote to push to (e.g. `origin`); omit to only commit locally. |
| `backlog` | `backlog.json` | The task backlog JSON. Keep it outside the repo if you can. |
| `blocker_file` | `BLOCKER.md` | Where `BLOCKER.md` is written. Keep it outside the repo so it is never committed. |
| `agent_command` | — | The coding agent: any shell command (string) or argv list. **Required.** |
| `agent_timeout_seconds` | — | Optional: kill the agent after this many seconds (`null` = never). |
| `agent_env` | `{}` | Extra environment variables for the agent process. |
| `agent_sandbox` | `none` | Agent isolation: `none` (runs on the host) or `docker` (runs in a container). |
| `agent_sandbox_image` | — | Container image, **required when `agent_sandbox: docker`**; must contain the agent CLI and a shell. |
| `agent_sandbox_network` | `none` | Docker `--network` for the sandboxed agent (default `none` = networking disabled). |
| `agent_sandbox_mounts` | `[]` | Host paths mounted read-only into the sandboxed container (agent credentials/config). |
| `blocked_exit_code` | `2` | Exit code meaning "needs human input". |
| `refactor_prompt` | default refactor prompt | Instruction used when the backlog is empty. |
| `log_file` | `factory.log` | Where the daemon writes its log. |
| `web_host` | `127.0.0.1` | Web server bind address (`0.0.0.0` exposes it on the LAN). |
| `web_port` | `8787` | Local web dashboard / HTTP API port (`0` disables the server). |
| `git_timeout_seconds` | `120` | Kill a git subprocess after this many seconds. |
| `telegram_bot_token` | — | Telegram bot token for blocked-run notifications (disabled unless `telegram_chat_id` is also set). |
| `telegram_chat_id` | — | Chat ID that receives blocked-run notifications (disabled unless `telegram_bot_token` is also set). |

## Minimal example

```yaml
name: my-project
repo: .
interval_minutes: 30
branch: main

backlog: .factory/backlog.json
blocker_file: .factory/BLOCKER.md

agent_command: "claude -p \"$FACTORY_TASK\""
refactor_prompt: >
  Review the codebase for improvement opportunities that do not change
  behavior, run the test suite, and apply safe changes.
```

## Key details

### `agent_command`

Any shell command (string) or argv list. It is run with the repository as its
working directory and the task delivered via the `FACTORY_TASK` environment
variable. A string is executed with `sh -c`; an argv list is executed directly
without a shell. See [Agent contract](agent-contract.md).

```yaml
agent_command: "claude -p \"$FACTORY_TASK\""
# or, as an argv list (no shell involved):
agent_command: ["aider", "--message", "$FACTORY_TASK"]
```

### `agent_timeout_seconds`

When set, the agent process is killed after this many seconds and the task
fails. When `null` (the default) the agent runs to completion. A run that
overruns `interval_minutes` never kills anything — the next iteration simply
skips while the previous run is still active.

### `agent_env`

Extra environment variables merged into the agent process environment. They
are merged *over* the process environment but *under* the `FACTORY_*`
variables (which are set unconditionally).

```yaml
agent_env:
  OPENAI_API_KEY: sk-...
  MODEL: claude-sonnet-4
```

### `blocked_exit_code`

The exit code the agent uses to signal "I need a human decision" — see
[Agent contract](agent-contract.md) for what happens on that exit code.
Default `2`.

### `agent_sandbox`

Opt-in isolation for the agent process. Default `none` runs the command
directly on the host with the user's full privileges. Set `docker` to run it
inside `docker run --rm`:

- the repository is bind-mounted into the container at the same absolute path
  (edits land on the host checkout);
- `FACTORY_TASK`, the other `FACTORY_*` variables, and every `agent_env` key
  are passed through as container environment variables;
- networking is disabled by default (`--network none`); set
  `agent_sandbox_network` to e.g. `bridge` or `host` to re-enable it;
- nothing is mounted unless listed in `agent_sandbox_mounts` (host paths such
  as agent credentials/config, mounted read-only at the same path).

`agent_sandbox_image` is required in this mode and must already contain the
agent CLI used by `agent_command` plus a POSIX shell (`sh`) — nothing is
installed at run time. The exit-code contract (0 / `blocked_exit_code` /
other) is unchanged. The factory needs a working `docker` binary; a missing
binary makes `factory start` / `factory once` fail fast with a clear error.

```yaml
agent_sandbox: docker
agent_sandbox_image: forgeo-agent
agent_sandbox_network: none
agent_sandbox_mounts:
  - ~/.claude
  - ~/.config/claude
```

### `blocker_file`

Where the blocker file is written. Keep it **outside the repository** (the
factory pauses while this file exists, and it should not be committed).
Relative paths resolve against the config file's directory.

### `remote`

When set, successful commits are pushed to `<remote> <branch>`. When omitted,
the factory only commits locally. A push failure never discards the commit —
the work stays committed locally and the error is logged.

### `web_host`

Bind address of the web dashboard / HTTP API (default `127.0.0.1`, this
machine only). Set `0.0.0.0` to expose it on the local network. See
[Web console & HTTP API](web-console-api.md).

### `web_port`

Port for the web dashboard / HTTP API served while the daemon runs (default
`8787`). Set to `0` to disable the server. See
[Web console & HTTP API](web-console-api.md).

### Telegram notifications

Both `telegram_bot_token` **and** `telegram_chat_id` must be set for blocked
run notifications. A notification failure never changes the outcome of a
cycle — it is logged as a warning.

## Instance registry

Several factories can run side by side — one config per repository, each a
separate daemon. The **instance registry** maps a stable instance name to the
absolute path of that instance's `factory.yaml`, so the CLI can resolve a
config by name (`--name`) and a single command can enumerate every factory on
the host (`factory list` / `factory instance list`).

- **Location**: the file at `$FORGEO_REGISTRY`, or
  `~/.config/forgeo/instances.yaml` when the variable is unset.
- **Format**: a YAML mapping of instance name → absolute path of that
  instance's `factory.yaml`:

  ```yaml
  site-a: /home/me/projects/site-a/factory.yaml
  site-b: /home/me/projects/site-b/factory.yaml
  ```

- The file is created on the first registration — a `factory instance add`,
  or a `factory start`/`factory stop` whose config is not registered yet (it
  is registered under the config's `name`); a missing file reads as an empty
  registry. Writes are atomic (temp file + rename), so a crash mid-write
  never corrupts it.
- Names must match `^[a-zA-Z0-9._-]+$`; duplicates and unknown names are
  rejected with a clear error.
- `factory instance rm NAME` unregisters without touching the config file or
  the repository.

Manage instances with `factory instance add|rm|list` and `factory list` — see
[CLI reference](cli-reference.md).

## Per-instance isolation

Each registered instance is fully independent: every instance owns its own
**backlog** file, **logs** (`log_file`), **run history** (`runs.jsonl` next
to the backlog), **locks** (`backlog.lock` and the per-iteration run lock),
and its own **`web_port`** for the embedded dashboard. Because relative paths
resolve against each config file's own directory, two configs in different
directories can never share state.

When two daemons run on the same host and both use the embedded dashboard,
give each `factory.yaml` a **distinct `web_port`** — they all default to
`8787`, and a bind failure is logged while the daemon keeps running. The
central dashboard (`factory web`, default port `8790`) reads every instance's
data straight from its files, so it works regardless of each daemon's
`web_port` — see [Web console & HTTP API](web-console-api.md).

## Default refactor prompt

When `refactor_prompt` is omitted, the factory uses:

> Review the codebase for improvement opportunities that do not change
> behavior: dead code, duplication, overly complex functions, missing tests,
> outdated comments. Apply the safe improvements you find and run the test
> suite to verify nothing broke. If nothing needs refactoring, make no
> changes.
