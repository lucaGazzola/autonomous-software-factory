"""Tests for the central multi-instance web dashboard (``factory web``)."""

from __future__ import annotations

import argparse
import json
import socket
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from factory.central import CentralWebServer, web_task_id_for
from factory.cli import build_parser, cmd_web
from factory.daemon import acquire_run_lock
from factory.instances import add_instance
from factory.models import RunKind, RunOutcome, RunRecord, TaskStatus
from factory.runs import RunRecorder
from tests.conftest import make_task

FINISHED = datetime(2026, 8, 1, 1, 0, 10, tzinfo=UTC)


def _get(url: str) -> tuple[int, dict | list | str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            ctype = resp.headers.get_content_type()
            if ctype == "application/json":
                return resp.status, json.loads(body)
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, body


def _post(url: str, data: str | None) -> tuple[int, dict | list | str]:
    body = data.encode("utf-8") if data is not None else None
    request = urllib.request.Request(url, data=body, method="POST")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            resp_body = resp.read().decode("utf-8")
            ctype = resp.headers.get_content_type()
            if ctype == "application/json":
                return resp.status, json.loads(resp_body)
            return resp.status, resp_body
    except urllib.error.HTTPError as exc:
        resp_body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(resp_body)
        except json.JSONDecodeError:
            return exc.code, resp_body


def task_json(task_id: str, title: str, status: TaskStatus) -> dict:
    return make_task(
        id=task_id,
        title=title,
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    ).model_dump(mode="json")


def run_record(task_id: str, outcome: RunOutcome) -> RunRecord:
    return RunRecord(
        started_at=datetime(2026, 8, 1, 1, 0, tzinfo=UTC),
        finished_at=FINISHED,
        kind=RunKind.TASK,
        task_id=task_id,
        task_title="Do the thing",
        outcome=outcome,
        agent_exit_code=0,
        duration_seconds=5.0,
    )


def write_instance(
    tmp_path: Path,
    name: str,
    *,
    repo: str,
    tasks: list[dict] | None = None,
    log_lines: list[str] | None = None,
    runs: list[RunRecord] | None = None,
    blocker: str | None = None,
) -> tuple[Path, Path]:
    """Create a registered instance in ``tmp_path/<name>``; returns the dir
    and config path. Only the files passed are written, so omitting them
    yields an instance with missing data files."""
    config_dir = tmp_path / name
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "factory.yaml"
    backlog = config_dir / "backlog.json"
    config_path.write_text(
        f"name: {name}\n"
        f"repo: {repo}\n"
        f"backlog: {backlog}\n"
        f"blocker_file: {config_dir / 'BLOCKER.md'}\n"
        f"agent_command: echo hi\n"
        f"log_file: {config_dir / 'factory.log'}\n"
        f"interval_minutes: 30\n",
        encoding="utf-8",
    )
    if tasks is not None:
        backlog.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    if log_lines:
        (config_dir / "factory.log").write_text(
            "\n".join(log_lines) + "\n", encoding="utf-8"
        )
    if runs:
        recorder = RunRecorder(config_dir / "runs.jsonl")
        for record in runs:
            recorder.append(record)
    if blocker is not None:
        (config_dir / "BLOCKER.md").write_text(blocker, encoding="utf-8")
    add_instance(name, config_path)
    return config_dir, config_path


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGEO_REGISTRY", str(tmp_path / "instances.yaml"))
    return tmp_path


@pytest.fixture
def central_server():
    server = CentralWebServer(host="127.0.0.1", port=0)
    assert server.start() is True
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def web_env(registry, central_server):
    """Two registered instances: ``alpha`` (with data files) and ``beta``."""
    write_instance(
        registry,
        "alpha",
        repo=str(registry / "repos" / "alpha"),
        tasks=[
            task_json("TASK-001", "First", TaskStatus.OPEN),
            task_json("TASK-002", "Done", TaskStatus.COMPLETED),
        ],
        log_lines=[
            "2026-08-01 01:00:00 INFO     factory.daemon: Run finished: task",
            "trailing line",
        ],
        runs=[run_record("TASK-001", RunOutcome.SUCCESS)],
        blocker="# Blocker\nPlease decide.\n",
    )
    write_instance(
        registry,
        "beta",
        repo=str(registry / "repos" / "beta"),
        tasks=[task_json("B-1", "Beta task", TaskStatus.OPEN)],
    )
    return central_server, registry


def test_home_page_served(web_env):
    server, _ = web_env
    status, body = _get(f"http://127.0.0.1:{server.port}/")
    assert status == 200
    assert isinstance(body, str)
    assert "<!doctype html" in body.lower()
    assert 'href="/style.css"' in body
    assert 'src="/central/central.js"' in body


def test_home_page_lists_registered_instances_via_api(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances")
    assert status == 200
    assert isinstance(data, list)
    names = [entry["name"] for entry in data]
    assert names == ["alpha", "beta"]
    alpha = data[0]
    assert alpha["repo"].endswith("repos/alpha")
    assert alpha["daemon_running"] is False
    assert alpha["last_outcome"] == "SUCCESS"
    assert alpha["backlog_counts"] == {
        "OPEN": 1,
        "BLOCKED": 0,
        "COMPLETED": 1,
        "FAILED": 0,
    }


def test_instance_page_served(web_env):
    server, _ = web_env
    status, body = _get(f"http://127.0.0.1:{server.port}/instances/alpha/")
    assert status == 200
    assert isinstance(body, str)
    assert "Backlog" in body
    assert 'data-page="instance"' in body


def test_instance_page_has_new_task_form(web_env):
    server, _ = web_env
    status, body = _get(f"http://127.0.0.1:{server.port}/instances/alpha/")
    assert status == 200
    assert 'data-tab="create"' in body
    assert 'id="new-task"' in body
    assert 'id="task-title"' in body
    backlog_panel = body.split('<main id="tab-backlog"')[1].split("</main>")[0]
    assert "new-task" not in backlog_panel
    create_panel = body.split('<main id="tab-create"')[1].split("</main>")[0]
    assert 'id="new-task"' in create_panel


def test_unknown_instance_returns_404(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/instances/nope/")
    assert status == 404
    assert data["error"] == "unknown instance"

    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/nope/tasks")
    assert status == 404
    assert data["error"] == "unknown instance"


def test_status_reads_files_without_daemon(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/status")
    assert status == 200
    assert data["name"] == "alpha"
    assert data["daemon_running"] is False
    assert data["pid"] is None
    assert data["interval_minutes"] == 30
    assert data["last_outcome"] == "SUCCESS"
    assert data["next_run_at"] is None


def test_status_reports_running_daemon_and_next_run(web_env):
    server, registry = web_env
    lock = acquire_run_lock(registry / "alpha" / "backlog.lock")
    assert lock is not None
    try:
        status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/status")
        assert status == 200
        assert data["daemon_running"] is True
        assert data["pid"] is not None
        assert data["next_run_at"] == "2026-08-01T01:30:10+00:00"
    finally:
        lock.close()


def test_status_prefers_daemon_state_file(web_env):
    """``next_run_at``/``last_outcome``/``pid`` come from daemon.state.json
    when present, even without a lock held."""
    server, registry = web_env
    state_path = registry / "alpha" / "backlog.state.json"
    state_path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "started_at": "2026-08-01T01:00:00+00:00",
                "last_outcome": "task",
                "next_run_at": "2026-08-01T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/status")
    assert status == 200
    assert data["pid"] == 4242
    assert data["last_outcome"] == "task"
    assert data["next_run_at"] is None  # daemon not running: no schedule
    assert data["daemon_running"] is False


def test_status_daemon_state_file_next_run(web_env):
    server, registry = web_env
    state_path = registry / "alpha" / "backlog.state.json"
    state_path.write_text(
        json.dumps(
            {
                "pid": 4242,
                "started_at": "2026-08-01T01:00:00+00:00",
                "last_outcome": "task",
                "next_run_at": "2026-08-01T12:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    lock = acquire_run_lock(registry / "alpha" / "backlog.lock")
    assert lock is not None
    try:
        status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/status")
        assert status == 200
        assert data["daemon_running"] is True
        assert data["pid"] == 4242
        assert data["next_run_at"] == "2026-08-01T12:00:00+00:00"
        assert data["last_outcome"] == "task"
    finally:
        lock.close()


def test_tasks_endpoints(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks")
    assert status == 200
    assert [task["id"] for task in data] == ["TASK-001", "TASK-002"]

    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks/TASK-002")
    assert status == 200
    assert data["title"] == "Done"

    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks/MISSING")
    assert status == 404
    assert data["error"] == "not found"


def test_post_task_creates_in_backlog(web_env):
    server, _ = web_env
    base = f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks"
    status, data = _post(base, json.dumps({"title": "Build a thing"}))
    assert status == 201
    assert isinstance(data, dict)
    assert data["id"] == "WEB-001"
    assert data["title"] == "Build a thing"
    assert data["description"] == ""
    assert data["acceptance_criteria"] == []
    assert data["status"] == "OPEN"

    status, tasks = _get(base)
    assert status == 200
    assert [t["id"] for t in tasks] == ["TASK-001", "TASK-002", "WEB-001"]

    status, tasks = _get(f"http://127.0.0.1:{server.port}/api/instances/beta/tasks")
    assert status == 200
    assert [t["id"] for t in tasks] == ["B-1"]


def test_post_task_increments_web_ids(web_env):
    server, _ = web_env
    base = f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks"
    _, first = _post(base, json.dumps({"title": "One"}))
    _, second = _post(base, json.dumps({"title": "Two"}))
    assert first["id"] == "WEB-001"
    assert second["id"] == "WEB-002"


def test_post_task_includes_optional_fields(web_env):
    server, _ = web_env
    base = f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks"
    status, data = _post(
        base,
        json.dumps(
            {
                "title": "  Refactor the cache  ",
                "description": "Make it faster.",
                "acceptance_criteria": ["no regressions", "tests pass"],
            }
        ),
    )
    assert status == 201
    assert data["title"] == "Refactor the cache"
    assert data["description"] == "Make it faster."
    assert data["acceptance_criteria"] == ["no regressions", "tests pass"]
    assert data["created_at"]
    assert data["updated_at"]


def test_post_task_validation_errors(web_env):
    server, _ = web_env
    base = f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks"

    for payload in ({}, {"title": "   "}, {"title": 42}):
        status, data = _post(base, json.dumps(payload))
        assert status == 400
        assert data["error"]

    status, data = _post(base, "{not json")
    assert status == 400
    assert data["error"]

    status, data = _post(base, "[1, 2]")
    assert status == 400
    assert data["error"]

    status, data = _post(base, None)
    assert status == 400
    assert data["error"]

    status, data = _post(base, json.dumps({"title": "x", "description": 1}))
    assert status == 400
    assert data["error"]

    status, data = _post(
        base, json.dumps({"title": "x", "acceptance_criteria": "nope"})
    )
    assert status == 400
    assert data["error"]


def test_post_task_unknown_instance_404(web_env):
    server, _ = web_env
    status, data = _post(
        f"http://127.0.0.1:{server.port}/api/instances/nope/tasks",
        json.dumps({"title": "x"}),
    )
    assert status == 404
    assert data["error"] == "unknown instance"


def test_post_task_wrong_path_404(web_env):
    server, _ = web_env
    status, data = _post(
        f"http://127.0.0.1:{server.port}/api/instances/alpha/bogus",
        json.dumps({"title": "x"}),
    )
    assert status == 404
    assert data["error"] == "not found"


def test_post_task_id_collision_409(web_env, monkeypatch):
    import factory.central as central_module

    server, _ = web_env
    monkeypatch.setattr(central_module, "web_task_id_for", lambda tasks: "TASK-001")
    status, data = _post(
        f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks",
        json.dumps({"title": "Duplicate"}),
    )
    assert status == 409
    assert data["error"]


def test_post_task_does_not_leak_failed_task(web_env, monkeypatch):
    import factory.central as central_module

    server, _ = web_env
    monkeypatch.setattr(central_module, "web_task_id_for", lambda tasks: "TASK-001")
    _post(
        f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks",
        json.dumps({"title": "Duplicate"}),
    )
    status, tasks = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/tasks")
    assert status == 200
    assert [t["id"] for t in tasks] == ["TASK-001", "TASK-002"]


def test_web_task_id_for():
    tasks = [make_task(id="TASK-001", title="a"), make_task(id="WEB-007", title="b")]
    assert web_task_id_for(tasks) == "WEB-008"
    assert web_task_id_for([]) == "WEB-001"


def test_logs_endpoint(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/logs?lines=1")
    assert status == 200
    assert data["lines"] == ["trailing line"]


def test_runs_endpoint(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/runs")
    assert status == 200
    assert [run["task_id"] for run in data] == ["TASK-001"]
    assert data[0]["outcome"] == "SUCCESS"


def test_blocker_endpoint(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/blocker")
    assert status == 200
    assert data["content"] == "# Blocker\nPlease decide.\n"


def test_blocker_null_when_missing(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/beta/blocker")
    assert status == 200
    assert data["content"] is None


def test_config_endpoint(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/config")
    assert status == 200
    assert data["name"] == "alpha"
    assert data["interval_minutes"] == 30


def test_missing_data_files_render_empty(registry, central_server):
    write_instance(
        registry,
        "ghost",
        repo=str(registry / "repos" / "ghost"),
        tasks=None,
        log_lines=None,
        runs=None,
        blocker=None,
    )
    base = f"http://127.0.0.1:{central_server.port}/api/instances/ghost"

    status, data = _get(f"{base}/status")
    assert status == 200
    assert data["daemon_running"] is False
    assert data["last_outcome"] is None
    assert data["next_run_at"] is None

    status, data = _get(f"{base}/tasks")
    assert status == 200
    assert data == []

    status, data = _get(f"{base}/logs")
    assert status == 200
    assert data["lines"] == []

    status, data = _get(f"{base}/runs")
    assert status == 200
    assert data == []

    status, data = _get(f"{base}/blocker")
    assert status == 200
    assert data["content"] is None

    status, _body = _get(f"http://127.0.0.1:{central_server.port}/instances/ghost/")
    assert status == 200


def test_unknown_api_endpoint_returns_404(web_env):
    server, _ = web_env
    status, data = _get(f"http://127.0.0.1:{server.port}/api/instances/alpha/bogus")
    assert status == 404
    assert data["error"] == "not found"


def test_static_assets_served(web_env):
    server, _ = web_env
    status, body = _get(f"http://127.0.0.1:{server.port}/style.css")
    assert status == 200
    assert isinstance(body, str)

    status, body = _get(f"http://127.0.0.1:{server.port}/central/central.js")
    assert status == 200
    assert "REFRESH_MS" in body

    status, body = _get(f"http://127.0.0.1:{server.port}/central/central.css")
    assert status == 200


def test_parser_help_lists_web(capsys):
    build_parser().print_help()
    assert "web" in capsys.readouterr().out


def test_web_bind_failure_exits_nonzero():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        assert cmd_web(argparse.Namespace(host="127.0.0.1", port=port)) == 1
    finally:
        sock.close()
