"""GitManager tests against real throwaway git repositories.

Every test builds its own repo under ``tmp_path`` (``git init`` + one
commit) so branch isolation, commit, and merge behavior are exercised for
real without touching the host repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from factory.adapters.git_manager import GitError, GitManager
from factory.core.models import GitConfig, Task


def git(repo, *args):
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def init_repo(path, filename: str = "hello.txt", content: str = "hello\n") -> None:
    """Create a git repo with one committed file and a known identity."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "factory@test.local")
    git(path, "config", "user.name", "Factory Test")
    (path / filename).write_text(content, encoding="utf-8")
    git(path, "add", "-A")
    git(path, "commit", "-m", "initial")


@pytest.fixture
def repo(tmp_path) -> str:
    path = tmp_path / "repo"
    init_repo(path)
    return str(path)


@pytest.fixture
def manager(repo) -> GitManager:
    return GitManager(repo)


def test_is_repo_and_branch(manager, repo):
    assert manager.is_repo()
    assert manager.current_branch() == "main"


def test_clean_and_dirty(manager, repo):
    assert manager.is_clean()
    (Path(repo) / "hello.txt").write_text("dirty\n", encoding="utf-8")
    assert not manager.is_clean()
    with pytest.raises(GitError, match="not clean"):
        manager.ensure_clean()


def test_non_repo_raises(tmp_path):
    manager = GitManager(tmp_path / "nope")
    assert not manager.is_repo()
    with pytest.raises(GitError, match="failed"):
        manager.ensure_clean()


async def test_prepare_task_branch_creates_branch(manager, repo):
    task = Task(id="T-1", title="Do work")
    branch = await manager.prepare_task_branch(task, GitConfig())
    assert branch == "factory/task-T-1"
    assert manager.current_branch() == "factory/task-T-1"
    assert manager.is_clean()


async def test_prepare_task_branch_reuses_existing(manager, repo):
    task = Task(id="T-1", title="Do work")
    git(Path(repo), "switch", "-c", "factory/task-T-1")
    (Path(repo) / "hello.txt").write_text("work\n", encoding="utf-8")
    git(Path(repo), "commit", "-am", "wip")
    branch = await manager.prepare_task_branch(task, GitConfig())
    assert branch == "factory/task-T-1"
    assert manager.current_branch() == "factory/task-T-1"


async def test_prepare_task_branch_dirty_tree_raises(manager, repo):
    (Path(repo) / "hello.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(GitError, match="not clean"):
        await manager.prepare_task_branch(Task(id="T-1", title="x"), GitConfig())


async def test_commit_task_work(manager, repo):
    (Path(repo) / "new.txt").write_text("new\n", encoding="utf-8")
    sha = await manager.commit_task_work(Task(id="T-1", title="x"), "factory: work")
    assert sha is not None
    assert manager.is_clean()
    # Nothing to commit -> None
    assert await manager.commit_task_work(Task(id="T-1", title="x"), "again") is None


async def test_merge_strategy(manager, repo):
    task = Task(id="T-1", title="Do work")
    branch = await manager.prepare_task_branch(task, GitConfig())
    (Path(repo) / "hello.txt").write_text("merged work\n", encoding="utf-8")
    await manager.commit_task_work(task, "factory: work")
    delivery = await manager.deliver_task(task, branch, GitConfig(strategy="merge"), remote=None)
    assert "merged" in delivery
    assert manager.current_branch() == "main"
    assert (Path(repo) / "hello.txt").read_text(encoding="utf-8") == "merged work\n"


async def test_abandon_task_branch(manager, repo):
    task = Task(id="T-1", title="Do work")
    branch = await manager.prepare_task_branch(task, GitConfig())
    (Path(repo) / "hello.txt").write_text("doomed\n", encoding="utf-8")
    await manager.abandon_task_branch(task, branch, GitConfig())
    assert manager.current_branch() == "main"
    assert manager.is_clean()
    assert not manager.branch_exists(branch)


def test_snapshot_lists_tracked_files(manager):
    snapshot = manager.snapshot()
    assert snapshot["branch"] == "main"
    assert snapshot["clean"] is True
    assert "hello.txt" in snapshot["tracked_files"]
