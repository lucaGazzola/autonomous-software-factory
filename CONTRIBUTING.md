# Contributing to Forgeo

Thanks for contributing! This project is an agent-driven software factory.

## Development setup

Requires Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

`.[dev]` installs the package plus the toolchain: `pytest`, `ruff`, and
`mypy`.

## Quality gates

Run all three before opening a PR. CI enforces the same gates.

```bash
pytest           # the test suite (tests/)
ruff check       # linting (src, tests)
mypy src/factory # type checking
```

The full suite should pass and `ruff check` and `mypy src/factory` should be
clean.

## Writing a backlog task

Backlog tasks are how the factory receives work. Tasks are JSON objects
in the backlog file (see [docs/backlog.md](docs/backlog.md) for the full
schema). A good task has three things:

- **`id`** — a unique identifier, e.g. `TASK-001`. Duplicate ids are rejected.
- **`description`** — a self-contained specification handed to the agent. State
  the current behavior, the desired behavior, and where the change lives. The
  agent does not have your mental context, so spell it out.
- **`acceptance_criteria`** — a list of concrete, verifiable outcomes. The
  factory renders these into the agent's `FACTORY_TASK` instruction, so write
  them as checks the agent can confirm itself (e.g. "`pytest` passes", "the
  `--help` output lists `once`").

Example:

```json
{
  "id": "TASK-002",
  "title": "Add `factory once` command to run a single cycle",
  "description": "The CLI only offers `factory start` (the persistent daemon). Add a `once` subcommand that runs exactly one cycle and exits.",
  "acceptance_criteria": [
    "`factory once --config factory.yaml` runs one cycle and exits 0",
    "`factory --help` lists `once`",
    "Tests cover the new command"
  ],
  "status": "OPEN",
  "created_at": "2026-07-31T20:01:00Z"
}
```

Keep the description scoped to one task, keep acceptance criteria minimal and
testable, and never commit the backlog file — it is gitignored.

## Pull-request process

Human contributions use the normal GitHub flow:

1. Create a feature branch off `main`: `git checkout -b feat/my-change`.
2. Make your change and commit it with a concise, descriptive message.
3. Run the [quality gates](#quality-gates) and fix any failures.
4. Push the branch and open a pull request against `main`.
5. CI runs `pytest`, `ruff check`, and `mypy src/factory` on the PR — it must
   be green.
6. Address review feedback; keep the branch rebased on `main` if it drifts.
7. Once approved and green, merge. Follow-up work is welcome as a new PR or as
   a backlog task for the factory.

## License

This project is MIT-licensed; see [LICENSE](LICENSE).
