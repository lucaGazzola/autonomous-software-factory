"""The scheduled factory daemon.

Wakes up every ``interval_minutes``, runs one cycle of the :class:`Factory`,
and sleeps. A lock file prevents two daemons from running on the same
factory. A per-run lock prevents two agents from ever working on the same
repository at the same time: when a run is still in progress at the next
wake-up, that iteration is skipped instead of killing the running agent.
Everything else is logged to the configured log file.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from factory.factory import Factory
from factory.models import FactoryConfig

logger = logging.getLogger(__name__)


def _take_flock(lock_path: str | Path) -> Any | None:
    """Open the lock file and take a non-blocking exclusive flock.

    Uses ``fcntl`` flock so the lock is released automatically when the
    process exits (even on crash). Returns ``None`` when another process
    holds the lock. Falls back to no locking when ``fcntl`` is unavailable.
    """
    lock_file = Path(lock_path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_file.open("w")
    try:
        import fcntl
    except ImportError:
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        return handle
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def acquire_run_lock(lock_path: str | Path) -> Any:
    """Take an exclusive, non-blocking lock; returns the handle or ``None``.

    The lock is released automatically when the process exits (even on
    crash). Returns ``None`` when another daemon holds the lock.
    """
    return _take_flock(lock_path)


def is_lock_held(lock_path: str | Path) -> bool:
    """Return True when another process currently holds the exclusive flock.

    Does not create the lock file when it is missing. A leftover file with
    no live holder counts as not held.
    """
    lock_file = Path(lock_path)
    if not lock_file.exists():
        return False
    try:
        import fcntl
    except ImportError:
        return False
    try:
        handle = lock_file.open("r")
    except OSError:
        return False
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        handle.close()


class RunLock:
    """Per-iteration lock: one agent run at a time per factory.

    Held for the duration of one cycle so that a run still in progress (an
    overlong agent, an orphaned process after a daemon restart) makes the
    next iteration skip instead of starting a second agent on the same
    repository.
    """

    def __init__(self, lock_path: str | Path) -> None:
        self.lock_path = Path(lock_path)

    @contextmanager
    def held(self) -> Iterator[bool]:
        """Acquire for the duration of the block; yields True when acquired."""
        handle = _take_flock(self.lock_path)
        try:
            yield handle is not None
        finally:
            if handle is not None:
                handle.close()


class FactoryDaemon:
    """Runs :class:`Factory` cycles on a fixed schedule until stopped."""

    def __init__(self, config: FactoryConfig, factory: Factory) -> None:
        self.config = config
        self.factory = factory
        self.interval_seconds: float = config.interval_minutes * 60.0
        self.run_lock = RunLock(config.backlog.with_suffix(".run"))
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        """Request a graceful shutdown after the current cycle."""
        self._stop_event.set()

    async def run_forever(self) -> None:
        """Wake up on the schedule interval until ``stop()`` is called."""
        logger.info(
            "Factory %r started (repo=%s, interval=%s min, branch=%s).",
            self.config.name,
            self.config.repo,
            self.config.interval_minutes,
            self.config.branch,
        )
        while not self._stop_event.is_set():
            try:
                with self.run_lock.held() as acquired:
                    if not acquired:
                        logger.info("Previous run still in progress; skipping this iteration.")
                        outcome = "skipped"
                    else:
                        outcome = await self.factory.run_cycle()
                logger.info("Run finished: %s", outcome)
            except Exception:
                logger.exception("Run crashed; continuing on the next interval.")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
        logger.info("Factory stopped.")
