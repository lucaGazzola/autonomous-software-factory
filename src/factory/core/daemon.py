"""Autonomous daemon: wake up, drain the backlog, else refactor.

``FactoryDaemon`` is the long-running process manager of the Software
Factory. One daemon instance owns exactly one project (one repository, one
backlog). Every ``schedule_interval_minutes`` it wakes up and:

1. If any task is ``BLOCKED`` — pause the project: alert the operator once,
   then sleep until the block is resolved (checked on the next wake-up).
2. If ``OPEN`` tasks exist — hand the backlog to the :class:`Orchestrator`,
   which drains it (each task on its own git branch when git is enabled).
3. If the backlog is empty — run the :class:`RefactoringScanner` to propose
   improvement tasks; those are picked up on the next cycle.

State (last cycle, last scan, alerted blocks) is persisted to a JSON state
file, and a lock file prevents two daemons from running the same project
concurrently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factory.adapters.agents.base import BaseAgentAdapter
from factory.adapters.backlog.base import BaseBacklogAdapter
from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.core.models import ProjectConfig, Task, TaskStatus
from factory.core.orchestrator import Orchestrator
from factory.core.refactoring import RefactoringScanner

logger = logging.getLogger(__name__)

_EMPTY_STATE: dict[str, Any] = {
    "last_cycle_at": None,
    "last_cycle_outcome": None,
    "last_refactor_scan_at": None,
    "blocked_alerted": {},
}


class DaemonState:
    """Small JSON-backed persistence for one daemon's cycle metadata.

    Writes are atomic (temp file + rename) and safe to call from asyncio.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        """Return the persisted state, tolerating a missing/corrupt file."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return dict(_EMPTY_STATE)
            return {**_EMPTY_STATE, **{k: v for k, v in data.items() if k in _EMPTY_STATE}}
        except (OSError, json.JSONDecodeError):
            return dict(_EMPTY_STATE)

    def save(self, state: dict[str, Any]) -> None:
        """Atomically persist ``state``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise


def acquire_run_lock(lock_path: str | Path) -> Any:
    """Take an exclusive, non-blocking lock; returns the handle or ``None``.

    Uses ``fcntl`` flock so the lock is released automatically when the
    process exits (even on crash). Returns ``None`` when another daemon
    holds the lock. On platforms without ``fcntl`` it degrades to a
    best-effort PID file that still serializes writers.
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
    """Scheduled, autonomous worker for a single project."""

    def __init__(
        self,
        config: ProjectConfig,
        backlog: BaseBacklogAdapter,
        agent: BaseAgentAdapter,
        feedback: BaseFeedbackProvider,
        orchestrator: Orchestrator | None = None,
        scanner: RefactoringScanner | None = None,
        state: DaemonState | None = None,
    ) -> None:
        """Create the daemon for ``config``.

        Args:
            config: Project configuration (schedule, repo, delivery policy).
            backlog: Task source for the project.
            agent: Agent adapter the orchestrator executes tasks with.
            feedback: Notification/HITL channel for blocks and alerts.
            orchestrator: Task executor; built from ``backlog``/``agent``/
                ``feedback`` when omitted.
            scanner: Refactoring scanner; when ``None`` and the config
                enables refactoring, one is built lazily.
            state: Cycle-state persistence; defaults next to the backlog.
        """
        self.config = config
        self.backlog = backlog
        self.agent = agent
        self.feedback = feedback
        self.orchestrator = orchestrator or Orchestrator(
            config=config, backlog=backlog, agent=agent, feedback=feedback
        )
        self.scanner = scanner
        if scanner is None and config.refactoring.enabled:
            from factory.core.refactoring import RefactoringScanner

            self.scanner = RefactoringScanner(
                repo_path=config.repo_path,
                backlog=backlog,
                config=config.refactoring,
            )
        self.state = state or DaemonState(
            Path("artifacts") / f"daemon_state_{config.project_name}.json"
        )
        self._stop_event = asyncio.Event()

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    @property
    def interval_seconds(self) -> float:
        """Seconds between wake-ups."""
        return self.config.schedule_interval_minutes * 60.0

    def stop(self) -> None:
        """Request a graceful shutdown after the current cycle."""
        self._stop_event.set()

    async def run_forever(self) -> None:
        """Wake up on the schedule interval until ``stop()`` is called."""
        logger.info(
            "Daemon started for project %r (repo=%s, interval=%s min).",
            self.config.project_name,
            self.config.repo_path,
            self.config.schedule_interval_minutes,
        )
        while not self._stop_event.is_set():
            outcome = await self.run_cycle()
            state = self.state.load()
            state["last_cycle_at"] = datetime.now(UTC).isoformat()
            state["last_cycle_outcome"] = outcome
            self.state.save(state)
            logger.info("Cycle finished: %s", outcome)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass
        logger.info("Daemon stopped.")

    async def run_cycle(self) -> str:
        """Execute one scheduled pass; returns a short outcome label.

        Outcomes: ``blocked``, ``processed:N``, ``scanned:N``, ``idle``.
        """
        tasks = await self.backlog.list_tasks()
        blocked = [t for t in tasks if t.status is TaskStatus.BLOCKED]
        if blocked:
            await self._pause_on_blocked(blocked)
            return "blocked"

        open_tasks = [t for t in tasks if t.status is TaskStatus.OPEN]
        if open_tasks:
            processed = await self.orchestrator.run_until_idle()
            return f"processed:{processed}"

        if self.scanner is not None and self._scan_allowed():
            try:
                created = await self.scanner.scan()
            except Exception:
                logger.exception("Refactoring scan raised; treating the cycle as idle.")
                created = []
            state = self.state.load()
            state["last_refactor_scan_at"] = datetime.now(UTC).isoformat()
            self.state.save(state)
            if created:
                logger.info("Refactoring scan proposed %d task(s).", len(created))
            return f"scanned:{len(created)}"

        return "idle"

    # ------------------------------------------------------------------ #
    # Internals                                                           #
    # ------------------------------------------------------------------ #

    def _scan_allowed(self) -> bool:
        """Apply the scanner's cooldown policy against persisted state."""
        last = self.state.load().get("last_refactor_scan_at")
        if not last:
            return True
        try:
            timestamp = datetime.fromisoformat(last)
        except ValueError:
            return True
        return self.scanner.cooldown_ok(timestamp)

    async def _pause_on_blocked(self, blocked: list[Task]) -> None:
        """Alert once per blocked task, then yield until the next cycle.

        The human resolves the block outside the daemon (e.g. by editing the
        task status back to OPEN); the next wake-up notices and resumes.
        """
        state = self.state.load()
        alerted: dict[str, str] = dict(state.get("blocked_alerted", {}))
        for task in blocked:
            if alerted.get(task.id) == task.status.value:
                continue
            try:
                await self.feedback.notify(
                    task.id,
                    f"Task {task.id} is BLOCKED. The factory is paused until a "
                    "human resolves the block (set the task back to OPEN).",
                )
            except Exception:
                logger.exception("Failed to alert about blocked task %s", task.id)
            alerted[task.id] = task.status.value
        state["blocked_alerted"] = alerted
        self.state.save(state)
        logger.warning("Project paused: %d blocked task(s).", len(blocked))
