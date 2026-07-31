"""Unattended feedback provider for daemon mode.

The daemon runs without a terminal, so blocked tasks cannot wait on
``stdin``. ``DeferredFeedbackProvider`` instead: notifies the operator that
a task is blocked, then *waits* — polling the backlog until the human
resolves the block (typically by editing the task's status back to
``OPEN``) — and finally returns a RETRY so the orchestrator re-executes the
task with fresh state on the next wake-up.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from factory.adapters.backlog.base import BaseBacklogAdapter
from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.core.models import ResponseAction, TaskStatus, UserResponse

#: How the provider reports a block; receives (task_id, prompt).
BlockNotifier = Callable[[str, str], Awaitable[None]]


class DeferredFeedbackProvider(BaseFeedbackProvider):
    """Waits for a human to resolve a block instead of prompting interactively."""

    name = "deferred"

    def __init__(
        self,
        backlog: BaseBacklogAdapter,
        poll_interval: float = 60.0,
        on_blocked: BlockNotifier | None = None,
    ) -> None:
        """Create the provider.

        Args:
            backlog: Backlog polled to detect when a blocked task is resolved.
            poll_interval: How often to re-check the task status while waiting.
            on_blocked: Async callback notified when a task blocks (e.g. to
                deliver the alert through a notification channel).
        """
        self.backlog = backlog
        self.poll_interval = poll_interval
        self.on_blocked = on_blocked

    async def request_input(self, task_id: str, prompt: str) -> UserResponse:
        """Alert that ``task_id`` is blocked, then wait for the human fix."""
        if self.on_blocked is not None:
            try:
                await self.on_blocked(task_id, prompt)
            except Exception:  # an alert must never break the wait loop
                import logging

                logging.getLogger(__name__).exception("Block alert for task %s failed", task_id)

        while True:
            await asyncio.sleep(self.poll_interval)
            task = await self.backlog.get_task(task_id)
            if task is None or task.status is not TaskStatus.BLOCKED:
                return UserResponse(task_id=task_id, action=ResponseAction.RETRY)

    async def notify(self, task_id: str, message: str) -> None:
        """Pass status alerts to the notifier when one is configured."""
        if self.on_blocked is not None:
            await self.on_blocked(task_id, message)
