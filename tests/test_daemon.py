"""FactoryDaemon cycle tests: blocked pause, backlog drain, refactoring mode,
state persistence, and the run lock."""

from __future__ import annotations

import asyncio

from factory.adapters.agents import MockAgentAdapter
from factory.adapters.backlog import JSONBacklogAdapter
from factory.adapters.feedback import DeferredFeedbackProvider
from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.core.daemon import DaemonState, FactoryDaemon, acquire_run_lock
from factory.core.models import ProjectConfig, Task, TaskStatus, UserResponse
from factory.core.orchestrator import Orchestrator
from factory.core.refactoring import RefactoringScanner


class ScriptedScanner(RefactoringScanner):
    """Stands in for the LLM-backed scanner with deterministic results."""

    def __init__(
        self, tasks: list[Task] | None = None, raise_on_scan: Exception | None = None
    ) -> None:
        self.tasks = tasks or []
        self.raise_on_scan = raise_on_scan
        self.scan_count = 0

    def bind_backlog(self, backlog) -> ScriptedScanner:
        """Attach the backlog so scripted tasks are injected like the real scanner."""
        self.backlog = backlog
        return self

    async def scan(self):
        self.scan_count += 1
        if self.raise_on_scan is not None:
            raise self.raise_on_scan
        created: list[Task] = []
        for task in self.tasks:
            created.append(await self.backlog.create_task(task))
        return created

    def cooldown_ok(self, last_scan_at, now=None):
        return True


class RecordingFeedback(BaseFeedbackProvider):
    """Records notifications and never answers HITL prompts."""

    name = "recording"

    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []

    async def request_input(self, task_id: str, prompt: str) -> UserResponse:
        return UserResponse(task_id=task_id, action="abort")

    async def notify(self, task_id: str, message: str) -> None:
        self.notifications.append((task_id, message))


def make_project(tmp_path, **overrides) -> ProjectConfig:
    defaults = {
        "project_name": "test-project",
        "repo_path": tmp_path,
        "backlog_source": "backlog.json",
        "schedule_interval_minutes": 60,
        "poll_interval_seconds": 0.1,
    }
    return ProjectConfig(**{**defaults, **overrides})


async def make_daemon(tmp_path, project: ProjectConfig | None = None, scanner=None, feedback=None):
    project = project or make_project(tmp_path)
    backlog = JSONBacklogAdapter(project.backlog_path)
    feedback = feedback or RecordingFeedback()
    agent = MockAgentAdapter(delay_seconds=0.0)
    orchestrator = Orchestrator(config=project, backlog=backlog, agent=agent, feedback=feedback)
    if scanner is not None:
        scanner.bind_backlog(backlog)
    daemon = FactoryDaemon(
        config=project,
        backlog=backlog,
        agent=agent,
        feedback=feedback,
        orchestrator=orchestrator,
        scanner=scanner,
        state=DaemonState(tmp_path / "daemon_state.json"),
    )
    return daemon, backlog


async def test_cycle_with_open_tasks_drains(tmp_path):
    daemon, backlog = await make_daemon(tmp_path, scanner=ScriptedScanner())
    await backlog.create_task(Task(id="T-1", title="One", metadata={"simulate": "success"}))
    await backlog.create_task(Task(id="T-2", title="Two", metadata={"simulate": "success"}))
    assert await daemon.run_cycle() == "processed:2"
    assert await backlog.list_tasks()  # all tasks done
    assert all(t.status is TaskStatus.COMPLETED for t in await backlog.list_tasks())


async def test_cycle_empty_backlog_runs_refactoring_scan(tmp_path):
    scanner = ScriptedScanner(tasks=[Task(id="REFACTOR-001", title="Improve it")])
    daemon, backlog = await make_daemon(tmp_path, scanner=scanner)
    assert await daemon.run_cycle() == "scanned:1"
    assert scanner.scan_count == 1
    stored = await backlog.get_task("REFACTOR-001")
    assert stored is not None and stored.status is TaskStatus.OPEN


async def test_cycle_empty_backlog_scan_failure_is_idle(tmp_path):
    scanner = ScriptedScanner(raise_on_scan=RuntimeError("llm down"))
    daemon, _backlog = await make_daemon(tmp_path, scanner=scanner)
    assert await daemon.run_cycle() == "scanned:0"
    assert scanner.scan_count == 1


async def test_cycle_no_scanner_is_idle(tmp_path):
    from factory.core.models import RefactoringConfig

    project = make_project(tmp_path, refactoring=RefactoringConfig(enabled=False))
    daemon, _backlog = await make_daemon(tmp_path, project=project, scanner=None)
    assert daemon.scanner is None
    assert await daemon.run_cycle() == "idle"


async def test_cycle_pauses_on_blocked_task(tmp_path):
    feedback = RecordingFeedback()
    daemon, backlog = await make_daemon(tmp_path, scanner=ScriptedScanner(), feedback=feedback)
    await backlog.create_task(Task(id="T-1", title="Stuck", status=TaskStatus.BLOCKED))
    assert await daemon.run_cycle() == "blocked"
    # Alert sent, orchestrator untouched, no refactoring scan while paused.
    assert any("BLOCKED" in message for _, message in feedback.notifications)
    assert await backlog.get_task("T-1") is not None
    assert (await backlog.get_task("T-1")).status is TaskStatus.BLOCKED


async def test_blocked_alert_only_once(tmp_path):
    feedback = RecordingFeedback()
    daemon, backlog = await make_daemon(tmp_path, scanner=ScriptedScanner(), feedback=feedback)
    await backlog.create_task(Task(id="T-1", title="Stuck", status=TaskStatus.BLOCKED))
    assert await daemon.run_cycle() == "blocked"
    assert await daemon.run_cycle() == "blocked"
    assert len(feedback.notifications) == 1


async def test_blocked_then_resolved_runs(tmp_path):
    feedback = RecordingFeedback()
    scanner = ScriptedScanner()
    daemon, backlog = await make_daemon(tmp_path, scanner=scanner, feedback=feedback)
    await backlog.create_task(Task(id="T-1", title="Unstuck", status=TaskStatus.BLOCKED))
    assert await daemon.run_cycle() == "blocked"
    await backlog.update_task_status("T-1", TaskStatus.OPEN)
    assert await daemon.run_cycle() == "processed:1"


async def test_run_forever_stops_cleanly(tmp_path):
    scanner = ScriptedScanner()
    daemon, backlog = await make_daemon(
        tmp_path,
        project=make_project(tmp_path, schedule_interval_minutes=60, poll_interval_seconds=0.1),
        scanner=scanner,
    )
    await backlog.create_task(Task(id="T-1", title="One", metadata={"simulate": "success"}))
    task = asyncio.create_task(daemon.run_forever())
    await asyncio.sleep(0.3)
    daemon.stop()
    await asyncio.wait_for(task, timeout=5)
    assert (await backlog.get_task("T-1")).status is TaskStatus.COMPLETED


async def test_run_forever_scans_when_idle(tmp_path):
    scanner = ScriptedScanner(tasks=[Task(id="REFACTOR-001", title="Polish")])
    daemon, backlog = await make_daemon(
        tmp_path,
        project=make_project(tmp_path, schedule_interval_minutes=60, poll_interval_seconds=0.1),
        scanner=scanner,
    )
    task = asyncio.create_task(daemon.run_forever())
    await asyncio.sleep(0.35)
    daemon.stop()
    await asyncio.wait_for(task, timeout=5)
    assert scanner.scan_count >= 1
    assert (await backlog.get_task("REFACTOR-001")) is not None


def test_daemon_state_roundtrip(tmp_path):
    state = DaemonState(tmp_path / "state.json")
    assert state.load()["last_cycle_at"] is None
    state.save({"last_cycle_at": "2026-01-01T00:00:00+00:00", "last_cycle_outcome": "processed:1"})
    loaded = state.load()
    assert loaded["last_cycle_outcome"] == "processed:1"
    # Unknown keys are dropped on load.
    state.save({"bogus": 1, "last_cycle_outcome": "idle"})
    assert state.load()["last_cycle_outcome"] == "idle"
    assert "bogus" not in state.load()


def test_daemon_state_tolerates_corruption(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert DaemonState(path).load()["last_cycle_at"] is None


def test_run_lock_serializes(tmp_path):
    lock_path = tmp_path / "daemon.lock"
    first = acquire_run_lock(lock_path)
    assert first is not None
    second = acquire_run_lock(lock_path)
    assert second is None  # held by "another daemon"
    first.close()
    third = acquire_run_lock(lock_path)
    assert third is not None
    third.close()


async def test_deferred_feedback_waits_until_resolved(tmp_path):
    backlog = JSONBacklogAdapter(tmp_path / "backlog.json")
    await backlog.create_task(Task(id="T-1", title="Stuck", status=TaskStatus.BLOCKED))
    provider = DeferredFeedbackProvider(backlog=backlog, poll_interval=0.05)
    response = asyncio.create_task(provider.request_input("T-1", "please help"))
    await asyncio.sleep(0.15)
    assert not response.done()  # still waiting
    await backlog.update_task_status("T-1", TaskStatus.OPEN)
    result = await asyncio.wait_for(response, timeout=5)
    assert result.action.value == "retry"


async def test_deferred_feedback_notifies_on_block(tmp_path):
    backlog = JSONBacklogAdapter(tmp_path / "backlog.json")
    await backlog.create_task(Task(id="T-1", title="Stuck", status=TaskStatus.BLOCKED))
    alerts: list[tuple[str, str]] = []

    async def alert(task_id: str, prompt: str) -> None:
        alerts.append((task_id, prompt))

    provider = DeferredFeedbackProvider(backlog=backlog, poll_interval=0.05, on_blocked=alert)
    response = asyncio.create_task(provider.request_input("T-1", "blocked!"))
    await asyncio.sleep(0.1)
    assert alerts == [("T-1", "blocked!")]
    await backlog.update_task_status("T-1", TaskStatus.OPEN)
    await asyncio.wait_for(response, timeout=5)
