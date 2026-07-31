"""Tests for the core data contracts."""

import json
from pathlib import Path

import pytest

from factory.core.models import (
    AgentConfig,
    ExecutionResult,
    ExecutionStatus,
    GitConfig,
    ProjectConfig,
    RefactoringConfig,
    RepoContext,
    ResponseAction,
    Task,
    TaskStatus,
    UserResponse,
)


def test_task_defaults():
    task = Task(id="T-1", title="Do a thing")
    assert task.status is TaskStatus.OPEN
    assert task.description == ""
    assert task.metadata == {}
    assert task.created_at is not None


def test_task_execution_fields_roundtrip():
    task = Task(
        id="T-1",
        title="Do a thing",
        description="Properly.",
        dependencies=["T-0"],
        acceptance_criteria=["works", "is tested"],
        files_to_modify=["src/main.py"],
    )
    restored = Task.model_validate(json.loads(task.model_dump_json()))
    assert restored == task
    assert Task(id="T-2", title="Plain").dependencies == []


def test_task_json_roundtrip():
    task = Task(
        id="T-1",
        title="Do a thing",
        description="Properly.",
        status=TaskStatus.IN_PROGRESS,
        metadata={"simulate": "blocked"},
    )
    restored = Task.model_validate(json.loads(task.model_dump_json()))
    assert restored == task


def test_execution_result_defaults():
    result = ExecutionResult(status=ExecutionStatus.SUCCESS)
    assert result.output_logs == []
    assert result.artifacts == []
    assert result.questions == []
    assert result.error is None


def test_repo_context_feedback_roundtrip():
    context = RepoContext()
    assert context.feedback_for("T-1") is None
    updated = context.with_feedback("T-1", "Use feature X")
    assert context.feedback_for("T-1") is None  # immutability
    assert updated.feedback_for("T-1") == "Use feature X"
    assert updated.repo_path == context.repo_path


def test_user_response_enum_coercion():
    response = UserResponse(task_id="T-1", action="retry")
    assert response.action is ResponseAction.RETRY


def test_agent_config_accepts_list_or_string_command():
    assert AgentConfig(command=["aider", "--message", "x"]).command == ["aider", "--message", "x"]
    assert AgentConfig(command="ls -la").timeout_seconds == 300.0


def test_project_config_defaults():
    project = ProjectConfig(project_name="demo")
    assert project.repo_path == Path(".")
    assert project.schedule_interval_minutes == 60
    assert project.backlog_source == "backlog.json"
    assert project.git.enabled is False
    assert project.git.strategy == "push"
    assert project.refactoring.enabled is True


def test_project_config_backlog_path_resolution():
    project = ProjectConfig(
        project_name="demo", repo_path="/tmp/repo", backlog_source="backlog.json"
    )
    assert project.backlog_path == Path("/tmp/repo/backlog.json")
    absolute = ProjectConfig(
        project_name="demo", repo_path="/tmp/repo", backlog_source="/data/backlog.json"
    )
    assert absolute.backlog_path == Path("/data/backlog.json")


def test_project_config_rejects_blank_backlog_source():
    with pytest.raises(ValueError):
        ProjectConfig(project_name="demo", backlog_source="   ")


def test_project_config_rejects_bad_schedule():
    with pytest.raises(ValueError):
        ProjectConfig(project_name="demo", schedule_interval_minutes=0)


def test_git_and_refactoring_config_validation():
    assert GitConfig(strategy="merge").strategy == "merge"
    with pytest.raises(ValueError):
        GitConfig(strategy="squash")
    assert RefactoringConfig(max_tasks_per_scan=2).max_tasks_per_scan == 2
    with pytest.raises(ValueError):
        RefactoringConfig(max_tasks_per_scan=0)


def test_project_config_json_roundtrip():
    project = ProjectConfig(
        project_name="demo",
        repo_path="/tmp/repo",
        git_remote="https://example.com/repo.git",
        schedule_interval_minutes=30,
        backlog_source="backlog.json",
        agent_name="shell",
        agent=AgentConfig(command="ls"),
        git=GitConfig(enabled=True, strategy="merge"),
        refactoring=RefactoringConfig(enabled=False, cooldown_minutes=120),
    )
    restored = ProjectConfig.model_validate(json.loads(project.model_dump_json()))
    assert restored == project
