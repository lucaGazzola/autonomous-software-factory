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
from pathlib import Path

from factory.adapters.agents.base import BaseAgentAdapter
from factory.adapters.backlog.base import BaseBacklogAdapter
from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.adapters.git_manager import GitError, GitManager
from factory.core.models import (
    ExecutionResult,
    ExecutionStatus,
    ProjectConfig,
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
    """Wires backlog, agent, and feedback components into a task loop.

    The orchestrator is driven by a :class:`ProjectConfig` (repository path,
    git delivery policy, retry limits) but stays adapter-agnostic: the
    concrete backlog, agent, and feedback implementations are injected. When
    ``git_manager`` is provided, every task runs on its own git branch
    (``factory/task-<id>``) and the changes are committed and delivered
    (merge / push / PR) on success.
    """

    def __init__(
        self,
        backlog: BaseBacklogAdapter,
        agent: BaseAgentAdapter,
        feedback: BaseFeedbackProvider,
        config: ProjectConfig | None = None,
        context: RepoContext | None = None,
        max_retries: int | None = None,
        poll_interval: float | None = None,
        git_manager: GitManager | None = None,
    ) -> None:
        """Create the orchestrator.

        Args:
            backlog: Task source (fetch, status updates, comments, artifacts).
            agent: The pluggable coding agent that executes tasks.
            feedback: Human-in-the-loop channel for blocked tasks.
            config: Project configuration; when given it provides the
                default repository context, retry limits, and poll interval.
            context: Repository context handed to the agent; when omitted it
                is derived from ``config`` (or defaults to the current
                directory).
            max_retries: Extra executions allowed after a task blocks before
                it is marked FAILED (each RETRY/REPLY consumes one). Defaults
                to ``config.max_retries`` or 3.
            poll_interval: Idle wait between polls when the backlog is empty.
                Defaults to ``config.poll_interval_seconds`` or 5.
            git_manager: Optional git isolation; when set, ``config.git``
                controls branch creation and delivery.
        """
        self.backlog = backlog
        self.agent = agent
        self.feedback = feedback
        self.config = config
        self.context = context or RepoContext(
            repo_path=config.repo_path if config else Path("."),
            branch=config.git.base_branch if config else "main",
        )
        self.max_retries = (
            max_retries if max_retries is not None else (config.max_retries if config else 3)
        )
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else (config.poll_interval_seconds if config else 5.0)
        )
        self.git_manager = git_manager
        self.stats = ExecutionStats()
        #: task id -> git branch currently checked out for it (cleaned up on completion/failure).
        self._task_branches: dict[str, str] = {}

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
        await self.backlog.add_comment(
            task.id, f"Attempt {attempt + 1} started by agent {self.agent.name!r}."
        )

        task_branch: str | None = None
        if self.git_manager is not None:
            try:
                task_branch = await self._prepare_git(task)
                self._task_branches[task.id] = task_branch
                context = context.model_copy(update={"branch": task_branch})
            except GitError as exc:
                logger.error("Git isolation failed for task %s: %s", task.id, exc)
                result = ExecutionResult(
                    status=ExecutionStatus.ERROR,
                    error=f"git isolation failed: {exc}",
                )
                await self._apply_result(task, context, result, attempt)
                return

        try:
            result = await self.agent.run_task(task, context.with_attempt(task.id, attempt))
        except Exception as exc:  # unexpected infrastructure failure
            logger.exception("Agent %r raised while running task %s", self.agent.name, task.id)
            result = ExecutionResult(
                status=ExecutionStatus.ERROR,
                error=f"agent raised {type(exc).__name__}: {exc}",
            )

        if self.git_manager is not None and task_branch is not None:
            await self._finalize_git(task, task_branch, result, attempt)
        await self._apply_result(task, context, result, attempt)

    async def _prepare_git(self, task: Task) -> str:
        """Cut and check out a per-task branch (requires a clean tree)."""
        assert self.config is not None
        branch = await self.git_manager.prepare_task_branch(task, self.config.git)
        await self.backlog.add_comment(task.id, f"Working on git branch {branch!r}.")
        return branch

    async def _finalize_git(
        self,
        task: Task,
        branch: str,
        result: ExecutionResult,
        attempt: int,
    ) -> None:
        """Commit agent work and deliver / park / discard the task branch."""
        assert self.config is not None
        git = self.config.git

        if result.status is ExecutionStatus.SUCCESS:
            sha = await self.git_manager.commit_task_work(
                task, f"factory: {task.title} (#{task.id})"
            )
            if sha is None:
                logger.info("Task %s produced no changes; nothing committed.", task.id)
                await self.backlog.add_comment(task.id, "No changes produced by the agent.")
            delivery = await self.git_manager.deliver_task(
                task, branch, git, remote=self.config.git_remote
            )
            await self.backlog.add_comment(task.id, f"Git delivery: {delivery}")
            return

        if result.status is ExecutionStatus.BLOCKED:
            # Keep the agent's partial work on the branch so the retry can
            # pick up where it stopped, and leave the tree clean.
            sha = await self.git_manager.commit_task_work(
                task, f"factory: WIP for {task.id} (attempt {attempt + 1})"
            )
            if sha is not None:
                await self.backlog.add_comment(
                    task.id, f"Partial work committed on {branch!r} as {sha}."
                )
            return

        await self._abandon_task_branch(task)

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
        self._task_branches.pop(task.id, None)
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
        await self._abandon_task_branch(task)

    async def _abandon_task_branch(self, task: Task) -> None:
        """Discard a per-task git branch once a task can no longer succeed."""
        if self.git_manager is None or self.config is None:
            return
        branch = self._task_branches.pop(task.id, None)
        if branch is None:
            return
        try:
            await self.git_manager.abandon_task_branch(task, branch, self.config.git)
        except GitError as exc:
            logger.warning("Could not discard branch %r for task %s: %s", branch, task.id, exc)
            return
        await self.backlog.add_comment(
            task.id, f"Discarded git branch {branch!r} (task did not succeed)."
        )

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
        prompt = (
            "\n".join(result.questions)
            if result.questions
            else (
                f"Agent {self.agent.name!r} could not complete task {task.id} without human input."
            )
        )
        await self.backlog.add_comment(task.id, f"BLOCKED — requesting human input: {prompt!r}")

        response = await self.feedback.request_input(task.id, prompt)
        logger.info(
            "Task %s blocked at attempt %d; operator chose %s.",
            task.id,
            attempt + 1,
            response.action.value,
        )

        if response.action is ResponseAction.ABORT:
            await self._fail(
                task,
                result,
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
