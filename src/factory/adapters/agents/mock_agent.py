"""Deterministic simulated agent.

Useful for demos, dry runs, and tests: instead of invoking a real coding
agent it produces plausible logs, artifacts, and statuses based on the
task's ``metadata["simulate"]`` value (``success``, ``blocked``, or
``error``). Blocked runs are resolved into successes once the HITL channel
delivers a human reply, mirroring how a real agent consumes guidance.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from factory.adapters.agents.base import BaseAgentAdapter
from factory.core.models import ExecutionResult, ExecutionStatus, RepoContext, Task

_VALID_BEHAVIORS = ("success", "blocked", "flaky", "error")


class MockAgentAdapter(BaseAgentAdapter):
    """Simulated agent whose behavior is driven by task metadata."""

    name = "mock"

    def __init__(
        self,
        default_behavior: str = "success",
        delay_seconds: float = 0.0,
        artifact_dir: str | Path | None = None,
    ) -> None:
        """Configure the simulated agent.

        Args:
            default_behavior: Behavior when the task has no ``simulate``
                metadata: one of ``success``, ``blocked``, ``error``.
            delay_seconds: Artificial latency to make runs observable.
            artifact_dir: Where to drop fake artifacts; defaults to
                ``<repo_path>/artifacts``.
        """
        if default_behavior not in _VALID_BEHAVIORS:
            raise ValueError(
                f"default_behavior must be one of {_VALID_BEHAVIORS}, got {default_behavior!r}"
            )
        self.default_behavior = default_behavior
        self.delay_seconds = delay_seconds
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None

    async def run_task(self, task: Task, context: RepoContext) -> ExecutionResult:
        """Simulate a full agent lifecycle for the task."""
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        behavior = task.metadata.get("simulate", self.default_behavior)
        logs: list[str] = [
            f"[{self.name}] Received task {task.id}: {task.title}",
            f"[{self.name}] Simulating behavior={behavior!r} ...",
        ]

        if behavior == "error":
            logs.append(f"[{self.name}] Simulated failure while executing {task.id}.")
            return ExecutionResult(
                status=ExecutionStatus.ERROR,
                output_logs=logs,
                error="mock: simulated agent failure",
            )

        if behavior == "blocked":
            guidance = context.feedback_for(task.id)
            if guidance:
                logs.append(f"[{self.name}] Applying human guidance: {guidance!r}")
                logs.append(f"[{self.name}] Task {task.id} completed after human input.")
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    output_logs=logs,
                    artifacts=[str(self._write_artifact(task, context))],
                )
            logs.append(f"[{self.name}] Missing information; requesting human guidance.")
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED,
                output_logs=logs,
                questions=[
                    f"How should task {task.id} ({task.title}) be implemented?",
                    "Provide retry instructions or guidance.",
                ],
            )

        if behavior == "flaky":
            if context.attempt_for(task.id) >= 1:
                logs.append(
                    f"[{self.name}] Transient block cleared on retry; task {task.id} completed."
                )
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    output_logs=logs,
                    artifacts=[str(self._write_artifact(task, context))],
                )
            logs.append(f"[{self.name}] Transient infrastructure failure; retrying may succeed.")
            return ExecutionResult(
                status=ExecutionStatus.BLOCKED,
                output_logs=logs,
                questions=["Transient failure detected; retrying the task may succeed."],
            )

        logs.append(f"[{self.name}] Writing implementation for {task.title} ...")
        logs.append(f"[{self.name}] Running verification ... all checks passed.")
        artifact = self._write_artifact(task, context)
        logs.append(f"[{self.name}] Produced artifact: {artifact}")
        return ExecutionResult(
            status=ExecutionStatus.SUCCESS,
            output_logs=logs,
            artifacts=[str(artifact)],
        )

    def _write_artifact(self, task: Task, context: RepoContext) -> Path:
        """Write a small markdown file representing the agent's output."""
        base_dir = self.artifact_dir or Path(context.repo_path) / "artifacts"
        base_dir.mkdir(parents=True, exist_ok=True)
        artifact = base_dir / f"{task.id}.md"
        artifact.write_text(
            f"# {task.title}\n\nSimulated implementation produced by the mock agent.\n",
            encoding="utf-8",
        )
        return artifact
