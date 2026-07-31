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
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from factory import __version__
from factory.agent import ShellAgent
from factory.backlog import JSONBacklog
from factory.config import load_config
from factory.daemon import FactoryDaemon, acquire_run_lock
from factory.factory import Factory
from factory.git import GitManager
from factory.setup import run_setup

DEFAULT_CONFIG = Path("factory.yaml")

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
        "--config", type=Path, default=DEFAULT_CONFIG, help="Factory YAML file (default: factory.yaml)."
    )
    start_parser.add_argument(
        "--interval-minutes",
        type=int,
        default=None,
        help="Override the schedule interval from the config file.",
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
    handler = RotatingFileHandler(file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def _offer_setup(config_path: Path) -> bool:
    """Offer the guided setup; returns True when a config now exists."""
    if not Confirm.ask("No config found. Run the guided first-time setup now?", default=True):
        return False
    return run_setup(base_dir=config_path.parent.resolve(), config_path=config_path) is not None


def cmd_start(args: argparse.Namespace) -> int:
    """Handle ``factory start``: the persistent scheduled worker."""
    if not args.config.exists():
        console.print(f"[yellow]Config file not found: {args.config}[/yellow]")
        if not _offer_setup(args.config):
            console.print("[yellow]Create one with `factory init`, or pass --config <file>.[/yellow]")
            return 1
    config = load_config(args.config)
    if args.interval_minutes is not None:
        config = config.model_copy(update={"interval_minutes": args.interval_minutes})

    setup_logging(config.log_file)
    log = logging.getLogger("factory.cli")
    log.info("Loading factory config from %s", args.config)

    lock = acquire_run_lock(config.backlog.with_suffix(".lock"))
    if lock is None:
        console.print(f"[red]Another factory daemon is already running for {config.name!r}.[/red]")
        return 1

    async def _serve() -> None:
        backlog = JSONBacklog(config.backlog)
        agent = ShellAgent(
            config.agent_command,
            timeout_seconds=config.agent_timeout_seconds,
            env=config.agent_env,
            blocked_exit_code=config.blocked_exit_code,
        )
        factory = Factory(config, backlog, agent, GitManager(config.repo))
        daemon = FactoryDaemon(config, factory)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, daemon.stop)
            except NotImplementedError:
                pass
        console.print(
            Panel.fit(
                f"[bold]Factory:[/bold] {config.name}\n"
                f"[bold]Repo:[/bold] {config.repo}\n"
                f"[bold]Interval:[/bold] {config.interval_minutes} min\n"
                f"[bold]Backlog:[/bold] {config.backlog}\n"
                f"[bold]Branch:[/bold] {config.branch}\n"
                f"[bold]Log:[/bold] {config.log_file}",
                title="Software Factory",
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


def cmd_init(args: argparse.Namespace) -> int:
    """Handle ``factory init``: the guided first-time setup."""
    if args.config.exists() and not args.force:
        console.print(f"[red]{args.config} already exists. Pass --force to overwrite.[/red]")
        return 2
    if run_setup(base_dir=args.config.parent.resolve(), config_path=args.config) is None:
        console.print("[yellow]Setup aborted; nothing was written.[/yellow]")
        return 130
    return 0


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
    if args.action == "init":
        return cmd_init(args)
    parser.error(f"unknown command: {args.action}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
