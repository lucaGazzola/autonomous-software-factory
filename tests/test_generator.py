"""Backlog generator tests: interview loop and task decomposition."""

from __future__ import annotations

import json

import pytest

from factory.generator.decomposer import DecompositionError, TaskDecomposer, extract_json
from factory.generator.interview import InterviewSession
from factory.models import Task


class FakeLLM:
    """Scripted chat client: serves queued replies, then repeats the last one."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        self.calls.append(messages)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


# --------------------------------------------------------------------- #
# extract_json                                                           #
# --------------------------------------------------------------------- #


def test_extract_json_from_fenced_block():
    payload = extract_json('Here you go:\n```json\n{"tasks": []}\n```\nEnjoy.')
    assert payload == {"tasks": []}


def test_extract_json_from_prose():
    payload = extract_json("The answer is [1, 2, 3].")
    assert payload == [1, 2, 3]


def test_extract_json_raises_without_payload():
    with pytest.raises(json.JSONDecodeError):
        extract_json("no json here")


# --------------------------------------------------------------------- #
# TaskDecomposer                                                         #
# --------------------------------------------------------------------- #

VALID_TASKS = json.dumps(
    [
        {
            "id": "setup",
            "title": "Set up project",
            "description": "Create the skeleton.",
            "dependencies": [],
            "acceptance_criteria": ["runs"],
            "files_to_modify": ["pyproject.toml"],
        },
        {
            "id": "engine",
            "title": "Build the engine",
            "description": "Core logic.",
            "dependencies": ["setup"],
            "acceptance_criteria": ["works"],
            "files_to_modify": [],
        },
    ]
)


def test_decomposer_produces_ordered_tasks():
    llm = FakeLLM(VALID_TASKS)
    tasks = TaskDecomposer(llm).decompose("spec")
    assert [t.id for t in tasks] == ["TASK-001", "TASK-002"]
    assert tasks[1].dependencies == ["TASK-001"]
    assert all(isinstance(t, Task) for t in tasks)


def test_decomposer_retries_on_invalid_output():
    llm = FakeLLM("not json", VALID_TASKS)
    tasks = TaskDecomposer(llm).decompose("spec")
    assert len(tasks) == 2
    assert len(llm.calls) == 2
    assert "rejected" in llm.calls[1][1]["content"]


def test_decomposer_fails_loudly_after_retries():
    llm = FakeLLM("not json", "still not json", "nope")
    with pytest.raises(DecompositionError):
        TaskDecomposer(llm, max_attempts=3).decompose("spec")


def test_decomposer_rejects_bad_topology():
    payload = json.dumps(
        [
            {"id": "a", "title": "A", "dependencies": ["b"], "description": ""},
            {"id": "b", "title": "B", "dependencies": [], "description": ""},
        ]
    )
    with pytest.raises(DecompositionError):
        TaskDecomposer(FakeLLM(payload)).decompose("spec")


def test_decomposer_rejects_unknown_dependency():
    payload = json.dumps(
        [
            {"id": "a", "title": "A", "dependencies": ["ghost"], "description": ""},
        ]
    )
    with pytest.raises(DecompositionError):
        TaskDecomposer(FakeLLM(payload)).decompose("spec")


# --------------------------------------------------------------------- #
# InterviewSession                                                       #
# --------------------------------------------------------------------- #


def test_interview_ends_on_done_and_keeps_decisions():
    llm = FakeLLM(
        "What stack? Recommendation: Use SQLite.",
        "We have enough to generate the backlog.",
    )
    answers = iter(["SQLite it is.", "/done", "y"])

    session = InterviewSession(
        "A todo app",
        llm,
        input_fn=lambda *args, **kwargs: next(answers),
    )
    session.run()

    assert session.finalized is True
    spec = session.specification()
    assert "A todo app" in spec
    assert "SQLite it is." in spec
    assert "## Decision log" in spec


def test_interview_stops_after_wrapup_confirmation():
    llm = FakeLLM(
        "Data? Recommendation: SQLite.",
        "API? Recommendation: REST.",
        "Security via JWT, testing with pytest, plain web UI. Spec is complete.",
    )
    answers = iter(["yes", "yes", "y"])

    session = InterviewSession("A todo app", llm, input_fn=lambda *a, **k: next(answers))
    session.run()

    assert session.finalized is True


def test_interview_saves_progress(tmp_path):
    llm = FakeLLM("Question?")
    session = InterviewSession("Idea", llm, input_fn=lambda *a, **k: "answer")
    path = session.save_progress(tmp_path / "progress.json")
    assert path.exists()
