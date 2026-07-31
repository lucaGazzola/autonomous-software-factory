# Software Factory

Pulls tasks from a backlog, executes them with pluggable coding agents (Claude Code, Aider, AutoGen, scripts), and asks a human when an agent gets stuck. Run it as a one-off pass — or as a scheduled daemon that also refactors your code while the backlog is empty.

```
Backlog ──▶ Orchestrator ──▶ Agent ──▶ done
                │
                └── blocked? ──▶ Human (retry / reply / abort)
```

## Quick start

```bash
pip install -e ".[dev]"
factory init    # seeds backlog.json with 3 sample tasks
factory run     # processes them; TASK-002 pauses for human input (HITL demo)
```

Real agents need nothing more than a command:

```bash
factory run --agent shell --command "aider --message 'implement the task'"
```

Settings live in `config/factory.yaml`; CLI flags override them.

## Autonomous daemon

One project (one repository, one backlog) = one `project.yaml` (`config/project.example.yaml` has a fully commented template). The daemon wakes up on `schedule_interval_minutes` and:

1. finds `BLOCKED` tasks → alerts you once and pauses until you resolve the block (set the task back to `OPEN`),
2. otherwise drains every `OPEN` task through the orchestrator — each task on its own git branch (`factory/task-<id>`), committed and merged / pushed / PR'd on success per `git.strategy`,
3. and when the backlog is empty, runs a refactoring scan that *proposes* improvement tasks (LLM review + git/static analysis) for the next cycle — it never edits code itself.

```bash
factory start-daemon --config config/project.yaml   # persistent worker, logs to factory.log
factory run --project config/project.yaml           # single immediate pass over that repo
```

## Backlog generator

Have an idea but no backlog? `factory generate-backlog` interviews you with an
LLM "Product Architect", then turns the agreed spec into an ordered list of
executable tasks:

```bash
pip install -e ".[llm]"                  # LLM backend (litellm)
export FACTORY_LLM_MODEL=gpt-4o          # optional; defaults to gpt-4o
factory generate-backlog -p "File conversion web app"
```

The architect asks a few pointed questions (stack, storage, auth, edge cases...)
and always recommends an answer. Reply normally, or type `/done` (or "let's
build it") to finish early. Ctrl+C saves your interview so nothing is lost.

The resulting `backlog.json` feeds straight into `factory run`.

## How it works

Everything is an adapter behind a tiny async interface (`src/factory/core/orchestrator.py`):

| Piece        | Interface                 | Built-in implementations                    |
| ------------ | ------------------------- | ------------------------------------------- |
| Task source  | `BaseBacklogAdapter`      | `JSONBacklogAdapter` (GitHub/Jira: implement 2 methods) |
| Agent        | `BaseAgentAdapter`        | `MockAgentAdapter`, `ShellAgentAdapter`     |
| Human channel| `BaseFeedbackProvider`    | `ConsoleFeedbackProvider`, `WebhookFeedbackProvider`, `DeferredFeedbackProvider` (unattended daemon) |
| Git          | `GitManager`              | per-task branches, commit, merge / push / PR |

A task's lifecycle is always the same: `OPEN → IN_PROGRESS → COMPLETED`, or `→ BLOCKED →` human input `→` retry (bounded) `→` success, or `→ FAILED`. Every step is logged as a comment on the task.

## Extend

Implement one abstract class, wire it in, done:

```python
Orchestrator(backlog=..., agent=..., feedback=..., config=ProjectConfig(...), git_manager=GitManager("."))
```

## Develop

```bash
pytest   # 89 tests: state machine, git isolation, daemon cycles, adapters, models
```
