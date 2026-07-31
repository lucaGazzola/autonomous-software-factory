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
