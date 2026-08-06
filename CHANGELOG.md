# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `factory web [--host HOST] [--port PORT]` — a standalone central dashboard
  (default `0.0.0.0:8790`, foreground like `factory start`) that aggregates
  every registered instance. It reads each instance's data straight from its
  files (`backlog.json`, `runs.jsonl`, `factory.log`, `BLOCKER.md`), so it
  works whether or not that instance's daemon is running. Home page at `/`,
  per-instance pages at `/instances/<name>/` (kanban backlog plus logs,
  runs, blocker and config tabs), and a per-instance API under
  `/api/instances/<name>/` mirroring the embedded daemon's endpoints.
- Shared web-server helpers (`factory.web_common`) used by both the embedded
  per-daemon dashboard and the central dashboard.

## [0.2.1] - 2026-08-05

### Added

- `web_host` config option: the web dashboard/API bind address. Default
  `127.0.0.1` (unchanged behavior); set `0.0.0.0` to reach it from other
  hosts on the local network.
- `install.sh` now prefers a prebuilt standalone binary downloaded from the
  matching GitHub Release for the host OS/arch — **no Python required**.
  The pipx/pip fallback remains, used only when no prebuilt binary matches
  the platform and a Python >= 3.11 is available.
- Tag-triggered CI builds single-file executables with PyInstaller on
  Linux (amd64), macOS (amd64/arm64), and Windows (amd64) and attaches them
  to the GitHub Release (`forgeo.spec`).
- Installer tests cover the binary-download path and the pipx/pip fallback
  with stubs (no network).

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

[Unreleased]: https://github.com/lucaGazzola/forgeo/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/lucaGazzola/forgeo/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/lucaGazzola/forgeo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/lucaGazzola/forgeo/releases/tag/v0.1.0
