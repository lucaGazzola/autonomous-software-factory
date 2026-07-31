"""Factory cycle tests: task run, refactor pass, blocker file, git behavior."""

from __future__ import annotations

from factory.backlog import JSONBacklog
from factory.factory import Factory
from factory.git import GitManager
from factory.models import ExecutionResult, ExecutionStatus, TaskStatus
from tests.conftest import FakeAgent, git, make_config, make_task


def make_factory(git_repo, tmp_path, **overrides) -> tuple[Factory, FakeAgent, JSONBacklog]:
    config = make_config(git_repo, tmp_path, **overrides)
    agent = FakeAgent()
    backlog = JSONBacklog(config.backlog)
    factory = Factory(config, backlog, agent, GitManager(git_repo))
    return factory, agent, backlog


async def test_task_success_is_committed_on_main(git_repo, tmp_path):
    factory, agent, backlog = make_factory(git_repo, tmp_path)
    await backlog.create_task(make_task())

    agent.result = ExecutionResult(status=ExecutionStatus.SUCCESS)
    agent.effect = lambda: (git_repo / "app.py").write_text("def answer():\n    return 7\n", encoding="utf-8")

    outcome = await factory.run_cycle()

    assert outcome == "task"
    assert (await backlog.get_task("TASK-001")).status is TaskStatus.COMPLETED
    assert git(git_repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert git(git_repo, "log", "-1", "--format=%s") == "factory: Do the thing (#TASK-001)"
    assert "factory" not in git(git_repo, "branch")


async def test_task_success_without_changes_commits_nothing(git_repo, tmp_path):
    factory, agent, backlog = make_factory(git_repo, tmp_path)
    await backlog.create_task(make_task())
    agent.result = ExecutionResult(status=ExecutionStatus.SUCCESS)

    outcome = await factory.run_cycle()

    assert outcome == "task"
    assert (await backlog.get_task("TASK-001")).status is TaskStatus.COMPLETED
    assert git(git_repo, "rev-list", "--count", "HEAD") == "1"


async def test_task_success_pushes_to_remote(git_repo, tmp_path):
    remote = tmp_path / "remote.git"
    git(git_repo, "clone", "--bare", str(git_repo), str(remote))
    git(git_repo, "remote", "add", "origin", str(remote))
    factory, agent, backlog = make_factory(git_repo, tmp_path, remote="origin")
    await backlog.create_task(make_task())
    agent.result = ExecutionResult(status=ExecutionStatus.SUCCESS)
    agent.effect = lambda: (git_repo / "feature.txt").write_text("done\n", encoding="utf-8")

    await factory.run_cycle()

    assert git(remote, "log", "-1", "--format=%s") == "factory: Do the thing (#TASK-001)"


async def test_task_error_is_failed_and_work_discarded(git_repo, tmp_path):
    factory, agent, backlog = make_factory(git_repo, tmp_path)
    await backlog.create_task(make_task())
    agent.result = ExecutionResult(status=ExecutionStatus.ERROR, error="boom")

    def wreck():
        (git_repo / "app.py").write_text("garbage\n", encoding="utf-8")

    agent.effect = wreck

    await factory.run_cycle()

    assert (await backlog.get_task("TASK-001")).status is TaskStatus.FAILED
    assert await GitManager(git_repo).a_is_clean()
    assert "def answer()" in (git_repo / "app.py").read_text(encoding="utf-8")


async def test_task_blocked_writes_blocker_file_and_pauses(git_repo, tmp_path):
    factory, agent, backlog = make_factory(git_repo, tmp_path)
    await backlog.create_task(make_task(description="Decide the retry policy."))
    agent.result = ExecutionResult(
        status=ExecutionStatus.BLOCKED,
        questions=["Which retry policy should I use?"],
    )
    agent.effect = lambda: (git_repo / "wip.txt").write_text("partial\n", encoding="utf-8")

    await factory.run_cycle()

    assert (await backlog.get_task("TASK-001")).status is TaskStatus.BLOCKED
    blocker = factory.config.blocker_file.read_text(encoding="utf-8")
    assert "TASK-001" in blocker
    assert "Which retry policy should I use?" in blocker
    assert "set the status of `TASK-001` back to `OPEN`" in blocker
    assert git(git_repo, "log", "-1", "--format=%s") == "factory: Do the thing (#TASK-001) [partial]"


async def test_blocked_task_pauses_until_reopened(git_repo, tmp_path):
    factory, agent, backlog = make_factory(git_repo, tmp_path)
    await backlog.create_task(make_task())
    agent.result = ExecutionResult(status=ExecutionStatus.BLOCKED, questions=["?"])
    await factory.run_cycle()
    assert await factory.run_cycle() == "blocked"

    await backlog.update_status("TASK-001", TaskStatus.OPEN)
    agent.result = ExecutionResult(status=ExecutionStatus.SUCCESS)
    agent.effect = lambda: (git_repo / "note.txt").write_text("done\n", encoding="utf-8")
    await factory.run_cycle()

    assert (await backlog.get_task("TASK-001")).status is TaskStatus.COMPLETED
    assert not factory.config.blocker_file.exists()


async def test_refactor_pass_when_backlog_empty(git_repo, tmp_path):
    factory, agent, _backlog = make_factory(git_repo, tmp_path)
    agent.result = ExecutionResult(status=ExecutionStatus.SUCCESS)

    def refactor():
        (git_repo / "app.py").write_text("def answer():\n    return 42  # neat\n", encoding="utf-8")

    agent.effect = refactor

    outcome = await factory.run_cycle()

    assert outcome == "refactor"
    task, context = agent.calls[0]
    assert task.id == "REFACTOR"
    assert context.repo_path == git_repo
    assert git(git_repo, "log", "-1", "--format=%s") == "factory: refactoring pass"


async def test_refactor_with_nothing_to_do_commits_nothing(git_repo, tmp_path):
    factory, agent, _backlog = make_factory(git_repo, tmp_path)
    agent.result = ExecutionResult(status=ExecutionStatus.SUCCESS)
    await factory.run_cycle()
    assert git(git_repo, "rev-list", "--count", "HEAD") == "1"


async def test_refactor_blocked_writes_blocker(git_repo, tmp_path):
    factory, agent, _backlog = make_factory(git_repo, tmp_path)
    agent.result = ExecutionResult(status=ExecutionStatus.BLOCKED, questions=["License question?"])

    await factory.run_cycle()

    blocker = factory.config.blocker_file.read_text(encoding="utf-8")
    assert "License question?" in blocker
    assert "delete this file" in blocker


async def test_paused_while_blocker_file_exists(git_repo, tmp_path):
    factory, agent, _backlog = make_factory(git_repo, tmp_path)
    factory.config.blocker_file.write_text("stale", encoding="utf-8")
    assert await factory.run_cycle() == "paused"
    assert agent.calls == []


async def test_dirty_tree_skips_task(git_repo, tmp_path):
    factory, _agent, backlog = make_factory(git_repo, tmp_path)
    await backlog.create_task(make_task())
    (git_repo / "manual.txt").write_text("wip\n", encoding="utf-8")
    outcome = await factory.run_cycle()
    assert outcome == "dirty"
    assert (await backlog.get_task("TASK-001")).status is TaskStatus.OPEN


async def test_task_instruction_reaches_agent(git_repo, tmp_path):
    factory, agent, backlog = make_factory(git_repo, tmp_path)
    task = make_task(acceptance_criteria=["tests pass"])
    await backlog.create_task(task)
    await factory.run_cycle()
    called_task, _ = agent.calls[0]
    assert called_task.id == "TASK-001"
