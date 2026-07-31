"""Generic process-based agent adapter.

Runs any shell command as the "agent" — e.g. ``aider --message ...``,
``claude -p ...``, or a custom automation script — capturing its stdout
and stderr into the execution log. This is the uniform wrapper that makes
the factory agent-agnostic: anything you can invoke from a shell becomes
a first-class coding agent.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from factory.adapters.agents.base import BaseAgentAdapter
from factory.core.models import AgentConfig, ExecutionResult, ExecutionStatus, RepoContext, Task


class ShellAgentAdapter(BaseAgentAdapter):
    """Executes ``AgentConfig.command`` in a subprocess and maps its exit
    code onto an :class:`ExecutionResult`.

    Exit code ``0`` maps to SUCCESS, anything else to ERROR, and a timeout
    or missing executable also yields ERROR with a descriptive message. The
    adapter never blocks the orchestrator: stdout/stderr are consumed
    asynchronously and the process is killed on timeout.
    """

    name = "shell"

    def __init__(self, config: AgentConfig, name: str | None = None) -> None:
        """Wrap a process-based agent.

        Args:
            config: Command, environment, timeout, and working directory.
            name: Optional custom adapter name for audit comments.
        """
        self.config = config
        if name:
            self.name = name

    async def run_task(self, task: Task, context: RepoContext) -> ExecutionResult:
        """Run the configured command once for the task."""
        logs: list[str] = [f"[{self.name}] Running task {task.id} ({task.title})"]

        workdir = Path(self.config.workdir) if self.config.workdir else Path(context.repo_path)
        env = {**os.environ, **self.config.env}
        command = self.config.command

        try:
            if isinstance(command, str):
                proc = await asyncio.create_subprocess_shell(
                    command,
                    cwd=str(workdir),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=str(workdir),
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
                proc.communicate(), timeout=self.config.timeout_seconds
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            logs.append(
                f"[{self.name}] Execution timed out after "
                f"{self.config.timeout_seconds:g}s; process killed."
            )
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                output_logs=logs,
                error=f"timed out after {self.config.timeout_seconds:g}s",
            )

        logs.extend(f"[stdout] {line}" for line in stdout.decode(errors="replace").splitlines())
        logs.extend(f"[stderr] {line}" for line in stderr.decode(errors="replace").splitlines())

        if proc.returncode == 0:
            logs.append(f"[{self.name}] Task {task.id} finished successfully (exit 0).")
            return ExecutionResult(status=ExecutionStatus.SUCCESS, output_logs=logs)
        logs.append(f"[{self.name}] Task {task.id} failed with exit code {proc.returncode}.")
        return ExecutionResult(
            status=ExecutionStatus.ERROR,
            output_logs=logs,
            error=f"exit code {proc.returncode}",
        )
