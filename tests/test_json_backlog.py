"""Tests for the JSON-file backlog adapter."""

import asyncio

import pytest

from factory.adapters.backlog import JSONBacklogAdapter
from factory.core.models import Task, TaskStatus


@pytest.fixture
def backlog_path(tmp_path):
    return tmp_path / "backlog.json"


async def test_create_and_reload(backlog_path):
    adapter = JSONBacklogAdapter(backlog_path)
    await adapter.create_task(Task(id="T-1", title="First"))

    fresh = JSONBacklogAdapter(backlog_path)
    tasks = await fresh.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].id == "T-1"
    assert tasks[0].status is TaskStatus.OPEN


async def test_duplicate_id_rejected(backlog_path):
    adapter = JSONBacklogAdapter(backlog_path)
    await adapter.create_task(Task(id="T-1", title="First"))
    with pytest.raises(ValueError):
        await adapter.create_task(Task(id="T-1", title="Second"))


async def test_fetch_next_task_returns_only_open(backlog_path):
    adapter = JSONBacklogAdapter(backlog_path)
    await adapter.create_task(Task(id="T-1", title="Old open"))
    await adapter.create_task(
        Task(id="T-2", title="In progress", status=TaskStatus.IN_PROGRESS)
    )
    await adapter.create_task(Task(id="T-3", title="Newer open"))
    task = await adapter.fetch_next_task()
    assert task is not None
    assert task.id == "T-1"  # oldest OPEN task wins


async def test_fetch_next_task_empty(backlog_path):
    adapter = JSONBacklogAdapter(backlog_path)
    assert await adapter.fetch_next_task() is None


async def test_status_transition_persists(backlog_path):
    adapter = JSONBacklogAdapter(backlog_path)
    await adapter.create_task(Task(id="T-1", title="First"))
    updated = await adapter.update_task_status("T-1", TaskStatus.IN_PROGRESS)
    assert updated is not None
    assert updated.status is TaskStatus.IN_PROGRESS
    assert updated.updated_at >= updated.created_at

    reloaded = JSONBacklogAdapter(backlog_path)
    assert (await reloaded.get_task("T-1")).status is TaskStatus.IN_PROGRESS


async def test_comments_and_artifacts_roundtrip(backlog_path):
    adapter = JSONBacklogAdapter(backlog_path)
    await adapter.create_task(Task(id="T-1", title="First"))
    await adapter.add_comment("T-1", "started")
    await adapter.add_comment("T-1", "finished")
    await adapter.attach_artifact("T-1", "artifacts/T-1.patch")

    fresh = JSONBacklogAdapter(backlog_path)
    assert await fresh.list_comments("T-1") == ["started", "finished"]
    assert await fresh.list_artifacts("T-1") == ["artifacts/T-1.patch"]


async def test_concurrent_mutations_are_safe(backlog_path):
    adapter = JSONBacklogAdapter(backlog_path)
    await adapter.create_task(Task(id="T-1", title="First"))

    async def commenter(index: int) -> None:
        for i in range(10):
            await adapter.add_comment("T-1", f"c{index}-{i}")

    await asyncio.gather(*[commenter(i) for i in range(5)])
    comments = await adapter.list_comments("T-1")
    assert len(comments) == 50
    assert len(set(comments)) == 50  # nothing lost, nothing duplicated


async def test_corrupt_file_tolerated(backlog_path):
    backlog_path.write_text("{not valid json", encoding="utf-8")
    adapter = JSONBacklogAdapter(backlog_path)
    assert await adapter.list_tasks() == []
    task = await adapter.create_task(Task(id="T-1", title="Recovered"))
    assert (await adapter.get_task("T-1")).id == task.id
