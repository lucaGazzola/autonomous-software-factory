"""CLI tests for the ``factory once`` and ``factory status`` commands."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from factory.cli import (
    backlog_status_counts,
    build_parser,
    cmd_once,
    cmd_status,
    last_outcome_from_log,
    next_open_task,
    render_status,
)
from factory.daemon import acquire_run_lock
from factory.models import TaskStatus
from tests.conftest import make_config, make_task


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
        f"log_file: {config.log_file}\n"
        f"interval_minutes: {config.interval_minutes}\n"
        f"branch: {config.branch}\n",
        encoding="utf-8",
    )
    return path


def once_args(config_path: Path) -> argparse.Namespace:
    return argparse.Namespace(config=config_path)


def status_args(config_path: Path) -> argparse.Namespace:
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


def test_parser_help_lists_status(capsys):
    build_parser().print_help()
    assert "status" in capsys.readouterr().out


def test_backlog_status_counts_empty():
    assert backlog_status_counts([]) == {
        "OPEN": 0,
        "BLOCKED": 0,
        "COMPLETED": 0,
        "FAILED": 0,
    }


def test_backlog_status_counts_by_status():
    tasks = [
        make_task(id="T1", status=TaskStatus.OPEN),
        make_task(id="T2", status=TaskStatus.OPEN),
        make_task(id="T3", status=TaskStatus.COMPLETED),
        make_task(id="T4", status=TaskStatus.FAILED),
        make_task(id="T5", status=TaskStatus.BLOCKED),
    ]
    assert backlog_status_counts(tasks) == {
        "OPEN": 2,
        "BLOCKED": 1,
        "COMPLETED": 1,
        "FAILED": 1,
    }


def test_next_open_task_picks_oldest():
    older = make_task(
        id="OLD",
        title="Older",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = make_task(
        id="NEW",
        title="Newer",
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    done = make_task(id="DONE", status=TaskStatus.COMPLETED)
    assert next_open_task([newer, done, older]) is older


def test_next_open_task_none_when_empty_or_no_open():
    assert next_open_task([]) is None
    assert next_open_task([make_task(status=TaskStatus.COMPLETED)]) is None


def test_last_outcome_from_log_missing(tmp_path):
    assert last_outcome_from_log(tmp_path / "missing.log") is None


def test_last_outcome_from_log_parses_last_marker(tmp_path):
    log = tmp_path / "factory.log"
    log.write_text(
        "2026-08-01 01:00:00 INFO     factory.daemon: Run finished: dirty\n"
        "2026-08-01 02:00:00 INFO     factory.factory: Task done\n"
        "2026-08-01 02:00:01 INFO     factory.daemon: Run finished: task\n",
        encoding="utf-8",
    )
    assert last_outcome_from_log(log) == "task"


def test_last_outcome_from_log_no_marker(tmp_path):
    log = tmp_path / "factory.log"
    log.write_text("no outcomes here\n", encoding="utf-8")
    assert last_outcome_from_log(log) is None


def test_render_status_includes_summary_fields(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path, interval_minutes=30, branch="main")
    tasks = [
        make_task(id="TASK-001", title="Do the thing", status=TaskStatus.OPEN),
        make_task(id="TASK-002", title="Done", status=TaskStatus.COMPLETED),
    ]
    text = render_status(config, tasks, daemon_running=True, last_outcome="task")
    assert "name: test-factory" in text
    assert f"repo: {config.repo}" in text
    assert "interval: 30 min" in text
    assert "branch: main" in text
    assert "OPEN=1" in text
    assert "COMPLETED=1" in text
    assert "next: TASK-001 — Do the thing" in text
    assert "daemon: running" in text
    assert "last outcome: task" in text


def test_render_status_empty_backlog_and_no_outcome(git_repo, tmp_path):
    config = make_config(git_repo, tmp_path)
    text = render_status(config, [], daemon_running=False, last_outcome=None)
    assert "OPEN=0" in text
    assert "next: (none)" in text
    assert "daemon: not running" in text
    assert "last outcome: (none)" in text


def test_status_prints_summary_and_exits_zero(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path)
    backlog = tmp_path / "backlog.json"
    backlog.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-001",
                        "title": "First open",
                        "status": "OPEN",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                    {
                        "id": "TASK-002",
                        "title": "Done already",
                        "status": "COMPLETED",
                        "created_at": "2026-01-02T00:00:00Z",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    log = tmp_path / "factory.log"
    log.write_text(
        "2026-08-01 02:00:01 INFO     factory.daemon: Run finished: task\n",
        encoding="utf-8",
    )

    assert cmd_status(status_args(config_path)) == 0
    out = capsys.readouterr().out
    assert "name: test-factory" in out
    assert "OPEN=1" in out
    assert "COMPLETED=1" in out
    assert "next: TASK-001 — First open" in out
    assert "daemon: not running" in out
    assert "last outcome: task" in out


def test_status_works_with_missing_backlog(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path)
    assert not (tmp_path / "backlog.json").exists()

    assert cmd_status(status_args(config_path)) == 0
    out = capsys.readouterr().out
    assert "OPEN=0" in out
    assert "next: (none)" in out


def test_status_reports_daemon_running(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path)
    lock = acquire_run_lock(tmp_path / "backlog.lock")
    assert lock is not None
    try:
        assert cmd_status(status_args(config_path)) == 0
        assert "daemon: running" in capsys.readouterr().out
    finally:
        lock.close()


def test_status_does_not_invoke_agent(git_repo, tmp_path, monkeypatch):
    config_path = write_config(git_repo, tmp_path)
    called: list[str] = []

    def boom(*_a, **_k):
        called.append("agent")
        raise AssertionError("agent must not be started")

    monkeypatch.setattr("factory.cli._make_factory", boom)
    monkeypatch.setattr("factory.cli.ShellAgent", boom)

    assert cmd_status(status_args(config_path)) == 0
    assert called == []


def test_status_missing_config(tmp_path, capsys):
    assert cmd_status(status_args(tmp_path / "missing.yaml")) == 1
    assert "not found" in capsys.readouterr().out
