"""Core data contracts shared by every Software Factory component.

These schemas are the single source of truth for how tasks, execution
results, agent configuration, and human feedback are represented across
the backlog, agent, and feedback adapters. All components communicate
exclusively through these models, which keeps the system pluggable and
decoupled.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(UTC)


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
        dependencies: Ids of tasks that must finish before this one starts
            (empty for setup/root tasks).
        acceptance_criteria: Concrete, testable conditions that mark the task done.
        files_to_modify: Repo-relative file paths the task is expected to touch.
        status: Current lifecycle state, managed by the orchestrator.
        metadata: Free-form adapter-specific key/value data (e.g. ``{"simulate": "blocked"}``).
        created_at: UTC timestamp of creation.
        updated_at: UTC timestamp of the last status change.
    """

    id: str
    title: str
    description: str = ""
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)
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


class GitConfig(BaseModel):
    """Git isolation and delivery policy for a project.

    When ``enabled``, the orchestrator requires a clean working tree before
    every task, works on a dedicated branch per task
    (``<branch_prefix><task-id>``), commits the agent's changes, and then
    merges / pushes / opens a PR depending on ``strategy``.

    Attributes:
        enabled: Perform per-task branch isolation, commits, and delivery.
        base_branch: Branch every task branch is cut from and merged back into.
        remote_name: Remote used by the ``push`` and ``pr`` strategies.
        branch_prefix: Prefix of the per-task branch name.
        strategy: What happens after a successful task: ``merge`` folds the
            task branch back into ``base_branch`` (and pushes when a remote
            is configured), ``push`` uploads the task branch, ``pr`` tries to
            open a pull request through the ``gh`` CLI, ``none`` leaves the
            commit on the local task branch.
        pr_labels: Labels passed to ``gh pr create --label``.
    """

    enabled: bool = False
    base_branch: str = "main"
    remote_name: str = "origin"
    branch_prefix: str = "factory/task-"
    strategy: Literal["merge", "push", "pr", "none"] = "push"
    pr_labels: list[str] = Field(default_factory=list)


class RefactoringConfig(BaseModel):
    """Policy for the proactive Exploratory/Refactoring mode.

    When the daemon wakes up to an empty backlog, ``RefactoringScanner``
    reviews the repository and proposes improvement tasks. The scanner never
    edits code itself: it only adds ``OPEN`` tasks for the orchestrator to
    execute on the next cycle.

    Attributes:
        enabled: Allow the daemon to run proactive refactoring scans.
        model: LLM model used for the review; falls back to the
            ``FACTORY_LLM_MODEL`` environment variable.
        max_tasks_per_scan: Upper bound on tasks the scanner may propose.
        cooldown_minutes: Minimum time between two scans that proposed
            nothing. 0 disables the cooldown (scan every idle cycle).
    """

    enabled: bool = True
    model: str | None = None
    max_tasks_per_scan: int = Field(default=3, ge=1)
    cooldown_minutes: int = Field(default=0, ge=0)


class ProjectConfig(BaseModel):
    """Everything the daemon needs to run one repository unattended.

    Attributes:
        project_name: Unique display name for the project; also used as a
            prefix in daemon state and lock files.
        repo_path: Local path of the git repository the factory works on.
        git_remote: Optional remote URL (e.g. ``origin`` URL) used for
            auto-pushing when set; supercedes the remote's configured URL.
        schedule_interval_minutes: How often the daemon wakes up to check
            the backlog (and run a refactoring scan when idle).
        backlog_source: Path to the backlog file, or an adapter identifier.
            Relative paths resolve against ``repo_path``.
        agent_name: Which agent adapter to use (``mock`` | ``shell``).
        agent: Extra configuration for the agent adapter.
        feedback: Which feedback provider to use (``console`` | ``webhook``);
            the daemon always uses the deferred provider regardless.
        webhook_url: URL for the ``webhook`` feedback provider.
        max_retries: Extra executions allowed after a task blocks.
        poll_interval_seconds: Idle wait between polls when the backlog is
            empty and the scheduler interval has not elapsed.
        git: Git isolation and delivery policy.
        refactoring: Proactive refactoring policy.
        log_file: Where the daemon writes its log; relative paths resolve
            against ``repo_path``.
    """

    project_name: str = Field(min_length=1)
    repo_path: Path = Field(default=Path("."))
    git_remote: str | None = None
    schedule_interval_minutes: int = Field(default=60, ge=1)
    backlog_source: str = "backlog.json"
    agent_name: str = "mock"
    agent: AgentConfig | None = None
    feedback: str = "console"
    webhook_url: str | None = None
    max_retries: int = Field(default=3, ge=0)
    poll_interval_seconds: float = Field(default=5.0, ge=0.1)
    git: GitConfig = Field(default_factory=GitConfig)
    refactoring: RefactoringConfig = Field(default_factory=RefactoringConfig)
    log_file: str | None = "factory.log"

    @field_validator("backlog_source")
    @classmethod
    def _backlog_source_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("backlog_source must not be blank")
        return value.strip()

    @property
    def backlog_path(self) -> Path:
        """Resolve the backlog file path against the repository path."""
        path = Path(self.backlog_source)
        return path if path.is_absolute() else self.repo_path / path
