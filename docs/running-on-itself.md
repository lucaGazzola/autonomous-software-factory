# Running the factory on itself

This repository dogfoods the factory: it runs the factory on the factory. The
`factory.yaml` at the repository root schedules the `autonomous-software-factory`
to work on its own codebase, commit on `main`, and push to `origin`.

## The dogfooding config

```yaml
name: autonomous-software-factory
repo: .
interval_minutes: 360
branch: main
remote: origin

backlog: .factory/backlog.json
blocker_file: .factory/BLOCKER.md

agent_command: >
  opencode run --auto "Work on the repository at the current working directory.
  Make the code changes requested below and nothing else. Do NOT run
  git commit, git push, or git add -A — the factory commits your work
  itself. Verify with the test suite where applicable.
  $FACTORY_TASK"
agent_timeout_seconds: 3600
agent_env: {}
blocked_exit_code: 2

refactor_prompt: |
  Review this codebase (the software factory itself) for improvement
  opportunities that do not change behavior: dead code, duplication,
  overly complex functions, missing tests, outdated comments. Apply the
  safe improvements you find, run `python -m pytest` to verify nothing
  broke, and make NO git commits — the factory commits your work. If
  nothing needs refactoring, make no changes.

log_file: .factory/factory.log
```

Every 6 hours (360 minutes) the factory wakes up and works on its own backlog.

## The agent command

The `agent_command` embeds the [agent contract](agent-contract.md) into the
prompt so the LLM agent behaves itself:

- it works on the current directory (the repo);
- it makes only the requested changes;
- it does **not** run `git commit`, `git push`, or `git add -A` — the factory
  commits its work itself;
- it verifies with the test suite where applicable;
- the actual task arrives as `$FACTORY_TASK`.

The 1-hour timeout (`agent_timeout_seconds: 3600`) gives a long agent run room
to finish without being killed.

## The backlog

The factory's own task list lives in `.factory/backlog.json` (gitignored, so it
is never committed). Tasks are added/edited the same way as any other factory
(see [Backlog format](backlog.md)), and the git history shows each one landing
as a commit prefixed with `factory:`.

## The blocker file

When the self-factory's agent needs a human decision it commits its partial
work and writes `.factory/BLOCKER.md`. The factory then pauses — it will not
run any more cycles until the blocker is resolved (see
[Backlog format](backlog.md) for the resolution steps).

## What it is for

Running the factory on its own codebase is the project's smoke test:

- new features and fixes are implemented as backlog tasks, executed by the
  same machinery documented on this site;
- every cycle exercises the full pipeline (backlog → agent → git → push),
  which is why this documentation site exists at all;
- refactoring passes keep the code tidy when the backlog is empty.
