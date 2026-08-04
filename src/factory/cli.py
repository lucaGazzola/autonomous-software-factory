"""Command-line interface.

Commands:

* ``factory`` / ``factory init`` — guided first-time setup: asks for the
  factory folder, the coding agent command, and the refactor prompt, then
  writes a ``factory.yaml``. Running ``factory`` or ``factory start`` without
  a config triggers it automatically.
* ``factory start --config factory.yaml`` — run the scheduled factory on one
   repository. Every ``interval_minutes`` it picks an ``OPEN`` task from the
   backlog, or runs a refactoring pass when the backlog is empty; everything
    is committed and pushed on the main branch. When the agent needs human
    input, a detailed ``BLOCKER.md`` file is written with what you must do.
* ``factory once --config factory.yaml`` — run exactly one cycle and exit.
   Shares the per-factory lock with the daemon, so it never overlaps a
   running ``start``.
* ``factory status --config factory.yaml`` — print a read-only summary of the
   factory (config, backlog, daemon lock, last log outcome) and exit. Never
   starts an agent.
* ``factory stop --config factory.yaml`` — stop a running daemon gracefully
   (SIGTERM; a cycle in progress finishes first).
* ``factory restart --config factory.yaml`` — stop the daemon when running,
   then start it again detached in the background, re-reading the config.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import subprocess
import sys
import time
from collections import Counter
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from factory import __version__
from factory.agent import DockerSandboxAgent, SandboxUnavailableError, ShellAgent
from factory.backlog import JSONBacklog
from factory.config import load_config
from factory.daemon import FactoryDaemon, acquire_run_lock, is_lock_held, read_lock_pid
from factory.factory import Factory
from factory.git import GitManager
from factory.models import FactoryConfig, SandboxMode, Task, TaskStatus
from factory.runs import RunRecorder, runs_path_for
from factory.server import WebServer
from factory.setup import run_setup

OUTCOME_MARKER = "Run finished: "

DEFAULT_CONFIG = Path("factory.yaml")

STOP_TIMEOUT_SECONDS = 600.0
START_TIMEOUT_SECONDS = 15.0
_POLL_SECONDS = 0.5

console = Console()


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="factory",
        description="A scheduled software factory: executes backlog tasks on main, "
        "refactors when idle, and writes BLOCKER.md when it needs human input.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="action")

    init_parser = sub.add_parser(
        "init", help="Guided first-time setup: interactively write a factory.yaml."
    )
    init_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Where to write the config (default: factory.yaml).",
    )
    init_parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing config file."
    )

    start_parser = sub.add_parser("start", help="Start the scheduled factory for a repository.")
    start_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Factory YAML file (default: factory.yaml).",
    )
    start_parser.add_argument(
        "--interval-minutes",
        type=int,
        default=None,
        help="Override the schedule interval from the config file.",
    )

    once_parser = sub.add_parser("once", help="Run exactly one factory cycle and exit.")
    once_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Factory YAML file (default: factory.yaml).",
    )

    status_parser = sub.add_parser(
        "status",
        help="Print a read-only summary of the factory (never starts an agent).",
    )
    status_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Factory YAML file (default: factory.yaml).",
    )

    stop_parser = sub.add_parser(
        "stop",
        help="Stop a running factory daemon gracefully (SIGTERM).",
    )
    stop_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Factory YAML file (default: factory.yaml).",
    )
    stop_parser.add_argument(
        "--timeout",
        type=float,
        default=STOP_TIMEOUT_SECONDS,
        help="Seconds to wait for the daemon to exit (default: 600); a cycle "
        "in progress always finishes first.",
    )

    restart_parser = sub.add_parser(
        "restart",
        help="Restart the factory daemon in the background, re-reading the config.",
    )
    restart_parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Factory YAML file (default: factory.yaml).",
    )
    restart_parser.add_argument(
        "--timeout",
        type=float,
        default=STOP_TIMEOUT_SECONDS,
        help="Seconds to wait for the old daemon to exit (default: 600); a "
        "cycle in progress always finishes first.",
    )
    return parser


def setup_logging(log_file: str | Path) -> None:
    """Configure the ``factory`` logger with a rotating file handler."""
    logger = logging.getLogger("factory")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_path = Path(log_file)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _offer_setup(config_path: Path) -> bool:
    """Offer the guided setup; returns True when a config now exists."""
    if not Confirm.ask("No config found. Run the guided first-time setup now?", default=True):
        return False
    return run_setup(base_dir=config_path.parent.resolve(), config_path=config_path) is not None


def _resolve_config(args: argparse.Namespace) -> FactoryConfig | None:
    """Load the config, offering the guided setup when missing.

    Applies the optional ``interval_minutes`` override. Returns ``None``
    when no config can be produced.
    """
    if not args.config.exists():
        console.print(f"[yellow]Config file not found: {args.config}[/yellow]")
        if not _offer_setup(args.config):
            console.print(
                "[yellow]Create one with `factory init`, or pass --config <file>.[/yellow]"
            )
            return None
    config = load_config(args.config)
    interval = getattr(args, "interval_minutes", None)
    if interval is not None:
        config = config.model_copy(update={"interval_minutes": interval})
    return config


def _acquire_run_lock(config: FactoryConfig) -> Any | None:
    """Take the per-factory lock; prints an error and returns None when busy."""
    lock = acquire_run_lock(config.backlog.with_suffix(".lock"))
    if lock is None:
        console.print(
            f"[red]Another factory process (daemon or `once`) is already "
            f"running for {config.name!r}.[/red]"
        )
    return lock


def _build_agent(config: FactoryConfig) -> ShellAgent:
    """Build the configured agent, sandboxed when ``agent_sandbox`` demands it."""
    if config.agent_sandbox is SandboxMode.DOCKER:
        return DockerSandboxAgent(
            config.agent_command,
            image=config.agent_sandbox_image or "",
            network=config.agent_sandbox_network,
            mounts=config.agent_sandbox_mounts,
            timeout_seconds=config.agent_timeout_seconds,
            env=config.agent_env,
            blocked_exit_code=config.blocked_exit_code,
        )
    return ShellAgent(
        config.agent_command,
        timeout_seconds=config.agent_timeout_seconds,
        env=config.agent_env,
        blocked_exit_code=config.blocked_exit_code,
    )


def _make_factory(config: FactoryConfig) -> Factory:
    """Build a :class:`Factory` wired to the config.

    Raises:
        SandboxUnavailableError: When the configured sandbox backend is
            unavailable (e.g. no docker binary) — callers turn that into a
            clear startup error.
    """
    backlog = JSONBacklog(config.backlog)
    agent = _build_agent(config)
    return Factory(
        config,
        backlog,
        agent,
        GitManager(config.repo, timeout_seconds=config.git_timeout_seconds),
    )


def _prepare_worker(
    args: argparse.Namespace,
) -> tuple[FactoryConfig, Factory, Any] | None:
    """Resolve the config, take the run lock, and build the factory.

    Shared by ``start`` and ``once``. Returns ``None`` (after printing an
    error) when any step fails; on success the caller owns the lock and must
    close it.
    """
    config = _resolve_config(args)
    if config is None:
        return None
    setup_logging(config.log_file)
    log = logging.getLogger("factory.cli")
    log.info("Loading factory config from %s", args.config)
    lock = _acquire_run_lock(config)
    if lock is None:
        return None
    try:
        factory = _make_factory(config)
    except SandboxUnavailableError as exc:
        lock.close()
        console.print(f"[red]{exc}[/red]")
        log.error("Sandbox unavailable: %s", exc)
        return None
    return config, factory, lock


def cmd_start(args: argparse.Namespace) -> int:
    """Handle ``factory start``: the persistent scheduled worker."""
    prepared = _prepare_worker(args)
    if prepared is None:
        return 1
    config, factory, lock = prepared

    async def _serve() -> None:
        daemon = FactoryDaemon(config, factory)
        loop = asyncio.get_running_loop()
        web = WebServer(config, factory.backlog, daemon)
        web.start(loop)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, daemon.stop)
            except NotImplementedError:
                pass
        web_line = (
            f"\n[bold]Web:[/bold] http://{config.web_host}:{config.web_port}"
            if config.web_port
            else ""
        )
        console.print(
            Panel.fit(
                f"[bold]Factory:[/bold] {config.name}\n"
                f"[bold]Repo:[/bold] {config.repo}\n"
                f"[bold]Interval:[/bold] {config.interval_minutes} min\n"
                f"[bold]Backlog:[/bold] {config.backlog}\n"
                f"[bold]Branch:[/bold] {config.branch}\n"
                f"[bold]Log:[/bold] {config.log_file}"
                f"{web_line}",
                title="Forgeo",
                border_style="green",
            )
        )
        try:
            await daemon.run_forever()
        finally:
            web.stop()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass
    finally:
        lock.close()
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    """Handle ``factory once``: run exactly one cycle and exit."""
    prepared = _prepare_worker(args)
    if prepared is None:
        return 1
    _config, factory, lock = prepared

    async def _run_once() -> None:
        outcome = await factory.run_cycle()
        logging.getLogger("factory.cli").info("Run finished: %s", outcome)
        console.print(f"[green]Cycle finished: {outcome}[/green]")

    try:
        asyncio.run(_run_once())
    except KeyboardInterrupt:
        pass
    finally:
        lock.close()
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Handle ``factory init``: the guided first-time setup."""
    if args.config.exists() and not args.force:
        console.print(f"[red]{args.config} already exists. Pass --force to overwrite.[/red]")
        return 2
    if run_setup(base_dir=args.config.parent.resolve(), config_path=args.config) is None:
        console.print("[yellow]Setup aborted; nothing was written.[/yellow]")
        return 130
    return 0


def backlog_status_counts(tasks: list[Task]) -> dict[str, int]:
    """Count tasks by status; always includes every known status key."""
    counts = {status.value: 0 for status in TaskStatus}
    counts.update(Counter(task.status.value for task in tasks))
    return counts


def next_open_task(tasks: list[Task]) -> Task | None:
    """Return the oldest OPEN task, or ``None`` when there is none."""
    open_tasks = [task for task in tasks if task.status is TaskStatus.OPEN]
    if not open_tasks:
        return None
    return min(open_tasks, key=lambda task: task.created_at)


def last_outcome_from_log(log_file: str | Path) -> str | None:
    """Return the last ``Run finished: …`` outcome from the log, if any."""
    path = Path(log_file)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    outcome: str | None = None
    for line in text.splitlines():
        if OUTCOME_MARKER in line:
            outcome = line.rsplit(OUTCOME_MARKER, 1)[-1].strip() or None
    return outcome


def last_outcome_from_runs(config: FactoryConfig) -> str | None:
    """Return the last run's outcome from ``runs.jsonl``, or ``None``.

    Never raises on a missing or corrupt file; corrupt lines are skipped
    with a warning.
    """
    last_run = RunRecorder(runs_path_for(config.backlog)).read_last()
    if last_run is None:
        return None
    return last_run.outcome.value


def render_status(
    config: FactoryConfig,
    tasks: list[Task],
    *,
    daemon_running: bool,
    last_outcome: str | None,
) -> str:
    """Render the human-readable status summary as plain text."""
    counts = backlog_status_counts(tasks)
    count_text = " ".join(f"{status}={counts[status]}" for status in counts)
    nxt = next_open_task(tasks)
    next_text = f"{nxt.id} — {nxt.title}" if nxt is not None else "(none)"
    daemon_text = "running" if daemon_running else "not running"
    outcome_text = last_outcome if last_outcome is not None else "(none)"
    return "\n".join(
        [
            f"name: {config.name}",
            f"repo: {config.repo}",
            f"interval: {config.interval_minutes} min",
            f"branch: {config.branch}",
            f"backlog: {count_text}",
            f"next: {next_text}",
            f"daemon: {daemon_text}",
            f"last outcome: {outcome_text}",
        ]
    )


def cmd_status(args: argparse.Namespace) -> int:
    """Handle ``factory status``: read-only summary; never starts an agent."""
    if not args.config.exists():
        console.print(f"[red]Config file not found: {args.config}[/red]")
        return 1
    config = load_config(args.config)
    tasks = asyncio.run(JSONBacklog(config.backlog).list_tasks())
    daemon_running = is_lock_held(config.backlog.with_suffix(".lock"))
    last_outcome = last_outcome_from_runs(config)
    console.print(
        render_status(
            config,
            tasks,
            daemon_running=daemon_running,
            last_outcome=last_outcome,
        )
    )
    return 0


def _load_config_or_error(config_path: Path) -> FactoryConfig | None:
    """Load an existing config; prints an error and returns None when missing."""
    if not config_path.exists():
        console.print(f"[red]Config file not found: {config_path}[/red]")
        return None
    return load_config(config_path)


def _wait_for_lock_release(lock_path: Path, timeout: float) -> bool:
    """Poll until the daemon lock is released; False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_lock_held(lock_path):
            return True
        time.sleep(_POLL_SECONDS)
    return not is_lock_held(lock_path)


def _stop_daemon(config: FactoryConfig, timeout: float) -> bool:
    """SIGTERM the running daemon and wait for it to exit; False on failure."""
    lock_path = config.backlog.with_suffix(".lock")
    pid = read_lock_pid(lock_path)
    if pid is None:
        console.print(
            f"[red]The lock file {lock_path} records no PID; find the daemon "
            f"with `pgrep -af factory` and stop it manually.[/red]"
        )
        return False
    console.print(f"Stopping factory {config.name!r} (pid {pid})…")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        if not is_lock_held(lock_path):
            console.print(f"[green]Factory {config.name!r} stopped.[/green]")
            return True
        console.print(
            f"[red]Recorded pid {pid} is gone but the lock is still held; "
            f"check with `pgrep -af factory`.[/red]"
        )
        return False
    except PermissionError:
        console.print(f"[red]No permission to stop process {pid}.[/red]")
        return False
    if _wait_for_lock_release(lock_path, timeout):
        console.print(f"[green]Factory {config.name!r} stopped.[/green]")
        return True
    console.print(
        f"[yellow]Factory is still shutting down after {timeout:.0f}s "
        f"(a cycle in progress finishes first); giving up.[/yellow]"
    )
    return False


def cmd_stop(args: argparse.Namespace) -> int:
    """Handle ``factory stop``: graceful daemon shutdown via SIGTERM."""
    config = _load_config_or_error(args.config)
    if config is None:
        return 1
    if not is_lock_held(config.backlog.with_suffix(".lock")):
        console.print(f"[yellow]Factory {config.name!r} is not running.[/yellow]")
        return 1
    return 0 if _stop_daemon(config, args.timeout) else 1


def cmd_restart(args: argparse.Namespace) -> int:
    """Handle ``factory restart``: stop when running, then start detached."""
    config = _load_config_or_error(args.config)
    if config is None:
        return 1
    lock_path = config.backlog.with_suffix(".lock")
    if is_lock_held(lock_path) and not _stop_daemon(config, args.timeout):
        return 1
    proc = subprocess.Popen(
        [sys.executable, "-m", "factory", "start", "--config", str(args.config)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if is_lock_held(lock_path):
            console.print(
                f"[green]Factory {config.name!r} restarted "
                f"(pid {read_lock_pid(lock_path) or proc.pid}, "
                f"interval {config.interval_minutes} min).[/green]"
            )
            return 0
        if proc.poll() is not None:
            break
        time.sleep(_POLL_SECONDS)
    console.print(f"[red]Factory daemon did not start; see {config.log_file} for details.[/red]")
    return 1


def cmd_default() -> int:
    """Bare ``factory``: show help when configured, run the wizard otherwise."""
    if DEFAULT_CONFIG.exists():
        build_parser().print_help()
        return 0
    console.print("[yellow]No factory.yaml found — starting the guided setup.[/yellow]")
    return cmd_init(argparse.Namespace(config=DEFAULT_CONFIG, force=False))


def main(argv: list[str] | None = None) -> int:
    """CLI entry point used by both ``factory`` and ``python -m factory``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action is None:
        return cmd_default()
    if args.action == "start":
        return cmd_start(args)
    if args.action == "once":
        return cmd_once(args)
    if args.action == "init":
        return cmd_init(args)
    if args.action == "status":
        return cmd_status(args)
    if args.action == "stop":
        return cmd_stop(args)
    if args.action == "restart":
        return cmd_restart(args)
    parser.error(f"unknown command: {args.action}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
