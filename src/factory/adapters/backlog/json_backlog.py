"""Local JSON-file backlog adapter.

Tasks, comments, and artifacts are persisted in a single human-readable
JSON document, making this the simplest possible task source for local
experiments, demos, and tests:

.. code-block:: json

    {
      "tasks": [{...}],
      "comments": {"TASK-001": ["..."]},
      "artifacts": {"TASK-001": ["path/to/file"]}
    }

The file is created automatically on first use, writes are atomic
(temp file + rename), and all mutations are serialized through an
asyncio lock so concurrent orchestrators cannot corrupt the store.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factory.adapters.backlog.base import BaseBacklogAdapter
from factory.core.models import Task, TaskStatus

_EMPTY_STORE: dict[str, Any] = {"tasks": [], "comments": {}, "artifacts": {}}


class JSONBacklogAdapter(BaseBacklogAdapter):
    """A backlog stored in a single JSON document on disk."""

    def __init__(self, path: str | Path) -> None:
        """Create an adapter bound to ``path``.

        Args:
            path: Location of the backlog file; created on first use if absent.
        """
        self.path = Path(path)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Task operations                                                     #
    # ------------------------------------------------------------------ #

    async def fetch_next_task(self) -> Task | None:
        """Return the oldest OPEN task, or ``None`` if none is available."""
        tasks = await self.list_tasks()
        open_tasks = [t for t in tasks if t.status is TaskStatus.OPEN]
        if not open_tasks:
            return None
        return min(open_tasks, key=lambda t: t.created_at)

    async def list_tasks(self) -> list[Task]:
        """Return all tasks, in the order they were created."""
        store = await self._read()
        return [self._to_task(entry) for entry in store["tasks"]]

    async def get_task(self, task_id: str) -> Task | None:
        """Return a task by id, or ``None`` if it does not exist."""
        store = await self._read()
        for entry in store["tasks"]:
            if entry["id"] == task_id:
                return self._to_task(entry)
        return None

    async def create_task(self, task: Task) -> Task:
        """Persist ``task``, rejecting duplicate ids with a ``ValueError``."""
        async with self._lock:
            store = await self._read()
            if any(entry["id"] == task.id for entry in store["tasks"]):
                raise ValueError(f"Task id already exists in backlog: {task.id!r}")
            store["tasks"].append(task.model_dump(mode="json"))
            await self._write(store)
        return await self.get_task(task.id) or task

    async def update_task_status(self, task_id: str, status: TaskStatus) -> Task | None:
        """Transition a task's status, bumping its ``updated_at`` timestamp."""
        async with self._lock:
            store = await self._read()
            updated: Task | None = None
            for entry in store["tasks"]:
                if entry["id"] == task_id:
                    entry["status"] = status.value
                    entry["updated_at"] = datetime.now(UTC).isoformat()
                    updated = self._to_task(entry)
                    break
            if updated is not None:
                await self._write(store)
        return updated

    # ------------------------------------------------------------------ #
    # Comments and artifacts                                              #
    # ------------------------------------------------------------------ #

    async def add_comment(self, task_id: str, comment: str) -> None:
        """Append a comment to a task's timeline (creates the task if absent)."""
        async with self._lock:
            store = await self._read()
            store.setdefault("comments", {}).setdefault(task_id, []).append(comment)
            await self._write(store)

    async def list_comments(self, task_id: str) -> list[str]:
        """Return all comments for a task, oldest first."""
        store = await self._read()
        return list(store.get("comments", {}).get(task_id, []))

    async def attach_artifact(self, task_id: str, artifact_path: str | Path) -> None:
        """Record an artifact produced for a task."""
        async with self._lock:
            store = await self._read()
            store.setdefault("artifacts", {}).setdefault(task_id, []).append(str(artifact_path))
            await self._write(store)

    async def list_artifacts(self, task_id: str) -> list[str]:
        """Return all artifact paths recorded for a task."""
        store = await self._read()
        return list(store.get("artifacts", {}).get(task_id, []))

    # ------------------------------------------------------------------ #
    # Internal persistence helpers                                        #
    # ------------------------------------------------------------------ #

    async def _read(self) -> dict[str, Any]:
        """Load the store from disk, tolerating a missing or empty file."""
        if not self.path.exists():
            return {
                k: (v.copy() if isinstance(v, dict) else list(v)) for k, v in _EMPTY_STORE.items()
            }
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {
                k: (v.copy() if isinstance(v, dict) else list(v)) for k, v in _EMPTY_STORE.items()
            }
        return {
            "tasks": data.get("tasks", []),
            "comments": data.get("comments", {}),
            "artifacts": data.get("artifacts", {}),
        }

    async def _write(self, store: dict[str, Any]) -> None:
        """Atomically persist the store (temp file + rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(store, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    @staticmethod
    def _to_task(entry: dict[str, Any]) -> Task:
        """Validate a stored dictionary back into a Task, skipping corrupt rows."""
        try:
            return Task.model_validate(entry)
        except ValidationError:
            # A manually edited backlog should not take the whole store down.
            return Task(
                id=str(entry.get("id", "<unknown>")),
                title="<unparsable task>",
                status=TaskStatus.FAILED,
            )
