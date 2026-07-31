"""Command-line interface: ``factory init``, ``factory run``, ``factory start-daemon``, and ``factory generate-backlog``.

``factory init`` seeds a JSON backlog with sample tasks for a dry run.
``factory run`` executes the orchestrator end to end: pull tasks from the
backlog, run them through the configured agent adapter, route blocked
tasks through the human-in-the-loop channel, and report final state. Pass
``--project`` to run a single immediate pass over the repository described
by a project config file (same isolation as the daemon).
``factory start-daemon`` launches the persistent, scheduled worker: it
wakes up on ``schedule_interval_minutes``, drains the backlog, and runs a
proactive refactoring scan when the backlog is empty. All daemon activity
is logged to ``factory.log`` so it can run unattended.
``factory generate-backlog`` interviews a raw product idea and writes a
validated, machine-executable backlog through the ``JSONBacklogAdapter``.

Both run commands read defaults from ``config/factory.yaml`` when present;
explicit CLI flags always win. Run ``python -m factory run --help`` for
the full option list.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
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
from factory.adapters.feedback import (
    ConsoleFeedbackProvider,
    DeferredFeedbackProvider,
    WebhookFeedbackProvider,
)
from factory.adapters.git_manager import GitManager
from factory.core.config import load_project_config
from factory.core.daemon import FactoryDaemon, acquire_run_lock
from factory.core.logging_setup import setup_logging
from factory.core.models import AgentConfig, ProjectConfig, Task
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
    init_parser.add_argument(
        "--backlog", type=Path, default=DEFAULT_BACKLOG, help="Backlog file path."
    )

    run_parser = sub.add_parser("run", help="Run the orchestrator over a backlog.")
    run_parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG, help="YAML config file (optional)."
    )
    run_parser.add_argument(
        "--project",
        type=Path,
        default=None,
        help="Project YAML file (ProjectConfig schema); a single immediate pass over that repository.",
    )
    run_parser.add_argument("--backlog", type=Path, default=None, help="Backlog file path.")
    run_parser.add_argument(
        "--agent", choices=("mock", "shell"), default=None, help="Agent adapter to use."
    )
    run_parser.add_argument(
        "--command",
        default=None,
        help="Shell command for the shell agent (string or JSON argv list).",
    )
    run_parser.add_argument("--timeout", type=float, default=None, help="Agent timeout in seconds.")
    run_parser.add_argument(
        "--env",
        action="append",
        default=None,
        metavar="KEY=VALUE",
        help="Extra env var for the agent (repeatable).",
    )
    run_parser.add_argument(
        "--repo", type=Path, default=None, help="Repository path given to agents."
    )
    run_parser.add_argument("--branch", default=None, help="Branch given to agents.")
    run_parser.add_argument(
        "--max-retries", type=int, default=None, help="Retries allowed after a task blocks."
    )
    run_parser.add_argument(
        "--poll-interval",
        type=float,
        default=None,
        help="Idle poll delay in seconds when the backlog is empty.",
    )
    run_parser.add_argument(
        "--feedback", choices=("console", "webhook"), default=None, help="Feedback provider."
    )
    run_parser.add_argument(
        "--webhook-url", default=None, help="URL for the webhook feedback provider."
    )
    run_parser.add_argument(
        "--delay", type=float, default=None, help="Mock agent artificial latency (seconds)."
    )
    run_parser.add_argument("--once", action="store_true", help="Process a single task and exit.")

    daemon_parser = sub.add_parser(
        "start-daemon", help="Run the persistent scheduled daemon for a project."
    )
    daemon_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Project YAML file (ProjectConfig schema).",
    )
    daemon_parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Log file (default: the project's log_file, i.e. factory.log).",
    )
    daemon_parser.add_argument(
        "--interval-minutes",
        type=int,
        default=None,
        help="Override schedule_interval_minutes from the project config.",
    )
    daemon_parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Daemon state file path (default: artifacts/daemon_state_<project>.json).",
    )
    daemon_parser.add_argument(
        "--no-refactor",
        action="store_true",
        help="Disable proactive refactoring scans when the backlog is empty.",
    )

    gen_parser = sub.add_parser(
        "generate-backlog", help="Interview a product idea and generate an executable backlog."
    )
    gen_parser.add_argument(
        "--prompt", "-p", default=None, help="Initial product idea (prompted if omitted)."
    )
    gen_parser.add_argument(
        "--output", "-o", type=Path, default=DEFAULT_BACKLOG, help="Backlog file to write."
    )
    gen_parser.add_argument(
        "--model", default=None, help="LLM model (overrides FACTORY_LLM_MODEL)."
    )
    gen_parser.add_argument(
        "--force", action="store_true", help="Replace an existing backlog file."
    )
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
    "poll_interval": "orchestrator.poll_interval_seconds",
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
        "poll_interval": 5.0,
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
        "poll_interval": float(pick("poll_interval")),
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
            console.print(
                "[red]The shell agent requires --command (or agent.command in the config).[/red]"
            )
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


def project_agent_opts(project: ProjectConfig) -> dict[str, Any]:
    """Derive agent options from a project config (for the legacy builder)."""
    agent_cfg = project.agent
    return {
        "command": agent_cfg.command if agent_cfg else None,
        "env": agent_cfg.env if agent_cfg else {},
        "timeout": agent_cfg.timeout_seconds if agent_cfg else 300.0,
        "delay": 0.0,
    }


def apply_project_overrides(project: ProjectConfig, args: argparse.Namespace) -> ProjectConfig:
    """Layer explicit CLI flags over a project config (CLI wins)."""
    updates: dict[str, Any] = {}
    if getattr(args, "backlog", None):
        updates["backlog_source"] = str(args.backlog)
    if getattr(args, "repo", None):
        updates["repo_path"] = args.repo
    if getattr(args, "agent", None):
        updates["agent_name"] = args.agent
    if getattr(args, "branch", None):
        updates["git"] = project.git.model_copy(update={"base_branch": args.branch})
    if getattr(args, "max_retries", None) is not None:
        updates["max_retries"] = args.max_retries
    if getattr(args, "feedback", None):
        updates["feedback"] = args.feedback
    if getattr(args, "webhook_url", None):
        updates["webhook_url"] = args.webhook_url
    command = getattr(args, "command", None)
    if command is not None or getattr(args, "agent", None) == "shell":
        command = command or (project.agent.command if project.agent else None)
        env = parse_env(getattr(args, "env", None))
        timeout = args.timeout or (project.agent.timeout_seconds if project.agent else 300.0)
        updates["agent"] = AgentConfig(command=command, env=env or {}, timeout_seconds=timeout)
    if not updates:
        return project
    return project.model_copy(update=updates)


def build_project_from_run_opts(cfg: dict[str, Any]) -> ProjectConfig:
    """Wrap plain ``factory run`` options in a (git-less) ProjectConfig."""
    agent_cfg = None
    if cfg["agent"] == "shell" and cfg["command"] is not None:
        agent_cfg = AgentConfig(
            command=cfg["command"], env=cfg["env"], timeout_seconds=cfg["timeout"]
        )
    return ProjectConfig(
        project_name="cli",
        repo_path=cfg["repo"],
        backlog_source=str(cfg["backlog"]),
        agent_name=cfg["agent"],
        agent=agent_cfg,
        feedback=cfg["feedback"],
        webhook_url=cfg["webhook_url"],
        max_retries=cfg["max_retries"],
        poll_interval_seconds=cfg["poll_interval"],
        log_file=None,
    )


async def build_components(project: ProjectConfig, *, unattended: bool):
    """Assemble backlog, agent, feedback, git manager, and orchestrator.

    Args:
        project: The project to operate on.
        unattended: When True, use the deferred (non-interactive) feedback
            provider and skip seeding sample tasks.
    """
    backlog = JSONBacklogAdapter(project.backlog_path)
    if not unattended:
        await seed_backlog(project.backlog_path)

    agent = build_agent(project.agent_name, project_agent_opts(project))
    if unattended:
        notifier = build_feedback(project.feedback, {"webhook_url": project.webhook_url})
        feedback = DeferredFeedbackProvider(
            backlog=backlog,
            poll_interval=project.poll_interval_seconds,
            on_blocked=notifier.notify,
        )
    else:
        feedback = build_feedback(project.feedback, {"webhook_url": project.webhook_url})

    git_manager = GitManager(project.repo_path) if project.git.enabled else None
    orchestrator = Orchestrator(
        config=project,
        backlog=backlog,
        agent=agent,
        feedback=feedback,
        git_manager=git_manager,
    )
    return backlog, agent, feedback, git_manager, orchestrator


def cmd_run(args: argparse.Namespace) -> int:
    """Handle ``factory run``."""

    async def _run() -> int:
        cfg = merge_config(load_config(args.config) if args.config else {}, args)
        if args.project is not None:
            project = apply_project_overrides(
                load_project_config(args.project, resolve_paths=True), args
            )
        else:
            project = build_project_from_run_opts(cfg)

        backlog, agent, feedback, _git, orchestrator = await build_components(
            project, unattended=False
        )

        console.print(
            Panel.fit(
                f"[bold]Project:[/bold] {project.project_name}  "
                f"[bold]Repo:[/bold] {project.repo_path}  "
                f"[bold]Backlog:[/bold] {project.backlog_path}  "
                f"[bold]Agent:[/bold] {agent.name}  "
                f"[bold]Feedback:[/bold] {feedback.name}",
                title="Software Factory",
                border_style="blue",
            )
        )

        processed = (
            1
            if args.once and await orchestrator.run_once() is not None
            else await orchestrator.run_until_idle()
        )

        await _render_summary(orchestrator, backlog, processed)
        return 0

    return asyncio.run(_run())


def cmd_start_daemon(args: argparse.Namespace) -> int:
    """Handle ``factory start-daemon``: the persistent scheduled worker."""
    project = load_project_config(args.config, resolve_paths=True)
    if args.interval_minutes is not None:
        project = project.model_copy(update={"schedule_interval_minutes": args.interval_minutes})
    if args.no_refactor:
        project = project.model_copy(
            update={"refactoring": project.refactoring.model_copy(update={"enabled": False})}
        )

    log_file = args.log_file or project.log_file or "factory.log"
    setup_logging(log_file)
    log = logging.getLogger("factory.cli")
    log.info("Loading project %r from %s", project.project_name, args.config)

    lock = acquire_run_lock(Path("artifacts") / f"daemon_lock_{project.project_name}.lock")
    if lock is None:
        console.print(
            f"[red]Another daemon appears to be running for project {project.project_name!r}.[/red]"
        )
        return 1

    async def _serve() -> None:
        backlog, _agent, _feedback, _git, orchestrator = await build_components(
            project, unattended=True
        )
        daemon = FactoryDaemon(
            config=project,
            backlog=backlog,
            agent=_agent,
            feedback=_feedback,
            orchestrator=orchestrator,
        )
        if args.state is not None:
            from factory.core.daemon import DaemonState

            daemon.state = DaemonState(args.state)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, daemon.stop)
            except NotImplementedError:
                pass
        console.print(
            Panel.fit(
                f"[bold]Daemon:[/bold] {project.project_name}\n"
                f"[bold]Repo:[/bold] {project.repo_path}\n"
                f"[bold]Interval:[/bold] {project.schedule_interval_minutes} min\n"
                f"[bold]Log:[/bold] {log_file}",
                title="Software Factory Daemon",
                border_style="green",
            )
        )
        await daemon.run_forever()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    finally:
        if lock is not None:
            lock.close()
    return 0


async def _render_summary(
    orchestrator: Orchestrator, backlog: JSONBacklogAdapter, processed: int
) -> None:
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
    if args.action == "start-daemon":
        return cmd_start_daemon(args)
    if args.action == "generate-backlog":
        from factory.generator.cli import cmd_generate_backlog

        return cmd_generate_backlog(args)
    parser.error(f"unknown command: {args.action}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
