"""CLI tests for the ``factory once``/``status``/``stop``/``restart`` commands."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from factory.cli import (
    backlog_status_counts,
    build_parser,
    cmd_once,
    cmd_restart,
    cmd_status,
    cmd_stop,
    last_outcome_from_runs,
    render_status,
)
from factory.daemon import acquire_run_lock, is_lock_held, read_lock_pid
from factory.models import RunKind, RunOutcome, RunRecord, TaskStatus
from factory.runs import RunRecorder, runs_path_for
from tests.conftest import FakeFactory, make_config, make_task


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
        f"branch: {config.branch}\n"
        f"web_port: {config.web_port}\n",
        encoding="utf-8",
    )
    return path


def once_args(config_path: Path) -> argparse.Namespace:
    return argparse.Namespace(config=config_path)


def status_args(config_path: Path) -> argparse.Namespace:
    return argparse.Namespace(config=config_path)


def stop_args(config_path: Path, timeout: float = 30.0) -> argparse.Namespace:
    return argparse.Namespace(config=config_path, timeout=timeout)


def restart_args(config_path: Path, timeout: float = 30.0) -> argparse.Namespace:
    return argparse.Namespace(config=config_path, timeout=timeout)


def wait_for(predicate: Callable[[], bool], timeout: float = 15.0) -> bool:
    """Poll ``predicate`` until it holds; False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def spawn_daemon(config_path: Path) -> subprocess.Popen[bytes]:
    """Start a real ``factory start`` subprocess, detached like restart does."""
    return subprocess.Popen(
        [sys.executable, "-m", "factory", "start", "--config", str(config_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


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
    RunRecorder(runs_path_for(backlog)).append(
        RunRecord(
            started_at=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 1, 1, 0, 5, tzinfo=UTC),
            kind=RunKind.TASK,
            task_id="TASK-001",
            task_title="First open",
            outcome=RunOutcome.SUCCESS,
            agent_exit_code=0,
            commit_sha="abc1234",
            duration_seconds=5.0,
        )
    )

    assert cmd_status(status_args(config_path)) == 0
    out = capsys.readouterr().out
    assert "name: test-factory" in out
    assert "OPEN=1" in out
    assert "COMPLETED=1" in out
    assert "next: TASK-001 — First open" in out
    assert "daemon: not running" in out
    assert "last outcome: SUCCESS" in out


def test_status_renders_last_run_from_runs(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path)
    recorder = RunRecorder(runs_path_for(tmp_path / "backlog.json"))
    recorder.append(
        RunRecord(
            started_at=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 1, 1, 0, 5, tzinfo=UTC),
            kind=RunKind.REFACTOR,
            outcome=RunOutcome.BLOCKED,
            agent_exit_code=2,
            duration_seconds=5.0,
        )
    )
    recorder.append(
        RunRecord(
            started_at=datetime(2026, 8, 1, 2, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 1, 2, 0, 5, tzinfo=UTC),
            kind=RunKind.TASK,
            task_id="TASK-001",
            task_title="First open",
            outcome=RunOutcome.ERROR,
            agent_exit_code=3,
            duration_seconds=5.0,
        )
    )

    assert cmd_status(status_args(config_path)) == 0
    assert "last outcome: ERROR" in capsys.readouterr().out


def test_status_works_with_missing_runs(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path)
    assert not (tmp_path / "runs.jsonl").exists()

    assert cmd_status(status_args(config_path)) == 0
    assert "last outcome: (none)" in capsys.readouterr().out


def test_last_outcome_from_runs_missing(tmp_path):
    config = make_config(tmp_path, tmp_path)
    assert last_outcome_from_runs(config) is None


def test_last_outcome_from_runs_skips_corrupt(tmp_path, caplog):
    import logging

    config = make_config(tmp_path, tmp_path)
    recorder = RunRecorder(runs_path_for(config.backlog))
    recorder.append(
        RunRecord(
            started_at=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 1, 1, 0, 5, tzinfo=UTC),
            kind=RunKind.TASK,
            task_id="TASK-001",
            outcome=RunOutcome.SUCCESS,
            duration_seconds=5.0,
        )
    )
    recorder.path.write_text(
        recorder.path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8"
    )
    with caplog.at_level(logging.WARNING, logger="factory.runs"):
        assert last_outcome_from_runs(config) == "SUCCESS"
    assert "corrupt" in caplog.text


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


def test_parser_help_lists_stop_and_restart(capsys):
    build_parser().print_help()
    out = capsys.readouterr().out
    assert "stop" in out
    assert "restart" in out


def test_stop_not_running(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path)
    assert cmd_stop(stop_args(config_path)) == 1
    assert "not running" in capsys.readouterr().out


def test_stop_missing_config(tmp_path, capsys):
    assert cmd_stop(stop_args(tmp_path / "missing.yaml")) == 1
    assert "not found" in capsys.readouterr().out


def test_stop_stale_pid_errors(git_repo, tmp_path, capsys):
    """Lock held by an unknown process with a dead recorded pid: refuse."""
    import fcntl

    config_path = write_config(git_repo, tmp_path)
    handle = (tmp_path / "backlog.lock").open("w")
    handle.write("pid=999999999\n")
    handle.flush()
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert cmd_stop(stop_args(config_path)) == 1
        assert "is gone" in capsys.readouterr().out
    finally:
        handle.close()


def test_stop_terminates_running_daemon(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path, web_port=0, interval_minutes=600)
    lock_path = tmp_path / "backlog.lock"
    proc = spawn_daemon(config_path)
    try:
        assert wait_for(lambda: is_lock_held(lock_path))

        assert cmd_stop(stop_args(config_path)) == 0
        assert "stopped" in capsys.readouterr().out
        assert wait_for(lambda: proc.poll() is not None)
        assert not is_lock_held(lock_path)
    finally:
        if proc.poll() is None:
            proc.kill()


def test_restart_starts_daemon_when_not_running(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path, web_port=0, interval_minutes=600)
    lock_path = tmp_path / "backlog.lock"

    assert cmd_restart(restart_args(config_path)) == 0
    try:
        out = capsys.readouterr().out
        assert "restarted" in out
        assert "interval 600 min" in out
        assert is_lock_held(lock_path)
        pid = read_lock_pid(lock_path)
        assert pid is not None
    finally:
        cmd_stop(stop_args(config_path))


def test_restart_replaces_running_daemon(git_repo, tmp_path, capsys):
    config_path = write_config(git_repo, tmp_path, web_port=0, interval_minutes=600)
    lock_path = tmp_path / "backlog.lock"
    old_proc = spawn_daemon(config_path)
    try:
        assert wait_for(lambda: is_lock_held(lock_path))
        old_pid = read_lock_pid(lock_path)
        assert old_pid is not None
        capsys.readouterr()

        assert cmd_restart(restart_args(config_path)) == 0
        out = capsys.readouterr().out
        assert "restarted" in out
        new_pid = read_lock_pid(lock_path)
        assert new_pid is not None
        assert new_pid != old_pid
        assert wait_for(lambda: old_proc.poll() is not None)
        assert is_lock_held(lock_path)
    finally:
        if old_proc.poll() is None:
            old_proc.kill()
        cmd_stop(stop_args(config_path))
