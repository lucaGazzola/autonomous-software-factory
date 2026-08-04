"""Model tests: task lifecycle statuses and factory config validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factory.config import load_config
from factory.models import DEFAULT_REFACTOR_PROMPT, FactoryConfig, SandboxMode, Task, TaskStatus


def test_task_defaults_to_open():
    task = Task(id="TASK-001", title="t")
    assert task.status is TaskStatus.OPEN
    assert task.description == ""
    assert task.acceptance_criteria == []
    assert task.agent_command is None
    assert task.agent_timeout_seconds is None


def test_task_accepts_agent_override_fields():
    task = Task(
        id="TASK-001",
        title="t",
        agent_command="claude -p \"$FACTORY_TASK\" --model cheap",
        agent_timeout_seconds=120,
    )
    assert task.agent_command == "claude -p \"$FACTORY_TASK\" --model cheap"
    assert task.agent_timeout_seconds == 120


def test_task_agent_override_accepts_argv_list():
    task = Task(id="TASK-001", title="t", agent_command=["sh", "-c", "exit 0"])
    assert task.agent_command == ["sh", "-c", "exit 0"]


def test_task_rejects_blank_agent_command():
    with pytest.raises(ValidationError):
        Task(id="TASK-001", title="t", agent_command="")


def test_task_rejects_empty_agent_command_list():
    with pytest.raises(ValidationError):
        Task(id="TASK-001", title="t", agent_command=[])


def test_task_rejects_non_positive_agent_timeout():
    with pytest.raises(ValidationError):
        Task(id="TASK-001", title="t", agent_timeout_seconds=0)


def test_config_requires_agent_command():
    with pytest.raises(ValidationError):
        FactoryConfig(name="x", agent_command="")


def test_config_defaults():
    config = FactoryConfig(agent_command="aider --message hi")
    assert config.interval_minutes == 60
    assert config.branch == "main"
    assert config.blocked_exit_code == 2
    assert config.agent_timeout_seconds is None
    assert config.git_timeout_seconds == 120
    assert config.refactor_prompt == DEFAULT_REFACTOR_PROMPT
    assert config.web_port == 8787
    assert config.web_host == "127.0.0.1"
    assert config.telegram_bot_token is None
    assert config.telegram_chat_id is None


def test_config_sandbox_defaults_to_none():
    config = FactoryConfig(agent_command="x")
    assert config.agent_sandbox is SandboxMode.NONE
    assert config.agent_sandbox_image is None
    assert config.agent_sandbox_network == "none"
    assert config.agent_sandbox_mounts == []


def test_config_docker_sandbox_requires_image():
    with pytest.raises(ValidationError):
        FactoryConfig(agent_command="x", agent_sandbox="docker")


def test_config_docker_sandbox_accepts_image():
    config = FactoryConfig(
        agent_command="x",
        agent_sandbox="docker",
        agent_sandbox_image="forgeo-agent",
    )
    assert config.agent_sandbox is SandboxMode.DOCKER
    assert config.agent_sandbox_image == "forgeo-agent"


def test_config_rejects_blank_network_and_mounts():
    with pytest.raises(ValidationError):
        FactoryConfig(agent_command="x", agent_sandbox_network="")
    with pytest.raises(ValidationError):
        FactoryConfig(agent_command="x", agent_sandbox_mounts=["", "  "])


def test_config_rejects_zero_interval():
    with pytest.raises(ValidationError):
        FactoryConfig(agent_command="x", interval_minutes=0)


def test_load_config_resolves_relative_paths(tmp_path):
    (tmp_path / "factory.yaml").write_text(
        "name: demo\nrepo: ../repo\nbacklog: tasks.json\nagent_command: echo\n",
        encoding="utf-8",
    )
    config = load_config(tmp_path / "factory.yaml")
    assert config.repo == tmp_path.resolve() / "../repo"
    assert config.backlog == tmp_path.resolve() / "tasks.json"
    assert config.blocker_file == tmp_path.resolve() / "BLOCKER.md"


def test_load_config_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")
