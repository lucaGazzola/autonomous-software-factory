"""Software Factory: a scheduled software factory that executes backlog tasks
on the main branch and refactors when idle."""

__version__ = "0.1.0"

from factory.models import (
    ExecutionResult,
    ExecutionStatus,
    FactoryConfig,
    RepoContext,
    Task,
    TaskStatus,
)

__all__ = [
    "ExecutionResult",
    "ExecutionStatus",
    "FactoryConfig",
    "RepoContext",
    "Task",
    "TaskStatus",
    "__version__",
]
