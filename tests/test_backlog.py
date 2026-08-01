"""Backlog tests: task lifecycle, oldest-OPEN ordering, corruption tolerance."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from factory.backlog import JSONBacklog
from factory.models import Task, TaskStatus
from tests.conftest import make_task


async def test_fetch_oldest_open_task(tmp_path):
    backlog = JSONBacklog(tmp_path / "backlog.json")
    older = Task(id="A", title="older", created_at=datetime.now(UTC) - timedelta(hours=2))
    newer = Task(id="B", title="newer", created_at=datetime.now(UTC))
    await backlog.create_task(newer)
    await backlog.create_task(older)

    fetched = await backlog.fetch_next_task()
    assert fetched.id == "A"


async def test_fetch_skips_non_open_tasks(tmp_path):
    backlog = JSONBacklog(tmp_path / "backlog.json")
    for status in (TaskStatus.BLOCKED, TaskStatus.COMPLETED, TaskStatus.FAILED):
        await backlog.create_task(Task(id=status.value, title="t", status=status))
    assert await backlog.fetch_next_task() is None


async def test_fetch_prefers_open_over_blocked(tmp_path):
    backlog = JSONBacklog(tmp_path / "backlog.json")
    await backlog.create_task(Task(id="BLOCKED-1", title="b", status=TaskStatus.BLOCKED))
    await backlog.create_task(make_task(id="OPEN-1"))
    fetched = await backlog.fetch_next_task()
    assert fetched.id == "OPEN-1"


async def test_update_status_persists_and_bumps_timestamp(tmp_path):
    backlog = JSONBacklog(tmp_path / "backlog.json")
    task = await backlog.create_task(make_task())
    updated = await backlog.update_status(task.id, TaskStatus.COMPLETED)
    assert updated.status is TaskStatus.COMPLETED
    stored = await backlog.get_task(task.id)
    assert stored.status is TaskStatus.COMPLETED
    assert stored.updated_at >= task.updated_at


async def test_create_duplicate_id_raises(tmp_path):
    backlog = JSONBacklog(tmp_path / "backlog.json")
    await backlog.create_task(make_task())
    with pytest.raises(ValueError):
        await backlog.create_task(make_task())


async def test_missing_file_yields_empty_backlog(tmp_path):
    backlog = JSONBacklog(tmp_path / "nope.json")
    assert await backlog.list_tasks() == []
    assert await backlog.fetch_next_task() is None


async def test_corrupt_file_is_preserved_and_yields_empty_backlog(tmp_path, caplog):
    path = tmp_path / "backlog.json"
    path.write_text("{not valid json", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="factory.backlog"):
        assert await JSONBacklog(path).list_tasks() == []
    assert not path.exists()
    corrupt = list(tmp_path.glob("backlog.json.corrupt-*"))
    assert len(corrupt) == 1
    assert corrupt[0].read_text(encoding="utf-8") == "{not valid json"
    assert "corrupt" in caplog.text.lower()


async def test_invalid_task_row_does_not_kill_the_store(tmp_path):
    path = tmp_path / "backlog.json"
    path.write_text(json.dumps({"tasks": [{"id": "BAD"}], "junk": [1]}), encoding="utf-8")
    tasks = await JSONBacklog(path).list_tasks()
    assert len(tasks) == 1
    assert tasks[0].id == "BAD"
    assert tasks[0].status is TaskStatus.FAILED
