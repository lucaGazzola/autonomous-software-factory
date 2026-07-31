"""The only data contracts the factory needs.

A task lives in the backlog, gets executed by the agent, and changes status
exactly once per run. A factory config describes one repository and how the
factory should work on it.
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

DEFAULT_REFACTOR_PROMPT = (
    "Review the codebase for improvement opportunities that do not change "
    "behavior: dead code, duplication, overly complex functions, missing "
    "tests, outdated comments. Apply the safe improvements you find and run "
    "the test suite to verify nothing broke. If nothing needs refactoring, "
    "make no changes."
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TaskStatus(str, enum.Enum):
    OPEN = "OPEN"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ExecutionStatus(str, enum.Enum):
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


class Task(BaseModel):
    """A unit of work the factory executes with the coding agent."""

    id: str
    title: str
    description: str = ""
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.OPEN
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class ExecutionResult(BaseModel):
    """The outcome of one agent run."""

    status: ExecutionStatus
    output_logs: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    error: str | None = None


class RepoContext(BaseModel):
    """Where the agent works: the repository checkout and its branch."""

    repo_path: Path = Path(".")
    branch: str = "main"


class FactoryConfig(BaseModel):
    """Everything needed to run one factory on one repository.

    Attributes:
        name: Display name of this factory (used in logs and commit messages).
        repo: Path of the git repository the factory works on.
        interval_minutes: How often a scheduled run happens.
        backlog: Path of the JSON backlog file (created on first use).
        blocker_file: Where ``BLOCKER.md`` is written when the agent needs
            human input. Keep it outside the repository so it is never
            committed.
        agent_command: Shell command (or argv list) that runs the coding
            agent. Exit 0 = success, ``blocked_exit_code`` = needs human
            input, anything else = error. The task is available to the
            process as the ``FACTORY_TASK`` environment variable.
        agent_timeout_seconds: Kill the agent process after this many seconds.
        agent_env: Extra environment variables for the agent process.
        blocked_exit_code: Exit code the agent uses to signal that it needs
            human input.
        remote: Git remote to push to (e.g. ``origin``). When omitted the
            factory only commits locally.
        branch: Branch everything is committed to (default ``main``).
        refactor_prompt: Instruction used for the refactoring run that
            happens when the backlog has no runnable task.
        log_file: Where the scheduled factory writes its log.
    """

    name: str = "software-factory"
    repo: Path = Field(default=Path("."))
    interval_minutes: int = Field(default=60, ge=1)
    backlog: Path = Field(default=Path("backlog.json"))
    blocker_file: Path = Field(default=Path("BLOCKER.md"))
    agent_command: str | list[str]
    agent_timeout_seconds: float = Field(default=1800.0, gt=0)
    agent_env: dict[str, str] = Field(default_factory=dict)
    blocked_exit_code: int = Field(default=2)
    remote: str | None = None
    branch: str = "main"
    refactor_prompt: str = DEFAULT_REFACTOR_PROMPT
    log_file: str = "factory.log"

    @field_validator("agent_command")
    @classmethod
    def _command_not_blank(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str) and not value.strip():
            raise ValueError("agent_command must not be blank")
        if isinstance(value, list) and not value:
            raise ValueError("agent_command must not be an empty list")
        return value

    @property
    def blocked_command(self) -> str:
        """The agent command as a plain string (``argv list`` joined)."""
        if isinstance(self.agent_command, list):
            return " ".join(self.agent_command)
        return self.agent_command
