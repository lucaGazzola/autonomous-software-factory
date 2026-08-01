"""Model tests: task lifecycle statuses and factory config validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from factory.config import load_config
from factory.models import DEFAULT_REFACTOR_PROMPT, FactoryConfig, Task, TaskStatus


def test_task_defaults_to_open():
    task = Task(id="TASK-001", title="t")
    assert task.status is TaskStatus.OPEN
    assert task.description == ""
    assert task.acceptance_criteria == []


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
