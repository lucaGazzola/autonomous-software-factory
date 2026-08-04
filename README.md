# Forgeo

[![CI](https://github.com/lucaGazzola/forgeo/actions/workflows/ci.yml/badge.svg)](https://github.com/lucaGazzola/forgeo/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Forgeo is an autonomous software factory for people with ideas, not teams.**
You have a product idea — an app, a website, an internal automation — but no
developers on staff. With Forgeo you don't need any: you write down what needs
to be built as a simple list of tasks, and an AI coding agent works through the
list on its own, writing the code and committing it to your repository. No
branches, no pull requests, no developer to hire.

All you need is basic comfort with a terminal, a git repository, and any coding
agent CLI — Claude Code, aider, opencode, or your own script. Forgeo works with
all of them.

Forgeo decides what to do next on its own: while tasks are left it implements
the oldest one and commits the result, and when the backlog is empty it reviews
the codebase and cleans it up. It only interrupts you when a decision is
genuinely yours to make — everything else happens autonomously.

## Quickstart

Requires Python 3.11+ and a git repository. The full walkthrough is in
[Getting started](docs/getting-started.md).

```bash
# 1. Install
curl -fsSL https://forgeo.org/install.sh | bash

# 2. Create your factory (guided wizard, run from your project root)
factory init

# 3. Start the factory
factory start   # run forever: every interval_minutes, implement the oldest OPEN task
```

`factory init` writes `factory.yaml` and a `.factory/` folder for the backlog
and logs, gitignored for you. Fill the backlog with plain JSON tasks (see
[Backlog format](docs/backlog.md)) and the factory does the rest — serving a
local dashboard at <http://127.0.0.1:8787> while it runs:

![Forgeo web console](docs/img/console.png)

One-off commands: `factory once` (single cycle), `factory status` (summary),
`factory stop`, `factory restart` — every command is in the
[CLI reference](docs/cli-reference.md).

## Documentation

| Topic | Where |
| --- | --- |
| Install, init, first cycle | [Getting started](docs/getting-started.md) |
| Every `factory.yaml` key | [Configuration](docs/configuration.md) |
| Task schema and statuses | [Backlog format](docs/backlog.md) |
| How the agent is invoked (env, exit codes, timeouts) | [Agent contract](docs/agent-contract.md) |
| All CLI commands | [CLI reference](docs/cli-reference.md) |
| Web dashboard & HTTP API | [Web console & HTTP API](docs/web-console-api.md) |

Everything is stored in plain files: the backlog, `factory.log`, and
`BLOCKER.md` whenever a decision is pending.

## Develop

```bash
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, quality
gates (`pytest`, `ruff check`, `mypy src/factory`), and the pull-request
process.

## License

MIT — see [LICENSE](LICENSE).
