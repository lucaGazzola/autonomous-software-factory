"""Abstract contract every agent adapter must implement."""

from __future__ import annotations

from abc import ABC, abstractmethod

from factory.core.models import ExecutionResult, RepoContext, Task


class BaseAgentAdapter(ABC):
    """Uniform interface for invoking a coding agent on a task.

    The orchestrator only relies on this interface, so swapping the
    underlying agent (Claude Code, Aider, AutoGen, a custom script, ...)
    never touches core logic. Adapters are responsible for:

    * invoking the agent with the task and repo context,
    * streaming stdout/stderr into ``ExecutionResult.output_logs``,
    * tracking token/cost data in ``ExecutionResult.metadata``,
    * mapping agent outcomes onto the three ``ExecutionStatus`` values.
    """

    name: str = "base"
    """Human-readable adapter name, used in audit comments and CLI output."""

    @abstractmethod
    async def run_task(self, task: Task, context: RepoContext) -> ExecutionResult:
        """Execute one task and return its result.

        Implementations must never raise for expected agent failures — they
        should encode them as ``ExecutionResult(status=ERROR)`` so the
        orchestrator's state machine can react. They should raise only for
        genuinely unexpected infrastructure errors.

        Args:
            task: The task to execute.
            context: Repository information, possibly including human
                feedback from earlier blocked attempts.

        Returns:
            An ``ExecutionResult`` with status SUCCESS, BLOCKED, or ERROR.
        """

    async def can_handle(self, task: Task) -> bool:
        """Return whether this adapter is willing to run the given task.

        Useful for routing between multiple registered agents (e.g. by
        task metadata or language). Defaults to accepting everything.
        """
        return True
