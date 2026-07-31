"""The orchestrator: an event/state-machine driven loop.

Lifecycle of a task through the factory::

    ┌─────────┐   fetch     ┌──────────────┐   run      ┌───────────────┐
    │ Backlog │───────────▶ │  OPEN        │──────────▶ │  IN_PROGRESS  │
    └─────────┘  (oldest)   │ (next task)  │            └───────┬───────┘
                            └──────────────┘                    │ agent
                                                                 ▼
                                              ┌───────── ExecutionResult ─────────┐
                                              │                                  │
                                          SUCCESS                            BLOCKED
                                              │                                  │
                                              ▼                                  ▼
                                       ┌──────────┐                  ┌───────────────────┐
                                       │ COMPLETED│                  │ HITL request to   │
                                       └──────────┘                  │ feedback provider │
                                                              ┌─────┴─────────┬─────────┐
                                                              │   retry /    │  abort  │
                                                              │    reply     │         │
                                                              │ (re-execute, │   FALL  │
                                                              │  ≤ max_retries)        │
                                                              └──────────────┴─────────┘
                                                              ERROR ──────────────▶ FAILED

On SUCCESS the task is marked COMPLETED and its artifacts are attached to
the backlog. On ERROR it is marked FAILED. On BLOCKED the human operator
is consulted through the feedback provider: RETRY/REPLY re-executes the
agent (bounded by ``max_retries``), ABORT marks the task FAILED. Every
transition is recorded as a backlog comment and surfaced as a
notification, keeping the whole lifecycle auditable.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from factory.adapters.agents.base import BaseAgentAdapter
from factory.adapters.backlog.base import BaseBacklogAdapter
from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.core.models import (
    ExecutionResult,
    ExecutionStatus,
    RepoContext,
    ResponseAction,
    Task,
    TaskStatus,
)

logger = logging.getLogger(__name__)

#: Canonical mapping from agent outcomes to backlog task states.
STATUS_TRANSITIONS: dict[ExecutionStatus, TaskStatus] = {
    ExecutionStatus.SUCCESS: TaskStatus.COMPLETED,
    ExecutionStatus.BLOCKED: TaskStatus.BLOCKED,
    ExecutionStatus.ERROR: TaskStatus.FAILED,
}


@dataclass
class ExecutionStats:
    """Aggregate counters for a factory run."""

    processed: int = 0
    completed: int = 0
    failed: int = 0
    blocked: int = 0
    retries: int = 0
    errors: list[str] = field(default_factory=list)


class Orchestrator:
    """Wires backlog, agent, and feedback components into a task loop."""

    def __init__(
        self,
        backlog: BaseBacklogAdapter,
        agent: BaseAgentAdapter,
        feedback: BaseFeedbackProvider,
        context: RepoContext | None = None,
        max_retries: int = 3,
        poll_interval: float = 5.0,
    ) -> None:
        """Create the orchestrator.

        Args:
            backlog: Task source (fetch, status updates, comments, artifacts).
            agent: The pluggable coding agent that executes tasks.
            feedback: Human-in-the-loop channel for blocked tasks.
            context: Repository context handed to the agent.
            max_retries: Extra executions allowed after a task blocks before
                it is marked FAILED (each RETRY/REPLY consumes one).
            poll_interval: Idle wait between polls when the backlog is empty.
        """
        self.backlog = backlog
        self.agent = agent
        self.feedback = feedback
        self.context = context or RepoContext()
        self.max_retries = max_retries
        self.poll_interval = poll_interval
        self.stats = ExecutionStats()

    # ------------------------------------------------------------------ #
    # Public loop entry points                                            #
    # ------------------------------------------------------------------ #

    async def run_once(self) -> Task | None:
        """Process a single task; returns the task or ``None`` if idle."""
        task = await self.backlog.fetch_next_task()
        if task is None:
            logger.debug("Backlog empty; nothing to do.")
            return None
        logger.info("Processing task %s (%s)", task.id, task.title)
        await self._process(task, self.context, attempt=0)
        self.stats.processed += 1
        return task

    async def run_until_idle(self) -> int:
        """Drain the backlog until no runnable tasks remain.

        Returns:
            Number of tasks processed.
        """
        processed = 0
        while await self.run_once() is not None:
            processed += 1
        return processed

    async def run_forever(self) -> None:
        """Poll the backlog continuously, sleeping between idle cycles.

        Intended for a long-running factory service; terminate with a
        ``KeyboardInterrupt`` / SIGINT.
        """
        while True:
            if await self.run_once() is None:
                await asyncio.sleep(self.poll_interval)

    # ------------------------------------------------------------------ #
    # State machine                                                       #
    # ------------------------------------------------------------------ #

    async def _process(self, task: Task, context: RepoContext, attempt: int) -> None:
        """Execute one attempt and apply the resulting state transitions."""
        await self.backlog.update_task_status(task.id, TaskStatus.IN_PROGRESS)
        await self.backlog.add_comment(task.id, f"Attempt {attempt + 1} started by agent {self.agent.name!r}.")

        try:
            result = await self.agent.run_task(task, context.with_attempt(task.id, attempt))
        except Exception as exc:  # unexpected infrastructure failure
            logger.exception("Agent %r raised while running task %s", self.agent.name, task.id)
            result = ExecutionResult(
                status=ExecutionStatus.ERROR,
                error=f"agent raised {type(exc).__name__}: {exc}",
            )

        await self._apply_result(task, context, result, attempt)

    async def _apply_result(
        self,
        task: Task,
        context: RepoContext,
        result: ExecutionResult,
        attempt: int,
    ) -> None:
        """Route an agent result through the SUCCESS / BLOCKED / ERROR branches."""
        if result.status is ExecutionStatus.SUCCESS:
            await self._complete(task, result)
            return
        if result.status is ExecutionStatus.ERROR:
            await self._fail(task, result)
            return
        await self._handle_blocked(task, context, result, attempt)

    async def _complete(self, task: Task, result: ExecutionResult) -> None:
        """Mark a task COMPLETED, attach artifacts, and notify."""
        self.stats.completed += 1
        await self.backlog.update_task_status(task.id, TaskStatus.COMPLETED)
        await self.backlog.add_comment(
            task.id, f"Completed by agent {self.agent.name!r} with status {result.status.value}."
        )
        for artifact in result.artifacts:
            await self.backlog.attach_artifact(task.id, artifact)
        await self.backlog.add_comment(task.id, f"Artifacts attached: {len(result.artifacts)}")
        await self.feedback.notify(task.id, f"Task {task.id} completed successfully.")
        logger.info("Task %s completed.", task.id)

    async def _fail(self, task: Task, result: ExecutionResult, reason: str | None = None) -> None:
        """Mark a task FAILED and notify."""
        detail = reason or result.error or "no error detail provided"
        self.stats.failed += 1
        self.stats.errors.append(f"{task.id}: {detail}")
        await self.backlog.update_task_status(task.id, TaskStatus.FAILED)
        await self.backlog.add_comment(task.id, f"Marked FAILED: {detail}")
        await self.feedback.notify(task.id, f"Task {task.id} failed: {detail}")
        logger.info("Task %s failed: %s", task.id, detail)

    async def _handle_blocked(
        self, task: Task, context: RepoContext, result: ExecutionResult, attempt: int
    ) -> None:
        """Intercept a BLOCKED result and consult the human operator.

        Human-in-the-loop is the primary fallback: the operator is always
        asked before giving up. RETRY or REPLY re-executes the agent, bounded
        by ``max_retries``; ABORT fails the task immediately.
        """
        await self.backlog.update_task_status(task.id, TaskStatus.BLOCKED)
        self.stats.blocked += 1
        prompt = "\n".join(result.questions) if result.questions else (
            f"Agent {self.agent.name!r} could not complete task {task.id} without human input."
        )
        await self.backlog.add_comment(task.id, f"BLOCKED — requesting human input: {prompt!r}")

        response = await self.feedback.request_input(task.id, prompt)
        logger.info(
            "Task %s blocked at attempt %d; operator chose %s.",
            task.id, attempt + 1, response.action.value,
        )

        if response.action is ResponseAction.ABORT:
            await self._fail(
                task, result,
                reason=response.message or "aborted by human operator",
            )
            return

        if response.action is ResponseAction.REPLY:
            context = context.with_feedback(task.id, response.message)
            await self.backlog.add_comment(task.id, f"Human input recorded: {response.message!r}")

        if attempt >= self.max_retries:
            await self._fail(task, result, reason="maximum retries exhausted")
            return

        self.stats.retries += 1
        await self._process(task, context, attempt + 1)
