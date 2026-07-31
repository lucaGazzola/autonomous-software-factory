"""Core data contracts shared by every Software Factory component.

These schemas are the single source of truth for how tasks, execution
results, agent configuration, and human feedback are represented across
the backlog, agent, and feedback adapters. All components communicate
exclusively through these models, which keeps the system pluggable and
decoupled.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


class TaskStatus(str, enum.Enum):
    """Lifecycle states a task can occupy in the backlog."""

    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExecutionStatus(str, enum.Enum):
    """Outcome of a single agent run on a task."""

    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class ResponseAction(str, enum.Enum):
    """Dispositions a human operator can choose when a task blocks."""

    RETRY = "retry"
    REPLY = "reply"
    ABORT = "abort"


class Task(BaseModel):
    """A unit of work pulled from the backlog and executed by an agent.

    Attributes:
        id: Stable, unique identifier assigned by the backlog (e.g. ``TASK-001``).
        title: Short human-readable summary of the work.
        description: Full specification or acceptance criteria for the work.
        status: Current lifecycle state, managed by the orchestrator.
        metadata: Free-form adapter-specific key/value data (e.g. ``{"simulate": "blocked"}``).
        created_at: UTC timestamp of creation.
        updated_at: UTC timestamp of the last status change.
    """

    id: str
    title: str
    description: str = ""
    status: TaskStatus = TaskStatus.OPEN
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ExecutionResult(BaseModel):
    """The outcome of one agent execution attempt on a task.

    Attributes:
        status: Whether the run succeeded, blocked on missing input, or errored.
        output_logs: Streamed stdout/stderr lines from the agent, line-oriented.
        artifacts: Absolute or repo-relative paths of files the agent produced.
        questions: Prompts for the human operator, only set when status is BLOCKED.
        error: Short machine-readable error description, only set when status is ERROR.
        metadata: Free-form extension point (e.g. token/cost accounting).
    """

    status: ExecutionStatus
    output_logs: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Static configuration for a process-based agent adapter.

    Attributes:
        command: Shell command string, or a pre-split argv list, to execute.
        env: Extra environment variables merged over the parent environment.
        timeout_seconds: Kill the agent process and fail after this many seconds.
        workdir: Working directory for the process; defaults to the repo path.
    """

    command: str | list[str]
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 300.0
    workdir: str | None = None


class RepoContext(BaseModel):
    """Repository information handed to agents alongside the task.

    Attributes:
        repo_path: Filesystem path of the checkout the agent works on.
        branch: Branch the agent is expected to operate on.
        metadata: Extension space; ``metadata["feedback"][task_id]`` carries
            human replies from the HITL channel back into subsequent runs.
    """

    repo_path: Path = Path(".")
    branch: str = "main"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def with_feedback(self, task_id: str, message: str) -> RepoContext:
        """Return a copy of this context augmented with a human reply.

        Human-in-the-loop replies are attached to the context so agents can
        observe guidance they received on a previous blocked attempt.
        """
        feedback = dict(self.metadata.get("feedback", {}))
        feedback[task_id] = message
        return self.model_copy(update={"metadata": {**self.metadata, "feedback": feedback}})

    def feedback_for(self, task_id: str) -> str | None:
        """Return the human reply previously provided for a task, if any."""
        return self.metadata.get("feedback", {}).get(task_id)

    def with_attempt(self, task_id: str, attempt: int) -> RepoContext:
        """Return a copy recording which execution attempt the task is on.

        Agents can use this to distinguish a first run from retries, e.g. to
        model transient blocks that clear on re-execution.
        """
        attempts = dict(self.metadata.get("attempts", {}))
        attempts[task_id] = attempt
        return self.model_copy(update={"metadata": {**self.metadata, "attempts": attempts}})

    def attempt_for(self, task_id: str) -> int:
        """Return the zero-based execution attempt recorded for a task."""
        return self.metadata.get("attempts", {}).get(task_id, 0)


class UserResponse(BaseModel):
    """A human operator's decision in response to a blocked task.

    Attributes:
        task_id: Identifier of the task the response concerns.
        action: How the factory should proceed (retry, reply with guidance, abort).
        message: Free-form guidance; required for REPLY, optional otherwise.
    """

    task_id: str
    action: ResponseAction
    message: str = ""
