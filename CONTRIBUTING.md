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

## Releasing

Releases are cut from `main` and published as GitHub Releases. Tagging the
repo triggers CI, which builds the wheel and sdist and attaches them to the
release — there is no PyPI publishing yet, and `install.sh` installs directly
from git.

1. Confirm the [quality gates](#quality-gates) are green on `main`.
2. Bump the version in `pyproject.toml` (`version = "x.y.z"`), following
   [Semantic Versioning](https://semver.org/).
3. Update `CHANGELOG.md`: move the entries from `## [Unreleased]` under a new
   `## [x.y.z] - <date>` section, add the compare links at the bottom, and
   leave a fresh `## [Unreleased]` heading.
4. Commit the bump and changelog update, e.g. `git commit -m "Release x.y.z"`.
5. Tag and push the tag — the `release` job in `.github/workflows/ci.yml`
   builds the wheel and sdist and attaches them to a GitHub Release:

   ```bash
   git tag vx.y.z
   git push origin vx.y.z
   ```

6. Confirm the release and its artifacts are listed under
   <https://github.com/lucaGazzola/forgeo/releases>.

## License

This project is MIT-licensed; see [LICENSE](LICENSE).
