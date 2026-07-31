"""Git repository management for task isolation and delivery.

``GitManager`` wraps the ``git`` CLI (via subprocess) — no Python git
library dependency — and offers two layers:

* low-level sync primitives (``is_clean``, ``checkout``, ``commit``,
  ``merge``, ``push``, ``create_pr``), and
* the async workflow the orchestrator drives: ``prepare_task_branch``
  (ensure clean, cut ``factory/task-<id>`` off the base branch),
  ``commit_task_work`` (stage + commit whatever the agent left), and
  ``deliver_task`` (merge / push / PR per ``GitConfig.strategy``).

Every command is executed with ``git -C <repo_path>`` and fails loudly
through :class:`GitError` so the orchestrator can fail the task instead of
silently proceeding on a broken repository.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

from factory.core.models import GitConfig, Task


class GitError(RuntimeError):
    """Raised when a git command cannot be executed or fails."""


class GitManager:
    """Run git commands against a single repository."""

    def __init__(self, repo_path: str | Path) -> None:
        self.repo_path = Path(repo_path)

    # ------------------------------------------------------------------ #
    # Low-level sync primitives                                          #
    # ------------------------------------------------------------------ #

    def _run(self, *args: str, check: bool = True) -> str:
        """Execute ``git -C <repo> <args>`` and return combined output."""
        if not shutil.which("git"):
            raise GitError("the 'git' executable was not found on PATH")
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.repo_path), *args],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out") from exc
        if check and proc.returncode != 0:
            raise GitError(
                f"git {args[0]} failed (exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout.strip()

    def is_repo(self) -> bool:
        """Return whether the path is inside a git repository."""
        try:
            self._run("rev-parse", "--git-dir")
            return True
        except GitError:
            return False

    def current_branch(self) -> str | None:
        """Return the checked-out branch name, or ``None`` when detached."""
        try:
            branch = self._run("rev-parse", "--abbrev-ref", "HEAD")
        except GitError:
            return None
        return None if branch == "HEAD" else branch

    def is_clean(self) -> bool:
        """Return whether the working tree has no staged/unstaged/untracked changes."""
        return not bool(self._run("status", "--porcelain"))

    def ensure_clean(self) -> None:
        """Raise :class:`GitError` when the working tree is not clean."""
        dirty = self._run("status", "--porcelain")
        if dirty:
            raise GitError("working tree is not clean; refusing to start a task:\n" + dirty)

    def branch_exists(self, branch: str) -> bool:
        """Return whether ``branch`` exists locally."""
        try:
            self._run("rev-parse", "--verify", f"refs/heads/{branch}")
            return True
        except GitError:
            return False

    def ensure_branch(self, branch: str) -> None:
        """Create ``branch`` from HEAD when it does not exist yet."""
        if not self.branch_exists(branch):
            self._run("switch", "-c", branch)

    def checkout(self, branch: str, *, create: bool = False) -> None:
        """Check out ``branch`` (creating it from HEAD when ``create`` is set)."""
        args = ["switch"]
        if create:
            args += ["-c"]
        args.append(branch)
        self._run(*args)

    def has_changes(self) -> bool:
        """Return whether anything would be staged by ``git add -A``."""
        return bool(self._run("status", "--porcelain"))

    def commit_all(self, message: str) -> str | None:
        """Stage all changes and commit; returns the short sha, or ``None`` if nothing to commit."""
        self._run("add", "-A")
        if not self.has_changes():
            return None
        self._run("commit", "-m", message)
        return self._run("rev-parse", "--short", "HEAD")

    def merge(self, branch: str, message: str) -> None:
        """Merge ``branch`` into the current branch (no fast-forward)."""
        self._run("merge", "--no-ff", "--no-edit", "-m", message, branch)

    def push(self, remote: str = "origin", branch: str | None = None) -> None:
        """Push the current branch (or ``branch``) to ``remote``."""
        self._run("push", remote, branch or self._run("rev-parse", "--abbrev-ref", "HEAD"))

    def delete_branch(self, branch: str) -> None:
        """Delete ``branch``; safe to call when it does not exist."""
        try:
            self._run("branch", "-D", branch, check=False)
        except GitError:
            pass

    def reset_hard(self) -> None:
        """Discard all uncommitted changes in the working tree."""
        self._run("reset", "--hard", "HEAD")

    def snapshot(self) -> dict[str, Any]:
        """Collect a compact view of the repository state for scans/reports.

        Best-effort by design: when git fails (not a repository, corrupt
        index, ...) an empty snapshot is returned so consumers can degrade
        to static analysis instead of crashing.
        """
        try:
            return {
                "branch": self.current_branch(),
                "clean": self.is_clean(),
                "changes": self._run("status", "--porcelain").splitlines(),
                "tracked_files": self._run("ls-files").splitlines(),
            }
        except GitError:
            return {"branch": None, "clean": False, "changes": [], "tracked_files": []}

    # ------------------------------------------------------------------ #
    # Async wrappers (run git in a worker thread)                         #
    # ------------------------------------------------------------------ #

    async def a_is_repo(self) -> bool:
        return await asyncio.to_thread(self.is_repo)

    async def a_ensure_clean(self) -> None:
        await asyncio.to_thread(self.ensure_clean)

    async def a_snapshot(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.snapshot)

    # ------------------------------------------------------------------ #
    # Orchestrator workflows                                              #
    # ------------------------------------------------------------------ #

    async def prepare_task_branch(self, task: Task, config: GitConfig) -> str:
        """Ensure the repo is clean and cut a branch for ``task``.

        Returns the branch name the agent should operate on.

        Raises:
            GitError: If the repository is not a git repo, the working tree
                is dirty, or git refuses the branch operations.
        """
        if not await self.a_is_repo():
            raise GitError(f"{self.repo_path} is not a git repository")

        await self.a_ensure_clean()

        branch = f"{config.branch_prefix}{task.id}"
        await asyncio.to_thread(self.ensure_branch, config.base_branch)
        exists = await asyncio.to_thread(self.branch_exists, branch)
        if exists:
            await asyncio.to_thread(self.checkout, branch)
        else:
            await asyncio.to_thread(self.checkout, branch, create=True)
        return branch

    async def commit_task_work(self, task: Task, message: str) -> str | None:
        """Stage and commit whatever the agent left in the working tree.

        Returns the short commit sha, or ``None`` when there was nothing to
        commit (agent made no changes).
        """
        return await asyncio.to_thread(self.commit_all, message)

    async def deliver_task(
        self,
        task: Task,
        branch: str,
        config: GitConfig,
        remote: str | None = None,
    ) -> str:
        """Finalize a successful task branch according to ``config.strategy``.

        ``merge`` folds the branch into ``base_branch`` and pushes when a
        remote is available; ``push`` uploads the branch; ``pr`` opens a
        pull request via the ``gh`` CLI; ``none`` leaves the branch local.

        Returns a short human-readable description of what was done.
        """
        if config.strategy == "merge":
            await asyncio.to_thread(self.checkout, config.base_branch)
            await asyncio.to_thread(
                self.merge,
                branch,
                f"factory: merge {branch} ({task.title})",
            )
            await asyncio.to_thread(self.delete_branch, branch)
            if remote is not None:
                await asyncio.to_thread(self.push, config.remote_name, config.base_branch)
                return f"merged {branch} into {config.base_branch} and pushed"
            return f"merged {branch} into {config.base_branch}"

        if config.strategy == "push":
            await asyncio.to_thread(self.push, config.remote_name, branch)
            return f"pushed {branch}"

        if config.strategy == "pr":
            url = await asyncio.to_thread(self.create_pr, task, branch, config)
            if url is None:
                return f"committed on {branch}; gh CLI unavailable, no PR opened"
            return f"opened PR {url}"

        return f"committed on local branch {branch}"

    def create_pr(self, task: Task, branch: str, config: GitConfig) -> str | None:
        """Open a pull request with ``gh``; returns the PR URL or ``None``.

        Returns ``None`` (and never raises) when ``gh`` is missing or the
        command fails — a missing PR tool must not fail the task.
        """
        if not shutil.which("gh"):
            return None
        body = task.description or task.title
        args = [
            "gh",
            "pr",
            "create",
            "--base",
            config.base_branch,
            "--head",
            branch,
            "--title",
            f"factory: {task.title}",
            "--body",
            body,
        ]
        for label in config.pr_labels:
            args += ["--label", label]
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    async def abandon_task_branch(self, task: Task, branch: str, config: GitConfig) -> None:
        """Discard uncommitted agent work and delete the task branch.

        Called when a task fails permanently so the repository stays clean
        and subsequent tasks are not blocked by stale state.
        """
        await asyncio.to_thread(self.checkout, config.base_branch)
        await asyncio.to_thread(self.reset_hard)
        await asyncio.to_thread(self.delete_branch, branch)
