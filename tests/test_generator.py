"""Tests for the Interactive Backlog Generator.

A scripted ``FakeLLMClient`` stands in for the real backend so the interview
loop, the decomposition engine, and the CLI pipeline are all deterministic:
every path (normal flow, auto wrap-up, early exit, Ctrl+C, retry-on-invalid
output, integration) is exercised without network access.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from factory.adapters.backlog import JSONBacklogAdapter
from factory.core.models import Task
from factory.generator import cli as generator_cli
from factory.generator.decomposer import (
    DECOMPOSITION_SYSTEM_PROMPT,
    DecompositionError,
    TaskDecomposer,
    extract_json,
)
from factory.generator.interview import (
    CRITICAL_TOPICS,
    EXIT_PHRASES,
    GRILLING_SYSTEM_PROMPT,
    InterviewSession,
)
from factory.generator.prompts import GRILLING_SYSTEM_PROMPT as PROMPTS_GRILLING

ARCHITECT_QUESTIONS = [
    (
        "Q1: What stack fits? A) FastAPI + SQLite B) Node + MongoDB. "
        "Recommendation: FastAPI + SQLite for a single-team tool. "
        "Also: what data model do we store, and what API endpoints do we need?"
    ),
    (
        "Q2: How do we authenticate users? Recommendation: JWT bearer tokens. "
        "And how should the UX flow look — upload page, progress, results?"
    ),
    (
        "Q3: What testing strategy? Recommendation: pytest unit + one integration "
        "suite. We have enough to generate the backlog."
    ),
]

VALID_TASKS_JSON = [
    {
        "id": "setup",
        "title": "Project setup",
        "description": "Scaffold the repo: pyproject.toml, package layout, dev deps.",
        "dependencies": [],
        "acceptance_criteria": ["pip install -e . works", "pytest runs"],
        "files_to_modify": ["pyproject.toml"],
    },
    {
        "id": "core-engine",
        "title": "Core conversion engine",
        "description": "Implement the conversion pipeline with the locked stack.",
        "dependencies": ["setup"],
        "acceptance_criteria": ["converts pdf to png", "errors surface as API errors"],
        "files_to_modify": ["src/engine.py"],
    },
    {
        "id": "api",
        "title": "REST API endpoints",
        "description": "Expose upload + status endpoints with JWT auth.",
        "dependencies": ["core-engine"],
        "acceptance_criteria": ["upload returns a job id", "auth rejects bad tokens"],
        "files_to_modify": [],
    },
]


class FakeLLMClient:
    """Scripted stand-in for the LLM backend."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[list[dict], bool]] = []

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        self.calls.append((messages, json_mode))
        if not self.responses:
            raise AssertionError("FakeLLMClient ran out of scripted responses")
        return self.responses.pop(0)


def scripted_input(answers: list[str]):
    """Build an input_fn that replays scripted user answers."""
    queue = list(answers)

    def input_fn(prompt, **kwargs):
        if not queue:
            raise AssertionError("scripted input exhausted")
        return queue.pop(0)

    return input_fn


def make_interview(responses: list[str], answers: list[str]) -> InterviewSession:
    return InterviewSession("File conversion web app", FakeLLMClient(responses), input_fn=scripted_input(answers))


# --------------------------------------------------------------------- #
# Prompts                                                                #
# --------------------------------------------------------------------- #


def test_grilling_prompt_requires_recommendations_and_challenge():
    assert GRILLING_SYSTEM_PROMPT == PROMPTS_GRILLING
    assert "Recommendation:" in GRILLING_SYSTEM_PROMPT
    assert "Challenge ambiguous statements" in GRILLING_SYSTEM_PROMPT
    for topic in ("Data", "API", "Security", "UX", "Testing"):
        assert topic in GRILLING_SYSTEM_PROMPT


def test_decomposition_prompt_requires_json_array_and_topology():
    assert "JSON array" in DECOMPOSITION_SYSTEM_PROMPT
    assert "acceptance_criteria" in DECOMPOSITION_SYSTEM_PROMPT
    assert "files_to_modify" in DECOMPOSITION_SYSTEM_PROMPT
    assert "topological" in DECOMPOSITION_SYSTEM_PROMPT


# --------------------------------------------------------------------- #
# Interview engine                                                       #
# --------------------------------------------------------------------- #


def test_interview_flow_to_auto_wrapup():
    """Covering every critical topic triggers the wrap-up confirmation."""
    session = make_interview(ARCHITECT_QUESTIONS, ["Python", "JWT + simple page", "y"])
    session.run()

    assert session.finalized
    assert session.covered_topics == set(CRITICAL_TOPICS)
    assert len(session.decisions) == 2
    assert session.turns == 3
    spec = session.specification()
    assert "File conversion web app" in spec
    assert "Decision log" in spec and "Python" in spec
    assert session.messages[0]["role"] == "system"


def test_interview_exit_phrase_finalizes_early():
    """'let's build it' ends the session even with partial coverage (confirmed)."""
    session = make_interview(ARCHITECT_QUESTIONS[:2], ["Python", "let's build it", "y"])
    session.run()

    assert session.finalized
    assert not session._all_critical_covered()
    assert session._remaining_topics() == ["Testing"]


def test_interview_help_command_is_not_a_decision():
    """/help is rendered and does not reach the decision log."""
    session = make_interview(
        ARCHITECT_QUESTIONS[:2], ["/help", "Python", "/done", "y"]
    )
    session.run()
    assert session.finalized
    assert len(session.decisions) == 1
    assert all("/help" not in answer for _, answer in session.decisions)


def test_exit_phrases_are_normalized():
    assert "let's build it" in EXIT_PHRASES
    assert InterviewSession._is_exit("Let's build it!")
    assert InterviewSession._is_exit("/done")
    assert not InterviewSession._is_exit("build it tomorrow")


def test_save_progress_roundtrip(tmp_path):
    session = make_interview(ARCHITECT_QUESTIONS[:1], ["Python", "y"])
    session._ask_llm()
    path = session.save_progress(tmp_path / "progress.json")

    payload = json.loads(path.read_text())
    assert payload["idea"] == "File conversion web app"
    assert payload["messages"][-1]["role"] == "assistant"


# --------------------------------------------------------------------- #
# Decomposition engine                                                   #
# --------------------------------------------------------------------- #


def test_extract_json_tolerates_code_fences():
    payload = extract_json('Here you go:\n```json\n[{"id": "a"}]\n```\nEnjoy.')
    assert payload == [{"id": "a"}]


def test_extract_json_finds_embedded_array():
    payload = extract_json('prose [1, 2, 3] trailing')
    assert payload == [1, 2, 3]


def test_decompose_validates_and_renumbers_tasks():
    llm = FakeLLMClient([json.dumps(VALID_TASKS_JSON)])
    tasks = TaskDecomposer(llm).decompose("spec")

    assert [t.id for t in tasks] == ["TASK-001", "TASK-002", "TASK-003"]
    assert tasks[1].dependencies == ["TASK-001"]
    assert tasks[2].dependencies == ["TASK-002"]
    assert tasks[0].dependencies == []
    for task in tasks:
        assert isinstance(task, Task)
        assert task.acceptance_criteria
    assert llm.calls[0][1] is True  # json_mode requested


def test_decompose_retries_after_invalid_output():
    llm = FakeLLMClient(["not json at all", json.dumps(VALID_TASKS_JSON)])
    tasks = TaskDecomposer(llm, max_attempts=3).decompose("spec")
    assert len(tasks) == 3
    assert len(llm.calls) == 2
    assert "rejected" in llm.calls[1][0][-1]["content"].lower()


def test_decompose_raises_after_exhausting_attempts():
    llm = FakeLLMClient(["garbage", "still garbage", "nope"])
    with pytest.raises(DecompositionError, match="after 3 attempt"):
        TaskDecomposer(llm, max_attempts=3).decompose("spec")


def test_decompose_rejects_dangling_dependency():
    bad = json.dumps(
        [dict(VALID_TASKS_JSON[0]), {**VALID_TASKS_JSON[1], "dependencies": ["ghost-task"]}]
    )
    llm = FakeLLMClient([bad, bad, bad])
    with pytest.raises(DecompositionError, match="unknown task"):
        TaskDecomposer(llm, max_attempts=3).decompose("spec")


def test_decompose_rejects_forward_dependency():
    bad = json.dumps([VALID_TASKS_JSON[2], VALID_TASKS_JSON[0], VALID_TASKS_JSON[1]])
    llm = FakeLLMClient([bad, bad, bad])
    with pytest.raises(DecompositionError, match="topological"):
        TaskDecomposer(llm, max_attempts=3).decompose("spec")


def test_decompose_rejects_duplicate_ids():
    bad = json.dumps([VALID_TASKS_JSON[0], {**VALID_TASKS_JSON[1], "id": "setup"}])
    llm = FakeLLMClient([bad, bad, bad])
    with pytest.raises(DecompositionError, match="duplicate task id"):
        TaskDecomposer(llm, max_attempts=3).decompose("spec")


# --------------------------------------------------------------------- #
# CLI integration                                                        #
# --------------------------------------------------------------------- #


def test_generate_backlog_end_to_end(tmp_path, monkeypatch):
    """Full pipeline: interview -> decompose -> JSONBacklogAdapter."""
    llm = FakeLLMClient(ARCHITECT_QUESTIONS + [json.dumps(VALID_TASKS_JSON)])
    session = InterviewSession(
        "File conversion web app", llm, input_fn=scripted_input(["Python", "JWT", "y"])
    )
    monkeypatch.setattr(generator_cli, "LiteLLMClient", lambda model: llm)
    monkeypatch.setattr(generator_cli, "InterviewSession", lambda idea, llm_, console=None: session)
    output = tmp_path / "backlog.json"

    args = SimpleNamespace(prompt="File conversion web app", output=output, model=None, force=False)

    exit_code = generator_cli.cmd_generate_backlog(args)
    assert exit_code == 0

    adapter = JSONBacklogAdapter(output)
    tasks = asyncio.run(adapter.list_tasks())
    assert [t.id for t in tasks] == ["TASK-001", "TASK-002", "TASK-003"]
    assert tasks[1].dependencies == ["TASK-001"]
    assert tasks[1].acceptance_criteria == VALID_TASKS_JSON[1]["acceptance_criteria"]
    assert tasks[2].status.value == "OPEN"
    assert output.exists()


def test_generate_backlog_refuses_existing_backlog(tmp_path, monkeypatch):
    """Without --force an existing backlog is left untouched."""
    output = tmp_path / "backlog.json"
    adapter = JSONBacklogAdapter(output)
    asyncio.run(adapter.create_task(Task(id="TASK-001", title="existing")))

    args = SimpleNamespace(prompt="idea", output=output, model=None, force=False)

    assert generator_cli.cmd_generate_backlog(args) == 2
    assert [t.title for t in asyncio.run(adapter.list_tasks())] == ["existing"]


def test_generate_backlog_force_overwrites(tmp_path, monkeypatch):
    """--force replaces an existing backlog with the generated one."""
    output = tmp_path / "backlog.json"
    adapter = JSONBacklogAdapter(output)
    asyncio.run(adapter.create_task(Task(id="TASK-001", title="stale")))

    llm = FakeLLMClient(ARCHITECT_QUESTIONS + [json.dumps(VALID_TASKS_JSON)])
    session = InterviewSession("idea", llm, input_fn=scripted_input(["Python", "JWT", "y"]))
    monkeypatch.setattr(generator_cli, "LiteLLMClient", lambda model: llm)
    monkeypatch.setattr(generator_cli, "InterviewSession", lambda idea, llm_, console=None: session)

    args = SimpleNamespace(prompt="idea", output=output, model=None, force=True)

    assert generator_cli.cmd_generate_backlog(args) == 0
    refreshed = asyncio.run(JSONBacklogAdapter(output).list_tasks())
    assert [t.title for t in refreshed] == [t["title"] for t in VALID_TASKS_JSON]


def test_generate_backlog_ctrl_c_saves_progress(tmp_path, monkeypatch):
    """KeyboardInterrupt during the interview saves progress and exits 130."""
    output = tmp_path / "backlog.json"

    def interrupt_input(prompt, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(generator_cli, "LiteLLMClient", lambda model: FakeLLMClient(["question?"]))
    monkeypatch.setattr(generator_cli, "DEFAULT_PROGRESS_PATH", tmp_path / "progress.json")

    args = SimpleNamespace(prompt="idea", output=output, model=None, force=False)

    session = InterviewSession("idea", FakeLLMClient(["question?"]), input_fn=interrupt_input)
    monkeypatch.setattr(generator_cli, "InterviewSession", lambda idea, llm, console=None: session)

    assert generator_cli.cmd_generate_backlog(args) == 130
    assert (tmp_path / "progress.json").exists()
    assert not output.exists()
