"""The coding agent: any shell command that implements the task.

The configured command is run with the repository as its working directory.
The task is delivered to the agent as the ``FACTORY_TASK`` environment
variable (title, description, acceptance criteria) so any CLI coding tool
(aider, claude, a custom script, ...) can consume it. The exit code decides
the outcome:

* ``0`` — SUCCESS, the work is committed and pushed,
* ``blocked_exit_code`` (default ``2``) — BLOCKED, the agent needs human
  input; its output ends up in the blocker file,
* anything else — ERROR, the task fails and the agent's changes are
  discarded.
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod

from factory.models import ExecutionResult, ExecutionStatus, RepoContext, Task


class BaseAgent(ABC):
    """Uniform interface for invoking a coding agent on a task."""

    name: str = "base"

    @abstractmethod
    async def run_task(self, task: Task, context: RepoContext) -> ExecutionResult:
        """Execute one task and return its result.

        Implementations must never raise for expected agent failures — they
        should encode them as ``ExecutionResult(status=ERROR)``.
        """


class ShellAgent(BaseAgent):
    """Runs ``FactoryConfig.agent_command`` in a subprocess."""

    name = "shell"

    def __init__(
        self,
        command: str | list[str],
        *,
        timeout_seconds: float = 1800.0,
        env: dict[str, str] | None = None,
        blocked_exit_code: int = 2,
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.env = dict(env or {})
        self.blocked_exit_code = blocked_exit_code

    def _task_instruction(self, task: Task) -> str:
        """The full instruction handed to the agent via ``FACTORY_TASK``."""
        lines = [task.title, ""]
        if task.description:
            lines.append(task.description)
        if task.acceptance_criteria:
            lines.append("Acceptance criteria:")
            lines.extend(f"- {criterion}" for criterion in task.acceptance_criteria)
        return "\n".join(lines)

    async def run_task(self, task: Task, context: RepoContext) -> ExecutionResult:
        """Run the configured command once for the task."""
        logs: list[str] = [f"[{self.name}] Running task {task.id} ({task.title})"]
        env = {
            **os.environ,
            **self.env,
            "FACTORY_TASK": self._task_instruction(task),
            "FACTORY_REPO": str(context.repo_path),
            "FACTORY_BRANCH": context.branch,
        }

        try:
            if isinstance(self.command, str):
                proc = await asyncio.create_subprocess_shell(
                    self.command,
                    cwd=str(context.repo_path),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *self.command,
                    cwd=str(context.repo_path),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
        except FileNotFoundError as exc:
            logs.append(f"[{self.name}] Command not found: {exc}")
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                output_logs=logs,
                error=f"command not found: {exc}",
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logs.append(
                f"[{self.name}] Execution timed out after "
                f"{self.timeout_seconds:g}s; process killed."
            )
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                output_logs=logs,
                error=f"timed out after {self.timeout_seconds:g}s",
            )

        logs.extend(f"[stdout] {line}" for line in stdout.decode(errors="replace").splitlines())
        logs.extend(f"[stderr] {line}" for line in stderr.decode(errors="replace").splitlines())

        if proc.returncode == 0:
            logs.append(f"[{self.name}] Task {task.id} finished successfully (exit 0).")
            return ExecutionResult(status=ExecutionStatus.SUCCESS, output_logs=logs)

        if proc.returncode == self.blocked_exit_code:
            logs.append(f"[{self.name}] Task {task.id} needs human input (exit {proc.returncode}).")
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED,
                output_logs=logs,
                questions=[line for line in logs if line.startswith(("[stdout]", "[stderr]"))],
            )

        logs.append(f"[{self.name}] Task {task.id} failed with exit code {proc.returncode}.")
        return ExecutionResult(
            status=ExecutionStatus.ERROR,
            output_logs=logs,
            error=f"exit code {proc.returncode}",
        )
