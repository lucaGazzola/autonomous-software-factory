"""Tests for the core data contracts."""

import json

from factory.core.models import (
    AgentConfig,
    ExecutionResult,
    ExecutionStatus,
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
