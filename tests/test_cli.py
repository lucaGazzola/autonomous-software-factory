"""CLI tests for the ``factory once`` command."""

from __future__ import annotations

import argparse
from pathlib import Path

from factory.cli import build_parser, cmd_once
from factory.daemon import acquire_run_lock
from tests.conftest import make_config


class FakeFactory:
    """Records the cycle count and returns a fixed outcome."""

    def __init__(self) -> None:
        self.cycles = 0

    async def run_cycle(self) -> str:
        self.cycles += 1
        return "task"


def write_config(git_repo: Path, tmp_path: Path, **overrides) -> Path:
    """A config file wired to the fixture repo; returns its path."""
    config = make_config(git_repo, tmp_path, **overrides)
    path = tmp_path / "factory.yaml"
    path.write_text(
        f"name: {config.name}\n"
        f"repo: {config.repo}\n"
        f"backlog: {config.backlog}\n"
        f"blocker_file: {config.blocker_file}\n"
        f"agent_command: {config.agent_command}\n"
        f"log_file: {config.log_file}\n",
        encoding="utf-8",
    )
    return path


def once_args(config_path: Path) -> argparse.Namespace:
    return argparse.Namespace(config=config_path)


def test_once_runs_one_cycle_and_exits_zero(git_repo, tmp_path, monkeypatch, capsys):
    config_path = write_config(git_repo, tmp_path)
    fake = FakeFactory()
    monkeypatch.setattr("factory.cli._make_factory", lambda config: fake)

    assert cmd_once(once_args(config_path)) == 0
    assert fake.cycles == 1
    assert "Cycle finished: task" in capsys.readouterr().out

    lock_path = tmp_path / "backlog.lock"
    released = acquire_run_lock(lock_path)
    assert released is not None
    released.close()


def test_once_refuses_while_lock_held(git_repo, tmp_path, monkeypatch, capsys):
    config_path = write_config(git_repo, tmp_path)
    fake = FakeFactory()
    monkeypatch.setattr("factory.cli._make_factory", lambda config: fake)
    lock = acquire_run_lock(tmp_path / "backlog.lock")
    assert lock is not None

    assert cmd_once(once_args(config_path)) == 1
    assert fake.cycles == 0
    assert "already running" in capsys.readouterr().out

    lock.close()


def test_once_refuses_while_daemon_lock_held(git_repo, tmp_path, monkeypatch):
    config_path = write_config(git_repo, tmp_path)
    fake = FakeFactory()
    monkeypatch.setattr("factory.cli._make_factory", lambda config: fake)
    config = make_config(git_repo, tmp_path)
    lock = acquire_run_lock(config.backlog.with_suffix(".lock"))
    assert lock is not None

    assert cmd_once(once_args(config_path)) == 1
    assert fake.cycles == 0

    lock.close()


def test_once_missing_config_offers_setup(monkeypatch, tmp_path):
    monkeypatch.setattr("factory.cli.Confirm.ask", lambda *a, **k: False)
    args = argparse.Namespace(config=tmp_path / "factory.yaml")
    assert cmd_once(args) == 1


def test_parser_help_lists_once(capsys):
    build_parser().print_help()
    assert "once" in capsys.readouterr().out
