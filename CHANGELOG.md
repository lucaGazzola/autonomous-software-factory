# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-04

### Added

- `CHANGELOG.md` in Keep a Changelog format, with the `0.1.0` history
  backfilled.
- Tag-triggered CI job that builds the wheel and sdist and attaches them to a
  GitHub Release.
- Release steps documented in `CONTRIBUTING.md`.
- Web console frontend in `src/factory/web/`: a self-contained
  HTML/CSS/JS dashboard (no framework, no build step, no external assets)
  served at `/` showing the backlog grouped by status and daemon status,
  auto-refreshing every 30 seconds.
- `install.sh` is now hosted on the project's own server and served from
  <https://forgeo.org/install.sh>; README and docs use it in the one-liner.

## [0.1.0] - 2026-08-03

Initial release of the scheduled, agent-driven software factory.

### Added

- `factory start` persistent daemon: every `interval_minutes` it picks the
  oldest `OPEN` task, runs it through the configured agent command, and commits
  and pushes the result directly on `main`.
- Refactoring mode: when the backlog is empty, runs the agent with the
  configured `refactor_prompt`.
- Blocker flow: an agent exiting with `blocked_exit_code` commits partial work,
  writes `BLOCKER.md`, and pauses the factory until the task is reopened.
- Guided first-time setup: `factory init` wizard.
- `factory once` command to run a single cycle and exit.
- `factory status`, `factory stop`, and `factory restart` commands.
- `--auto` flag for the agent command for unattended runs.
- Local web dashboard and HTTP API served by the daemon.
- Durable run history recorded to `runs.jsonl` and exposed through the API.
- Telegram notification when a task is marked `BLOCKED`.
- Curl-to-bash one-liner installer (`install.sh`), pipx-first.
- MkDocs documentation website, published at <https://forgeo.org/>.
- GitHub Actions CI running `pytest`, `ruff`, and `mypy` on Python 3.11-3.13.
- Optional Docker sandbox for agent execution.
- Per-task `agent_command` override for cheap/expensive model routing.
- Project renamed to Forgeo, with MIT `LICENSE` and `CONTRIBUTING.md`.

### Changed

- Project slimmed down to a single-purpose scheduled worker; the interactive
  backlog generator utility was removed.
- Duplicated commit/blocker handling unified between task and refactor runs.
- Agent stdout/stderr streamed into run logs instead of buffered.
- Corrupt backlog files preserved instead of silently discarded.
- Git command timeout made configurable; agent timeout made optional with
  overlapping-run skipping.
- Dogfooding docs removed; local configs kept out of the repository.

[Unreleased]: https://github.com/lucaGazzola/forgeo/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/lucaGazzola/forgeo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/lucaGazzola/forgeo/releases/tag/v0.1.0
