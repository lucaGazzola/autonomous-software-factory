"""Orchestrator state-machine tests: success, blocked (HITL), and failure paths.

A ``FakeFeedbackProvider`` stands in for the human operator so every
disposition (retry / reply / abort) can be scripted deterministically.
"""

from __future__ import annotations

import pytest

from factory.adapters.agents import MockAgentAdapter
from factory.adapters.backlog import JSONBacklogAdapter
from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.core.models import (
    ExecutionResult,
    ExecutionStatus,
    RepoContext,
    ResponseAction,
    Task,
    TaskStatus,
    UserResponse,
)
from factory.core.orchestrator import Orchestrator


class FakeFeedbackProvider(BaseFeedbackProvider):
    """Scriptable stand-in for the human operator."""

    name = "fake"

    def __init__(self, responses: list[UserResponse] | None = None) -> None:
        self.responses = list(responses or [])
        self.prompts: list[str] = []
        self.notifications: list[tuple[str, str]] = []

    async def request_input(self, task_id: str, prompt: str) -> UserResponse:
        self.prompts.append(prompt)
        if self.responses:
            return self.responses.pop(0)
        return UserResponse(task_id=task_id, action=ResponseAction.ABORT)

    async def notify(self, task_id: str, message: str) -> None:
        self.notifications.append((task_id, message))


def make_task(task_id: str = "T-1", simulate: str = "success") -> Task:
    return Task(id=task_id, title=f"Task {task_id}", metadata={"simulate": simulate})


async def run_orchestrator(tmp_path, task: Task, feedback, max_retries: int = 3):
    """Build a full stack with a JSON backlog + mock agent and process one task."""
    backlog = JSONBacklogAdapter(tmp_path / "backlog.json")
    await backlog.create_task(task)
    agent = MockAgentAdapter(delay_seconds=0.0)
    orchestrator = Orchestrator(
        backlog=backlog,
        agent=agent,
        feedback=feedback,
        context=RepoContext(repo_path=tmp_path),
        max_retries=max_retries,
    )
    processed = await orchestrator.run_once()
    assert processed is not None
    return backlog, orchestrator, feedback


async def test_success_path(tmp_path):
    """SUCCESS -> task COMPLETED, artifact attached, notification sent."""
    feedback = FakeFeedbackProvider()
    backlog, orchestrator, feedback = await run_orchestrator(tmp_path, make_task(simulate="success"), feedback)

    final = await backlog.get_task("T-1")
    assert final is not None
    assert final.status is TaskStatus.COMPLETED
    assert await backlog.list_artifacts("T-1")  # mock artifact recorded
    assert not feedback.prompts  # HITL never triggered
    assert any("completed" in message for _, message in feedback.notifications)
    assert orchestrator.stats.completed == 1
    assert orchestrator.stats.failed == 0


async def test_failure_path(tmp_path):
    """ERROR -> task FAILED, error surfaced in comments and notification."""
    feedback = FakeFeedbackProvider()
    backlog, orchestrator, feedback = await run_orchestrator(tmp_path, make_task(simulate="error"), feedback)

    final = await backlog.get_task("T-1")
    assert final is not None
    assert final.status is TaskStatus.FAILED
    assert any("FAILED" in comment for comment in await backlog.list_comments("T-1"))
    assert any("failed" in message for _, message in feedback.notifications)
    assert orchestrator.stats.failed == 1
    assert orchestrator.stats.errors == ["T-1: mock: simulated agent failure"]


async def test_blocked_then_retry_succeeds(tmp_path):
    """BLOCKED (transient) -> operator RETRY -> agent succeeds on second attempt."""
    feedback = FakeFeedbackProvider([UserResponse(task_id="T-1", action=ResponseAction.RETRY)])
    backlog, orchestrator, feedback = await run_orchestrator(tmp_path, make_task(simulate="flaky"), feedback)

    final = await backlog.get_task("T-1")
    assert final is not None
    assert final.status is TaskStatus.COMPLETED
    assert len(feedback.prompts) == 1
    assert orchestrator.stats.blocked == 1
    assert orchestrator.stats.retries == 1
    comments = await backlog.list_comments("T-1")
    assert any("BLOCKED" in c for c in comments)
    assert any("Completed by agent" in c for c in comments)


async def test_blocked_then_human_reply_resolves(tmp_path):
    """BLOCKED -> operator REPLY -> guidance reaches the agent, task succeeds."""
    feedback = FakeFeedbackProvider(
        [UserResponse(task_id="T-1", action=ResponseAction.REPLY, message="Use exponential backoff")]
    )
    backlog, orchestrator, feedback = await run_orchestrator(tmp_path, make_task(simulate="blocked"), feedback)

    final = await backlog.get_task("T-1")
    assert final is not None
    assert final.status is TaskStatus.COMPLETED
    comments = await backlog.list_comments("T-1")
    assert any("Human input recorded: 'Use exponential backoff'" in c for c in comments)
    assert orchestrator.stats.completed == 1


async def test_blocked_then_abort_fails(tmp_path):
    """BLOCKED -> operator ABORT -> task FAILED with abort reason."""
    feedback = FakeFeedbackProvider(
        [UserResponse(task_id="T-1", action=ResponseAction.ABORT, message="out of scope")]
    )
    backlog, orchestrator, feedback = await run_orchestrator(tmp_path, make_task(simulate="blocked"), feedback)

    final = await backlog.get_task("T-1")
    assert final is not None
    assert final.status is TaskStatus.FAILED
    comments = await backlog.list_comments("T-1")
    assert any("out of scope" in c for c in comments)
    assert orchestrator.stats.failed == 1
    assert orchestrator.stats.completed == 0


async def test_retries_exhausted_fails(tmp_path):
    """Persistent BLOCKED with RETRY -> task FAILED after max_retries."""
    retries = [UserResponse(task_id="T-1", action=ResponseAction.RETRY) for _ in range(3)]
    feedback = FakeFeedbackProvider(retries)
    backlog, orchestrator, feedback = await run_orchestrator(
        tmp_path, make_task(simulate="blocked"), feedback, max_retries=2
    )

    final = await backlog.get_task("T-1")
    assert final is not None
    assert final.status is TaskStatus.FAILED
    assert len(feedback.prompts) == 3  # operator consulted on every block
    comments = await backlog.list_comments("T-1")
    assert any("maximum retries exhausted" in c for c in comments)


async def test_zero_retries_fails_immediately(tmp_path):
    """With max_retries=0 the first block fails the task (after HITL)."""
    feedback = FakeFeedbackProvider([UserResponse(task_id="T-1", action=ResponseAction.RETRY)])
    backlog, orchestrator, feedback = await run_orchestrator(
        tmp_path, make_task(simulate="blocked"), feedback, max_retries=0
    )
    final = await backlog.get_task("T-1")
    assert final is not None
    assert final.status is TaskStatus.FAILED
    assert orchestrator.stats.retries == 0


async def test_agent_exception_marks_failed(tmp_path):
    """An unexpected agent exception is captured as an ERROR result."""

    class ExplodingAgent(MockAgentAdapter):
        name = "exploding"

        async def run_task(self, task, context):
            raise RuntimeError("boom")

    backlog = JSONBacklogAdapter(tmp_path / "backlog.json")
    await backlog.create_task(make_task(simulate="success"))
    orchestrator = Orchestrator(
        backlog=backlog,
        agent=ExplodingAgent(delay_seconds=0.0),
        feedback=FakeFeedbackProvider(),
        context=RepoContext(repo_path=tmp_path),
    )
    await orchestrator.run_once()
    final = await backlog.get_task("T-1")
    assert final is not None
    assert final.status is TaskStatus.FAILED
    assert orchestrator.stats.errors == ["T-1: agent raised RuntimeError: boom"]


async def test_run_until_idle_drains_backlog(tmp_path):
    """run_until_idle processes every OPEN task and reports the count."""
    backlog = JSONBacklogAdapter(tmp_path / "backlog.json")
    await backlog.create_task(make_task("T-1", simulate="success"))
    await backlog.create_task(make_task("T-2", simulate="success"))
    await backlog.create_task(make_task("T-3", simulate="error"))

    orchestrator = Orchestrator(
        backlog=backlog,
        agent=MockAgentAdapter(delay_seconds=0.0),
        feedback=FakeFeedbackProvider(),
        context=RepoContext(repo_path=tmp_path),
    )
    assert await orchestrator.run_until_idle() == 3
    assert orchestrator.stats.completed == 2
    assert orchestrator.stats.failed == 1
    assert await orchestrator.run_once() is None  # fully drained
