"""Software Factory: agent-agnostic software development automation with human-in-the-loop."""

__version__ = "0.1.0"

from factory.core.models import (
    AgentConfig,
    ExecutionResult,
    ExecutionStatus,
    GitConfig,
    ProjectConfig,
    RefactoringConfig,
    RepoContext,
    ResponseAction,
    Task,
    TaskStatus,
    UserResponse,
)

__all__ = [
    "AgentConfig",
    "ExecutionResult",
    "ExecutionStatus",
    "GitConfig",
    "ProjectConfig",
    "RefactoringConfig",
    "RepoContext",
    "ResponseAction",
    "Task",
    "TaskStatus",
    "UserResponse",
    "__version__",
]
