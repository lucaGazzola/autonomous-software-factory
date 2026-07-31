"""Abstract contract every feedback provider must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod

from factory.core.models import UserResponse


class BaseFeedbackProvider(ABC):
    """Human-in-the-loop channel between the factory and an operator.

    Implementations deliver two kinds of messages:

    * ``request_input`` — a blocking question for a human when a task blocks;
      the returned :class:`UserResponse` drives the orchestrator's next move.
    * ``notify`` — a fire-and-forget alert (task completed, failed, aborted).

    Providers must be safe to call from asyncio tasks and must not raise on
    delivery failures; they should degrade to logging instead.
    """

    name: str = "base"
    """Human-readable provider name, used in CLI output."""

    @abstractmethod
    async def request_input(self, task_id: str, prompt: str) -> UserResponse:
        """Ask the human operator how to proceed on a blocked task.

        Args:
            task_id: Identifier of the blocked task.
            prompt: The question(s) to present, as a single message.

        Returns:
            The operator's disposition (retry / reply / abort).
        """

    @abstractmethod
    async def notify(self, task_id: str, message: str) -> None:
        """Deliver a non-blocking status alert about a task."""
