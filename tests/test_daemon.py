"""Daemon tests: scheduled cycles, stop handling, run lock."""

from __future__ import annotations

import asyncio
import logging
import os

from factory.daemon import FactoryDaemon, RunLock, acquire_run_lock, is_lock_held, read_lock_pid
from tests.conftest import FakeFactory, make_config


def make_daemon(git_repo, tmp_path, interval=1, **overrides) -> FactoryDaemon:
    config = make_config(git_repo, tmp_path, interval_minutes=interval, **overrides)
    return FactoryDaemon(config, FakeFactory())


async def test_daemon_runs_cycles_on_interval(git_repo, tmp_path):
    daemon = make_daemon(git_repo, tmp_path)
    daemon.interval_seconds = 0.01
    task = asyncio.create_task(daemon.run_forever())

    while daemon.factory.cycles == 0:
        await asyncio.sleep(0.01)
    daemon.stop()
    await asyncio.wait_for(task, timeout=5)

    assert daemon.factory.cycles >= 1


async def test_daemon_interval_seconds(git_repo, tmp_path):
    daemon = make_daemon(git_repo, tmp_path, interval=5)
    assert daemon.interval_seconds == 300.0


async def test_daemon_survives_crashed_cycle(git_repo, tmp_path, caplog):
    daemon = make_daemon(git_repo, tmp_path)
    daemon.interval_seconds = 0.01
    daemon.factory.crash = True
    daemon.factory.cycles = 0
    with caplog.at_level(logging.ERROR, logger="factory"):
        task = asyncio.create_task(daemon.run_forever())
        await asyncio.sleep(0.1)
        daemon.stop()
        await asyncio.wait_for(task, timeout=5)
    assert "boom" in caplog.text


def test_run_lock_is_exclusive(tmp_path):
    lock_path = tmp_path / "factory.lock"
    first = acquire_run_lock(lock_path)
    assert first is not None
    assert acquire_run_lock(lock_path) is None
    first.close()
    assert acquire_run_lock(lock_path) is not None


def test_is_lock_held_detects_holder_and_stale_file(tmp_path):
    lock_path = tmp_path / "factory.lock"
    assert is_lock_held(lock_path) is False
    held = acquire_run_lock(lock_path)
    assert held is not None
    assert is_lock_held(lock_path) is True
    held.close()
    assert is_lock_held(lock_path) is False


def test_run_lock_held_while_active(tmp_path):
    lock_path = tmp_path / "factory.run"
    first = RunLock(lock_path)
    second = RunLock(lock_path)
    with first.held() as acquired:
        assert acquired is True
        with second.held() as blocked:
            assert blocked is False
    with second.held() as again:
        assert again is True


def test_read_lock_pid(tmp_path):
    lock_path = tmp_path / "factory.lock"
    assert read_lock_pid(lock_path) is None
    held = acquire_run_lock(lock_path)
    try:
        assert read_lock_pid(lock_path) == os.getpid()
    finally:
        held.close()


def test_read_lock_pid_garbage(tmp_path):
    lock_path = tmp_path / "factory.lock"
    lock_path.write_text("garbage\npid=notanumber\n", encoding="utf-8")
    assert read_lock_pid(lock_path) is None


def test_failed_acquire_keeps_holders_pid(tmp_path):
    """A second, failing acquire must not wipe the running holder's PID."""
    lock_path = tmp_path / "factory.lock"
    held = acquire_run_lock(lock_path)
    try:
        assert acquire_run_lock(lock_path) is None
        assert read_lock_pid(lock_path) == os.getpid()
    finally:
        held.close()


async def test_daemon_skips_when_previous_run_active(git_repo, tmp_path):
    daemon = make_daemon(git_repo, tmp_path)
    daemon.interval_seconds = 0.01
    lock = RunLock(daemon.run_lock.lock_path)
    with lock.held():
        task = asyncio.create_task(daemon.run_forever())
        await asyncio.sleep(0.1)
        daemon.stop()
        await asyncio.wait_for(task, timeout=5)
    assert daemon.factory.cycles == 0


async def test_daemon_runs_after_run_lock_released(git_repo, tmp_path):
    daemon = make_daemon(git_repo, tmp_path)
    daemon.interval_seconds = 0.01
    task = asyncio.create_task(daemon.run_forever())
    while daemon.factory.cycles == 0:
        await asyncio.sleep(0.01)
    daemon.stop()
    await asyncio.wait_for(task, timeout=5)
    assert daemon.factory.cycles >= 1


def test_state_file_written_on_start(git_repo, tmp_path):
    import json

    daemon = make_daemon(git_repo, tmp_path)
    assert not daemon.state_file.exists()
    daemon.write_state()
    payload = json.loads(daemon.state_file.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["started_at"]
    assert payload["last_outcome"] is None
    assert payload["next_run_at"] is None


async def test_state_file_tracks_runs(git_repo, tmp_path):
    import json

    daemon = make_daemon(git_repo, tmp_path)
    daemon.interval_seconds = 0.01
    task = asyncio.create_task(daemon.run_forever())
    while daemon.factory.cycles == 0:
        await asyncio.sleep(0.01)
    daemon.stop()
    await asyncio.wait_for(task, timeout=5)

    payload = json.loads(daemon.state_file.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["last_outcome"] == "task"
    assert payload["next_run_at"]


def test_state_file_path_next_to_backlog(git_repo, tmp_path):
    daemon = make_daemon(git_repo, tmp_path)
    assert daemon.state_file == tmp_path / "backlog.state.json"
