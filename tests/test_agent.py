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


async def test_timeout_includes_streamed_output():
    """Lines printed before a timeout must appear in output_logs."""
    agent = ShellAgent(
        "echo pre-timeout-marker; sleep 5",
        timeout_seconds=0.5,
    )
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.ERROR
    assert "timed out" in (result.error or "")
    assert any("pre-timeout-marker" in line for line in result.output_logs)


async def test_output_log_window_is_bounded():
    """A chatty agent must not retain more than the last 1000 process lines."""
    # 1500 numbered lines; only the trailing window should remain.
    agent = ShellAgent("python3 -c \"import sys; [print(i) for i in range(1500)]\"")
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.SUCCESS
    stream = [line for line in result.output_logs if line.startswith(("[stdout]", "[stderr]"))]
    assert len(stream) == 1000
    assert stream[0] == "[stdout] 500"
    assert stream[-1] == "[stdout] 1499"


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


async def test_per_task_command_override(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "marker.txt").write_text("", encoding="utf-8")
    agent = ShellAgent("exit 1")
    result = await agent.run_task(
        TASK,
        RepoContext(repo_path=repo, branch="main"),
        command='echo "$FACTORY_TASK" > marker.txt',
    )
    assert result.status is ExecutionStatus.SUCCESS
    output = (repo / "marker.txt").read_text(encoding="utf-8")
    assert "Add retries" in output


async def test_falls_back_to_configured_command():
    agent = ShellAgent("exit 1")
    result = await agent.run_task(TASK, RepoContext())
    assert result.status is ExecutionStatus.ERROR


async def test_per_task_timeout_override():
    agent = ShellAgent("sleep 5")
    result = await agent.run_task(TASK, RepoContext(), timeout_seconds=0.2)
    assert result.status is ExecutionStatus.ERROR
    assert "timed out after 0.2s" in (result.error or "")


async def test_per_task_timeout_null_uses_configured_timeout():
    agent = ShellAgent("sleep 0.2", timeout_seconds=5)
    result = await agent.run_task(TASK, RepoContext(), timeout_seconds=None)
    assert result.status is ExecutionStatus.SUCCESS


async def test_per_task_argv_list_override():
    agent = ShellAgent("exit 1")
    result = await agent.run_task(
        TASK,
        RepoContext(),
        command=["sh", "-c", "exit 2"],
    )
    assert result.status is ExecutionStatus.BLOCKED
