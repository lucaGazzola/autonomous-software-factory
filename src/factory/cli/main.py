"""Command-line interface: ``factory init``, ``factory run``, and ``factory generate-backlog``.

``factory init`` seeds a JSON backlog with sample tasks for a dry run.
``factory run`` executes the orchestrator end to end: pull tasks from the
backlog, run them through the configured agent adapter, route blocked
tasks through the human-in-the-loop channel, and report final state.
``factory generate-backlog`` interviews a raw product idea and writes a
validated, machine-executable backlog through the ``JSONBacklogAdapter``.

Both run commands read defaults from ``config/factory.yaml`` when present;
explicit CLI flags always win. Run ``python -m factory run --help`` for
the full option list.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from factory import __version__
from factory.adapters.agents import MockAgentAdapter, ShellAgentAdapter
from factory.adapters.backlog import JSONBacklogAdapter
from factory.adapters.feedback import ConsoleFeedbackProvider, WebhookFeedbackProvider
from factory.core.models import AgentConfig, RepoContext, Task
from factory.core.orchestrator import Orchestrator

DEFAULT_CONFIG = Path("config/factory.yaml")
DEFAULT_BACKLOG = Path("backlog.json")

console = Console()

#: (id, title, description, metadata) — used to seed a fresh backlog.
SAMPLE_TASKS: list[tuple[str, str, str, dict[str, Any]]] = [
    (
        "TASK-001",
        "Implement fibonacci module",
        "Write a fibonacci module with memoization and a small test suite.",
        {"simulate": "success"},
    ),
    (
        "TASK-002",
        "Add retry logic to HTTP client",
        "Add exponential backoff retries to the HTTP client; the agent needs the operator to pick a retry policy.",
        {"simulate": "blocked"},
    ),
    (
        "TASK-003",
        "Bump dependency versions",
        "Upgrade all pinned dependencies to their latest compatible versions.",
        {"simulate": "error"},
    ),
]


# --------------------------------------------------------------------- #
# Parsing and configuration                                             #
# --------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="factory",
        description="Agent-agnostic Software Factory: automate software development "
        "from a backlog with pluggable agents and human-in-the-loop fallback.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="action", required=True)

    init_parser = sub.add_parser("init", help="Create a backlog file seeded with sample tasks.")
    init_parser.add_argument("--backlog", type=Path, default=DEFAULT_BACKLOG, help="Backlog file path.")

    run_parser = sub.add_parser("run", help="Run the orchestrator over a backlog.")
    run_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="YAML config file (optional).")
    run_parser.add_argument("--backlog", type=Path, default=None, help="Backlog file path.")
    run_parser.add_argument("--agent", choices=("mock", "shell"), default=None, help="Agent adapter to use.")
    run_parser.add_argument("--command", default=None, help="Shell command for the shell agent (string or JSON argv list).")
    run_parser.add_argument("--timeout", type=float, default=None, help="Agent timeout in seconds.")
    run_parser.add_argument("--env", action="append", default=None, metavar="KEY=VALUE", help="Extra env var for the agent (repeatable).")
    run_parser.add_argument("--repo", type=Path, default=None, help="Repository path given to agents.")
    run_parser.add_argument("--branch", default=None, help="Branch given to agents.")
    run_parser.add_argument("--max-retries", type=int, default=None, help="Retries allowed after a task blocks.")
    run_parser.add_argument("--feedback", choices=("console", "webhook"), default=None, help="Feedback provider.")
    run_parser.add_argument("--webhook-url", default=None, help="URL for the webhook feedback provider.")
    run_parser.add_argument("--delay", type=float, default=None, help="Mock agent artificial latency (seconds).")
    run_parser.add_argument("--once", action="store_true", help="Process a single task and exit.")

    gen_parser = sub.add_parser(
        "generate-backlog", help="Interview a product idea and generate an executable backlog."
    )
    gen_parser.add_argument("--prompt", "-p", default=None, help="Initial product idea (prompted if omitted).")
    gen_parser.add_argument("--output", "-o", type=Path, default=DEFAULT_BACKLOG, help="Backlog file to write.")
    gen_parser.add_argument("--model", default=None, help="LLM model (overrides FACTORY_LLM_MODEL).")
    gen_parser.add_argument("--force", action="store_true", help="Replace an existing backlog file.")
    return parser


def load_config(path: Path) -> dict[str, Any]:
    """Load a YAML config file, tolerating absence."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


#: Mapping from CLI/config option name to its dotted key in factory.yaml.
CONFIG_KEYS: dict[str, str] = {
    "backlog": "backlog.path",
    "agent": "agent.name",
    "command": "agent.command",
    "timeout": "agent.timeout_seconds",
    "env": "agent.env",
    "repo": "repo.path",
    "branch": "repo.branch",
    "max_retries": "orchestrator.max_retries",
    "feedback": "feedback.provider",
    "webhook_url": "feedback.webhook_url",
}


def parse_env(value: Any) -> dict[str, str]:
    """Normalize env values from config (dict) or CLI (``KEY=VALUE`` list)."""
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    env: dict[str, str] = {}
    for item in value or []:
        key, _, val = str(item).partition("=")
        env[key] = val
    return env


def merge_config(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Merge CLI flags over config-file defaults (CLI wins)."""
    defaults = {
        "backlog": DEFAULT_BACKLOG,
        "agent": "mock",
        "command": None,
        "timeout": 300.0,
        "env": {},
        "repo": Path("."),
        "branch": "main",
        "max_retries": 3,
        "feedback": "console",
        "webhook_url": None,
        "delay": 0.0,
    }

    def pick(name: str) -> Any:
        """Resolve a value: CLI flag > config file > built-in default."""
        cli_value = getattr(args, name, None)
        if cli_value is not None:
            return cli_value
        key = CONFIG_KEYS.get(name)
        if key is not None:
            value: Any = cfg
            for part in key.split("."):
                value = value.get(part) if isinstance(value, dict) else None
                if value is None:
                    break
            if value is not None:
                return value
        return defaults[name]

    return {
        "backlog": Path(pick("backlog")),
        "agent": pick("agent"),
        "command": pick("command"),
        "timeout": float(pick("timeout")),
        "env": parse_env(pick("env")),
        "repo": Path(pick("repo")),
        "branch": pick("branch"),
        "max_retries": int(pick("max_retries")),
        "feedback": pick("feedback"),
        "webhook_url": pick("webhook_url"),
        "delay": float(pick("delay")),
    }


# --------------------------------------------------------------------- #
# Commands                                                               #
# --------------------------------------------------------------------- #


async def seed_backlog(path: Path) -> None:
    """Create a backlog file with the sample tasks if it does not exist."""
    if path.exists():
        return
    adapter = JSONBacklogAdapter(path)
    for task_id, title, description, metadata in SAMPLE_TASKS:
        await adapter.create_task(
            Task(id=task_id, title=title, description=description, metadata=metadata)
        )
    console.print(f"[green]Seeded[/green] {path} with {len(SAMPLE_TASKS)} sample tasks.")


def cmd_init(args: argparse.Namespace) -> int:
    """Handle ``factory init``."""
    asyncio.run(seed_backlog(args.backlog))
    return 0


def build_agent(name: str, opts: dict[str, Any]):
    """Instantiate the configured agent adapter."""
    if name == "shell":
        command = opts["command"]
        if command is None:
            console.print("[red]The shell agent requires --command (or agent.command in the config).[/red]")
            raise SystemExit(2)
        if isinstance(command, str) and command.lstrip().startswith("["):
            command = list(yaml.safe_load(command))
        return ShellAgentAdapter(
            AgentConfig(
                command=command,
                env=opts["env"],
                timeout_seconds=opts["timeout"],
            )
        )
    return MockAgentAdapter(delay_seconds=opts["delay"])


def build_feedback(name: str, opts: dict[str, Any]):
    """Instantiate the configured feedback provider."""
    if name == "webhook":
        return WebhookFeedbackProvider(url=opts["webhook_url"])
    return ConsoleFeedbackProvider(console=console)


def cmd_run(args: argparse.Namespace) -> int:
    """Handle ``factory run``."""

    async def _run() -> int:
        cfg = merge_config(load_config(args.config) if args.config else {}, args)
        backlog = JSONBacklogAdapter(cfg["backlog"])
        await seed_backlog(cfg["backlog"])

        agent = build_agent(cfg["agent"], cfg)
        feedback = build_feedback(cfg["feedback"], cfg)
        context = RepoContext(repo_path=cfg["repo"], branch=cfg["branch"])
        orchestrator = Orchestrator(
            backlog=backlog,
            agent=agent,
            feedback=feedback,
            context=context,
            max_retries=cfg["max_retries"],
        )

        console.print(
            Panel.fit(
                f"[bold]Backlog:[/bold] {cfg['backlog']}  "
                f"[bold]Agent:[/bold] {agent.name}  "
                f"[bold]Feedback:[/bold] {feedback.name}",
                title="Software Factory",
                border_style="blue",
            )
        )

        processed = 1 if args.once and await orchestrator.run_once() is not None else await orchestrator.run_until_idle()

        await _render_summary(orchestrator, backlog, processed)
        return 0

    return asyncio.run(_run())


async def _render_summary(orchestrator: Orchestrator, backlog: JSONBacklogAdapter, processed: int) -> None:
    """Print final task states and execution stats as Rich tables."""
    tasks = await backlog.list_tasks()

    state_table = Table(title=f"Final backlog state ({processed} task(s) processed)")
    state_table.add_column("ID", style="cyan")
    state_table.add_column("Title")
    state_table.add_column("Status", style="bold")
    for task in tasks:
        style = {
            "COMPLETED": "green",
            "FAILED": "red",
            "BLOCKED": "yellow",
            "IN_PROGRESS": "blue",
        }.get(task.status.value, "white")
        state_table.add_row(task.id, task.title, f"[{style}]{task.status.value}[/{style}]")
    console.print(state_table)

    stats = orchestrator.stats
    stats_table = Table(title="Execution stats")
    stats_table.add_column("Metric", style="bold")
    stats_table.add_column("Value")
    for label, value in (
        ("Tasks processed", stats.processed),
        ("Completed", stats.completed),
        ("Failed", stats.failed),
        ("Blocked (HITL triggered)", stats.blocked),
        ("Retries", stats.retries),
    ):
        stats_table.add_row(label, str(value))
    if stats.errors:
        stats_table.add_row("Errors", "\n".join(stats.errors))
    console.print(stats_table)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by both ``factory`` and ``python -m factory``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action == "init":
        return cmd_init(args)
    if args.action == "run":
        return cmd_run(args)
    if args.action == "generate-backlog":
        from factory.generator.cli import cmd_generate_backlog

        return cmd_generate_backlog(args)
    parser.error(f"unknown command: {args.action}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
