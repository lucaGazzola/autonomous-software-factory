"""Abstract contract every backlog adapter must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from factory.core.models import Task, TaskStatus


class BaseBacklogAdapter(ABC):
    """Interface for task sources.

    Implementations wrap a concrete task tracker (JSON file, GitHub Issues,
    Jira, ...) and expose a uniform async API so the orchestrator never needs
    to know where tasks live. All methods are safe to call from concurrent
    asyncio tasks.
    """

    @abstractmethod
    async def fetch_next_task(self) -> Task | None:
        """Return the next runnable task, or ``None`` if the backlog is empty.

        Implementations decide what "runnable" means (e.g. oldest OPEN task)
        but must never hand out a task that is already being worked on.
        """

    @abstractmethod
    async def list_tasks(self) -> list[Task]:
        """Return every task currently tracked by the backlog."""

    @abstractmethod
    async def get_task(self, task_id: str) -> Task | None:
        """Return a single task by id, or ``None`` when it does not exist."""

    @abstractmethod
    async def create_task(self, task: Task) -> Task:
        """Persist a new task and return it as stored (ids are enforced unique)."""

    @abstractmethod
    async def update_task_status(self, task_id: str, status: TaskStatus) -> Task | None:
        """Transition a task's lifecycle status; returns the updated task."""

    @abstractmethod
    async def add_comment(self, task_id: str, comment: str) -> None:
        """Append a comment to a task's timeline (audit trail for actions)."""

    @abstractmethod
    async def list_comments(self, task_id: str) -> list[str]:
        """Return all comments attached to a task, oldest first."""

    @abstractmethod
    async def attach_artifact(self, task_id: str, artifact_path: str | Path) -> None:
        """Record a file produced by an agent against a task."""

    @abstractmethod
    async def list_artifacts(self, task_id: str) -> list[str]:
        """Return all artifact paths recorded against a task."""
