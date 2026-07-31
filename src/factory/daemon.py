"""The scheduled factory daemon.

Wakes up every ``interval_minutes``, runs one cycle of the :class:`Factory`,
and sleeps. A lock file prevents two daemons from running on the same
factory. Everything else is logged to the configured log file.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from factory.factory import Factory
from factory.models import FactoryConfig

logger = logging.getLogger(__name__)


def acquire_run_lock(lock_path: str | Path) -> Any:
    """Take an exclusive, non-blocking lock; returns the handle or ``None``.

    Uses ``fcntl`` flock so the lock is released automatically when the
    process exits (even on crash). Returns ``None`` when another daemon
    holds the lock.
    """
    lock_file = Path(lock_path)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl
    except ImportError:
        fcntl = None
    handle = lock_file.open("w")
    if fcntl is not None:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return None
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


class FactoryDaemon:
    """Runs :class:`Factory` cycles on a fixed schedule until stopped."""

    def __init__(self, config: FactoryConfig, factory: Factory) -> None:
        self.config = config
        self.factory = factory
        self.interval_seconds: float = config.interval_minutes * 60.0
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
                outcome = await self.factory.run_cycle()
                logger.info("Run finished: %s", outcome)
            except Exception:
                logger.exception("Run crashed; continuing on the next interval.")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
        logger.info("Factory stopped.")
