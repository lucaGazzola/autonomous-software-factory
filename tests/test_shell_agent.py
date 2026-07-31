"""Tests for the shell (process-based) agent adapter."""

import sys

from factory.adapters.agents import ShellAgentAdapter
from factory.core.models import AgentConfig, ExecutionStatus, RepoContext, Task


def make_agent(command, timeout=30.0, **kwargs):
    return ShellAgentAdapter(AgentConfig(command=command, timeout_seconds=timeout, **kwargs))


async def test_successful_command(tmp_path):
    agent = make_agent("echo hello-from-factory")
    result = await agent.run_task(Task(id="T-1", title="Echo"), RepoContext(repo_path=tmp_path))
    assert result.status is ExecutionStatus.SUCCESS
    assert any("hello-from-factory" in line for line in result.output_logs)


async def test_argv_list_command(tmp_path):
    agent = make_agent([sys.executable, "-c", "print('argv works')"])
    result = await agent.run_task(Task(id="T-1", title="Argv"), RepoContext(repo_path=tmp_path))
    assert result.status is ExecutionStatus.SUCCESS
    assert any("argv works" in line for line in result.output_logs)


async def test_failing_command(tmp_path):
    agent = make_agent(f"{sys.executable} -c 'import sys; sys.exit(3)'")
    result = await agent.run_task(Task(id="T-1", title="Fail"), RepoContext(repo_path=tmp_path))
    assert result.status is ExecutionStatus.ERROR
    assert result.error == "exit code 3"
    assert any("exit code 3" in line for line in result.output_logs)


async def test_timeout_kills_process(tmp_path):
    agent = make_agent(f"{sys.executable} -c 'import time; time.sleep(10)'", timeout=0.2)
    result = await agent.run_task(Task(id="T-1", title="Slow"), RepoContext(repo_path=tmp_path))
    assert result.status is ExecutionStatus.ERROR
    assert result.error is not None and "timed out" in result.error


async def test_missing_command_via_shell(tmp_path):
    """A shell string for a missing binary fails via the shell (exit 127)."""
    agent = make_agent("definitely-not-a-real-command-xyz")
    result = await agent.run_task(Task(id="T-1", title="Missing"), RepoContext(repo_path=tmp_path))
    assert result.status is ExecutionStatus.ERROR
    assert result.error == "exit code 127"
    assert any("not found" in line for line in result.output_logs)


async def test_missing_command_via_argv(tmp_path):
    """An argv list for a missing binary raises FileNotFoundError -> ERROR."""
    agent = make_agent(["definitely-not-a-real-command-xyz"])
    result = await agent.run_task(Task(id="T-1", title="Missing"), RepoContext(repo_path=tmp_path))
    assert result.status is ExecutionStatus.ERROR
    assert result.error is not None and "command not found" in result.error


async def test_env_and_workdir(tmp_path):
    (tmp_path / "marker.txt").write_text("ok", encoding="utf-8")
    agent = make_agent(
        f"{sys.executable} -c 'import os,sys; "
        "assert os.environ[\"FACTORY_FLAG\"] == \"1\"; "
        "assert os.path.exists(\"marker.txt\"); print(\"env-cwd-ok\")'",
        env={"FACTORY_FLAG": "1"},
    )
    result = await agent.run_task(
        Task(id="T-1", title="Env"),
        RepoContext(repo_path=tmp_path),
    )
    assert result.status is ExecutionStatus.SUCCESS
    assert any("env-cwd-ok" in line for line in result.output_logs)
