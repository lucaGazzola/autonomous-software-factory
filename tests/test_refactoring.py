"""RefactoringScanner tests: repo snapshot, LLM review -> OPEN backlog tasks.

A scripted ``FakeLLMClient`` stands in for the LLM so the whole
scan -> validate -> inject pipeline is deterministic.
"""

from __future__ import annotations

import json
from datetime import UTC

import pytest

from factory.adapters.backlog import JSONBacklogAdapter
from factory.core.models import RefactoringConfig, Task, TaskStatus
from factory.core.refactoring import RefactoringScanner

REVIEW_JSON = json.dumps(
    {
        "tasks": [
            {
                "title": "Add tests for the checkout module",
                "description": "Cover the checkout module with unit tests.",
                "acceptance_criteria": ["pytest passes", ">=80% coverage"],
                "files_to_modify": ["tests/test_checkout.py"],
            },
            {
                "title": "Deduplicate HTTP client code",
                "description": "Extract a shared request helper.",
                "acceptance_criteria": ["no duplicated request code"],
                "files_to_modify": ["src/client.py"],
            },
        ]
    }
)


class FakeLLMClient:
    """Scripted stand-in for the LLM backend."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        self.calls.append(messages)
        return self.responses.pop(0)


@pytest.fixture
def repo(tmp_path):
    """A tiny project with a TODO marker, a large file, and no tests."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "src").mkdir()
    (root / "README.md").write_text("# project\n", encoding="utf-8")
    (root / "src" / "main.py").write_text(
        "def main():\n    # TODO: handle errors\n    pass\n", encoding="utf-8"
    )
    (root / "src" / "huge.py").write_text("x" * 600_000, encoding="utf-8")
    return root


@pytest.fixture
def backlog(tmp_path):
    return JSONBacklogAdapter(tmp_path / "backlog.json")


async def make_scanner(repo, backlog, responses, **config_kwargs):
    return RefactoringScanner(
        repo_path=repo,
        backlog=backlog,
        llm=FakeLLMClient(responses),
        config=RefactoringConfig(**config_kwargs),
    )


async def test_scan_proposes_open_tasks(repo, backlog):
    scanner = await make_scanner(repo, backlog, [REVIEW_JSON])
    created = await scanner.scan()

    assert len(created) == 2
    assert [t.id for t in created] == ["REFACTOR-001", "REFACTOR-002"]
    assert all(t.status is TaskStatus.OPEN for t in created)
    assert all(t.metadata["source"] == "refactoring-scanner" for t in created)
    assert await backlog.list_tasks() == created
    # The review prompt carried the snapshot evidence.
    prompt = scanner._build_prompt(await scanner._collect_snapshot())
    assert "todo_markers" in prompt
    assert "no_tests" in prompt


async def test_scan_respects_max_tasks(repo, backlog):
    scanner = await make_scanner(repo, backlog, [REVIEW_JSON], max_tasks_per_scan=1)
    created = await scanner.scan()
    assert len(created) == 1
    assert created[0].id == "REFACTOR-001"


async def test_scan_empty_review_creates_nothing(repo, backlog):
    scanner = await make_scanner(repo, backlog, ['{"tasks": []}'])
    assert await scanner.scan() == []


async def test_scan_invalid_llm_output_degrades_gracefully(repo, backlog):
    """A broken LLM reply must not crash the daemon: empty result instead."""
    scanner = await make_scanner(repo, backlog, ["not json at all"])
    assert await scanner.scan() == []


async def test_ids_are_collision_free(repo, backlog):
    await backlog.create_task(Task(id="REFACTOR-001", title="Already there"))
    scanner = await make_scanner(repo, backlog, [REVIEW_JSON])
    created = await scanner.scan()
    assert created[0].id == "REFACTOR-002"


def test_cooldown_policy():
    from datetime import datetime, timedelta

    scanner = RefactoringScanner(
        repo_path=".",
        backlog=JSONBacklogAdapter("/tmp/never-used.json"),
        config=RefactoringConfig(cooldown_minutes=30),
    )
    now = datetime.now(UTC)
    assert scanner.cooldown_ok(None, now) is True  # never scanned
    assert scanner.cooldown_ok(now - timedelta(minutes=31), now) is True
    assert scanner.cooldown_ok(now - timedelta(minutes=10), now) is False
    # Naive timestamps (as persisted) are handled.
    assert scanner.cooldown_ok(now.replace(tzinfo=None) - timedelta(minutes=10), now) is False


async def test_static_findings_detect_tests_and_todo(repo):
    scanner = await make_scanner(repo, JSONBacklogAdapter("/tmp/never-used.json"), [REVIEW_JSON])
    findings = await scanner._collect_snapshot()
    types = {f["type"] for f in findings["findings"]}
    assert "todo_markers" in types
    assert "large_files" in types
    assert "no_tests" in types
    assert "no_readme" not in types  # README exists
