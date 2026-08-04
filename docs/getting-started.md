# Getting Started

This guide installs the `factory` CLI, initializes a config, and runs your
first backlog task.

## 1. Install

Requires **Python 3.11+**. Install the `factory` CLI from the public GitHub
remote with the one-liner:

```bash
curl -fsSL https://forgeo.org/install.sh | bash
```

The installer:

- prefers `pipx` and falls back to `pip install --user`;
- never needs root;
- re-running it upgrades the existing install (`pipx --force` /
  `pip --upgrade`).

If `pipx` is not installed, it warns when the user bin directory is not on
your `PATH` and tells you how to add it.

## 2. Initialize

Run the guided wizard from your project root:

```bash
factory init
```

The wizard asks for three things:

1. **Factory folder** — where the backlog, `BLOCKER.md` and the log live
   (default `.factory`). It is gitignored by default.
2. **Coding agent command** — any shell command that reads `$FACTORY_TASK` and
   works in the repository (default `aider --message "$FACTORY_TASK"`).
3. **Refactor prompt** — the instruction used when the backlog is empty; the
   default is offered, or you can paste a custom one.

`factory init` writes `factory.yaml`, creates the factory folder, and appends
`<folder>/` to `.gitignore` (unless you opt out).

```bash
factory init --force    # overwrite an existing factory.yaml
```

## 3. Create your first backlog

The backlog is a plain JSON file (see [Backlog format](backlog.md)). Create
the file configured as `backlog:` in your `factory.yaml` — by default
`.factory/backlog.json`:

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

!!! tip

    Hand this spec to your favorite LLM to generate the initial backlog for
    the application you want to build.

## 4. Start the factory

```bash
factory start
```

The daemon wakes up every `interval_minutes` and runs one cycle. It runs in
the foreground — interrupt it with Ctrl-C, or stop it from another terminal
with `factory stop`. A local web dashboard is served at
<http://127.0.0.1:8787> (see [Web console & HTTP API](web-console-api.md)):

![Forgeo web console](img/console.png)

## 5. Verify

```bash
factory status
```

shows the config, backlog counts, the next `OPEN` task, whether the daemon is
running, and the last run outcome. To run exactly one cycle without leaving a
daemon up:

```bash
factory once
```

## Next steps

- [Configuration reference](configuration.md) — every `factory.yaml` key.
- [Backlog format](backlog.md) — task schema and statuses.
- [Agent contract](agent-contract.md) — how the agent is invoked.
- [CLI reference](cli-reference.md) — all commands.
