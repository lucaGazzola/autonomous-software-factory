"""The factory: one scheduled run of one repository.

Each run does exactly one of three things:

1. A ``BLOCKED`` task exists -> write the blocker file (the detailed
   explanation of what the human must do) and pause.
2. An ``OPEN`` task exists -> execute it with the agent, commit and push the
   result on the main branch.
3. The backlog has nothing runnable -> run the agent in refactoring mode on
   the same branch, committing and pushing whatever it improves.

Whenever the agent signals BLOCKED, its partial work is committed and pushed
(no branches, nothing lost) and a ``BLOCKER.md`` file is written outside the
repository with exactly what the human needs to do. The factory stays paused
while that file exists.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from factory.agent import BaseAgent
from factory.backlog import JSONBacklog
from factory.git import GitError, GitManager
from factory.models import (
    ExecutionResult,
    ExecutionStatus,
    FactoryConfig,
    RepoContext,
    Task,
    TaskStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class BlockerEntry:
    """One blocked run to explain in the blocker file."""

    task: Task
    result: ExecutionResult
    instruction: str
    is_refactor: bool = False


class Factory:
    """Executes one scheduled run against a single repository."""

    def __init__(
        self,
        config: FactoryConfig,
        backlog: JSONBacklog,
        agent: BaseAgent,
        git: GitManager,
    ) -> None:
        self.config = config
        self.backlog = backlog
        self.agent = agent
        self.git = git

    async def run_cycle(self) -> str:
        """Execute one run; returns a short outcome label.

        Outcomes: ``blocked``, ``task``, ``paused``, ``refactor``, ``dirty``.
        """
        await self.git.a_ensure_branch(self.config.branch)
        tasks = await self.backlog.list_tasks()
        blocked = [t for t in tasks if t.status is TaskStatus.BLOCKED]
        if blocked:
            await self._write_blocker(
                [
                    BlockerEntry(
                        task=task,
                        result=ExecutionResult(
                            status=ExecutionStatus.BLOCKED,
                            questions=[f"Task {task.id} is blocked; see the backlog."],
                        ),
                        instruction=self._task_instruction(task),
                    )
                    for task in blocked
                ]
            )
            return "blocked"

        task = await self.backlog.fetch_next_task()
        if task is not None:
            if not await self.git.a_is_clean():
                logger.error(
                    "Working tree of %s is dirty; refusing to run task %s.",
                    self.config.repo,
                    task.id,
                )
                return "dirty"
            await self._run_task(task)
            return "task"

        if self.config.blocker_file.exists():
            logger.info("Blocker file present; factory paused until it is resolved.")
            return "paused"

        await self._refactor()
        return "refactor"

    # ------------------------------------------------------------------ #
    # Task execution                                                      #
    # ------------------------------------------------------------------ #

    async def _run_task(self, task: Task) -> None:
        """Execute one task: agent run, then commit/push on the main branch."""
        logger.info("Running task %s (%s)", task.id, task.title)
        result = await self.agent.run_task(
            task,
            RepoContext(repo_path=self.config.repo, branch=self.config.branch),
        )

        if result.status is ExecutionStatus.SUCCESS:
            if await self._commit_and_push(f"factory: {task.title} (#{task.id})", task=task):
                await self.backlog.update_status(task.id, TaskStatus.COMPLETED)
                self.config.blocker_file.unlink(missing_ok=True)
                logger.info("Task %s completed.", task.id)
            return

        if result.status is ExecutionStatus.BLOCKED:
            await self.backlog.update_status(task.id, TaskStatus.BLOCKED)
            if await self._commit_and_push(
                f"factory: {task.title} (#{task.id}) [partial]", task=task
            ):
                await self._write_blocker(
                    [
                        BlockerEntry(
                            task=task,
                            result=result,
                            instruction=self._task_instruction(task),
                        )
                    ]
                )
            logger.warning("Task %s is BLOCKED; blocker file written.", task.id)
            return

        await self._fail(task, result, discard_work=True)

    async def _fail(self, task: Task, result: ExecutionResult, *, discard_work: bool) -> None:
        """Discard the agent's work, mark the task FAILED, and log the error."""
        if discard_work:
            try:
                await self.git.a_reset_hard()
            except GitError as exc:
                logger.error("Could not discard agent work for %s: %s", task.id, exc)
        await self.backlog.update_status(task.id, TaskStatus.FAILED)
        detail = result.error or "no error detail provided"
        logger.error("Task %s FAILED: %s", task.id, detail)

    async def _commit_and_push(self, message: str, *, task: Task) -> bool:
        """Commit everything on the main branch and push when a remote is set.

        Returns ``False`` (and marks the task FAILED) when git refuses to
        cooperate.
        """
        try:
            sha = await self.git.a_commit_all(message)
        except GitError as exc:
            await self._fail(
                task,
                ExecutionResult(status=ExecutionStatus.ERROR, error=f"git: {exc}"),
                discard_work=True,
            )
            return False
        if sha is None:
            logger.info("No changes produced; nothing committed.")
            return True
        logger.info("Committed %s: %s", sha, message)
        if self.config.remote:
            try:
                await self.git.a_push(self.config.remote, self.config.branch)
                logger.info("Pushed %s to %s/%s", sha, self.config.remote, self.config.branch)
            except GitError as exc:
                logger.error("Push failed (work stays committed locally): %s", exc)
        return True

    # ------------------------------------------------------------------ #
    # Refactoring pass                                                    #
    # ------------------------------------------------------------------ #

    async def _refactor(self) -> None:
        """Run the agent in refactoring mode; commit and push its changes."""
        refactor_task = Task(
            id="REFACTOR",
            title="Refactoring pass",
            description=self.config.refactor_prompt,
        )
        logger.info("Backlog empty; running refactoring pass.")
        result = await self.agent.run_task(
            refactor_task,
            RepoContext(repo_path=self.config.repo, branch=self.config.branch),
        )

        if result.status is ExecutionStatus.SUCCESS:
            await self._commit_and_push("factory: refactoring pass", task=refactor_task)
            return
        if result.status is ExecutionStatus.BLOCKED:
            if await self._commit_and_push(
                "factory: refactoring pass [partial]", task=refactor_task
            ):
                await self._write_blocker(
                    [
                        BlockerEntry(
                            task=refactor_task,
                            result=result,
                            instruction=self.config.refactor_prompt,
                            is_refactor=True,
                        )
                    ]
                )
            logger.warning("Refactoring pass is BLOCKED; blocker file written.")
            return
        try:
            await self.git.a_reset_hard()
        except GitError as exc:
            logger.error("Could not discard refactoring changes: %s", exc)
        detail = result.error or "no error detail provided"
        logger.error("Refactoring pass FAILED: %s", detail)

    # ------------------------------------------------------------------ #
    # Blocker file                                                        #
    # ------------------------------------------------------------------ #

    def _task_instruction(self, task: Task) -> str:
        """The instruction the agent was given for ``task``."""
        lines = [f"{task.title}"]
        if task.description:
            lines.append(task.description)
        if task.acceptance_criteria:
            lines.append("Acceptance criteria:")
            lines.extend(f"- {criterion}" for criterion in task.acceptance_criteria)
        return "\n".join(lines)

    async def _write_blocker(self, entries: list[BlockerEntry]) -> None:
        """Write the blocker file with a detailed explanation of every block."""
        sections: list[str] = [
            "# BLOCKER: the software factory needs your input",
            "",
            "The coding agent could not finish without a human decision. The",
            f"factory is paused until this is resolved. Backlog: `{self.config.backlog}`.",
            "",
        ]
        for entry in entries:
            sections.append(self._render_entry(entry))
            sections.append("")
        self.config.blocker_file.parent.mkdir(parents=True, exist_ok=True)
        self.config.blocker_file.write_text("\n".join(sections), encoding="utf-8")
        logger.info("Blocker file written to %s", self.config.blocker_file)

    def _render_entry(self, entry: BlockerEntry) -> str:
        """Render the explanation and the required human action for one block."""
        task = entry.task
        lines = [f"## {task.id}: {task.title}", "", "### What the agent was asked to do", ""]
        lines += [f"> {line}" for line in entry.instruction.splitlines()]
        lines += ["", "### What the agent says it needs", ""]
        questions = entry.result.questions or entry.result.output_logs
        if not questions:
            questions = ["The agent did not explain what it needs."]
        lines += [f"> {line}" for line in questions[-10:]]
        if entry.is_refactor:
            lines += [
                "",
                "### What you must do",
                "",
                "Decide how to handle this refactoring question, then delete this file.",
                "The factory will continue on the next scheduled run.",
            ]
        else:
            lines += [
                "",
                "### What you must do",
                "",
                "1. Decide what the agent needs (edit the repository directly if required).",
                f"2. Open `{self.config.backlog}` and set the status of `{task.id}` back to `OPEN`",
                "   so the factory retries it — or delete the task if it should not be done.",
                "3. The factory will retry on the next scheduled run.",
            ]
        return "\n".join(lines)
