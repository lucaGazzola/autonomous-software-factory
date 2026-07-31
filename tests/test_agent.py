"""Shell agent tests: exit-code mapping, env delivery, timeout."""

from __future__ import annotations

from factory.agent import ShellAgent
from factory.models import ExecutionStatus, RepoContext
from tests.conftest import make_task

TASK = make_task(id="TASK-001", title="Add retries", description="Implement retry logic.")


async def test_exit_zero_is_success():
    agent = ShellAgent("true")
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.SUCCESS


async def test_blocked_exit_code_is_blocked():
    agent = ShellAgent("echo need input; exit 2")
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.BLOCKED
    assert any("need input" in line for line in result.questions)


async def test_other_exit_code_is_error():
    agent = ShellAgent("exit 1")
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.ERROR
    assert "exit code 1" in (result.error or "")


async def test_missing_command_is_error():
    agent = ShellAgent("/nonexistent/binary --flag")
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.ERROR


async def test_timeout_is_error():
    agent = ShellAgent("sleep 5", timeout_seconds=0.2)
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.ERROR
    assert "timed out" in (result.error or "")


async def test_no_timeout_runs_to_completion():
    agent = ShellAgent("sleep 0.2", timeout_seconds=None)
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.SUCCESS


async def test_argv_list_command(tmp_path):
    agent = ShellAgent(["sh", "-c", "exit 2"])
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.BLOCKED


async def test_task_instruction_via_env(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "out.txt").write_text("", encoding="utf-8")
    agent = ShellAgent('echo "$FACTORY_TASK" > out.txt')
    await agent.run_task(TASK, RepoContext(repo_path=repo, branch="main"))
    output = (repo / "out.txt").read_text(encoding="utf-8")
    assert "Add retries" in output
    assert "Implement retry logic." in output
