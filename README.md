# Software Factory

Pulls tasks from a backlog, executes them with pluggable coding agents (Claude Code, Aider, AutoGen, scripts), and asks a human when an agent gets stuck.

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

## How it works

Everything is an adapter behind a tiny async interface (`src/factory/core/orchestrator.py`):

| Piece        | Interface                 | Built-in implementations                    |
| ------------ | ------------------------- | ------------------------------------------- |
| Task source  | `BaseBacklogAdapter`      | `JSONBacklogAdapter` (GitHub/Jira: implement 2 methods) |
| Agent        | `BaseAgentAdapter`        | `MockAgentAdapter`, `ShellAgentAdapter`     |
| Human channel| `BaseFeedbackProvider`    | `ConsoleFeedbackProvider`, `WebhookFeedbackProvider` |

A task's lifecycle is always the same: `OPEN → IN_PROGRESS → COMPLETED`, or `→ BLOCKED →` human input `→` retry (bounded) `→` success, or `→ FAILED`. Every step is logged as a comment on the task.

## Extend

Implement one abstract class, wire it in, done:

```python
Orchestrator(backlog=..., agent=..., feedback=..., context=..., max_retries=3)
```

## Develop

```bash
pytest   # 30 tests: state machine, adapters, models
```
