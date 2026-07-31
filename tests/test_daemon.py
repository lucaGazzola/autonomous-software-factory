"""Daemon tests: scheduled cycles, stop handling, run lock."""

from __future__ import annotations

import asyncio
import logging

from factory.daemon import FactoryDaemon, acquire_run_lock
from tests.conftest import make_config


class FakeFactory:
    """Records the cycle count; can block or crash on demand."""

    def __init__(self) -> None:
        self.cycles = 0
        self.crash = False
        self.block = False

    async def run_cycle(self) -> str:
        if self.block:
            await asyncio.sleep(3600)
        if self.crash:
            raise RuntimeError("boom")
        self.cycles += 1
        return "task"


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
