"""HTTP API tests: handlers backed by temp config, backlog, and log."""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from factory.backlog import JSONBacklog
from factory.daemon import FactoryDaemon
from factory.models import RunKind, RunOutcome, RunRecord, TaskStatus
from factory.runs import RunRecorder, runs_path_for
from factory.server import WEB_ROOT, WebServer
from tests.conftest import make_config, make_task


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


@pytest.fixture
def api_env(git_repo, tmp_path):
    log_file = tmp_path / "factory.log"
    log_file.write_text(
        "2026-08-01 01:00:00 INFO     factory.daemon: Run finished: dirty\n"
        "2026-08-01 02:00:00 INFO     factory.daemon: Run finished: task\n"
        "trailing line\n",
        encoding="utf-8",
    )
    config = make_config(
        git_repo,
        tmp_path,
        log_file=str(log_file),
        web_port=0,
        interval_minutes=15,
    )
    backlog = JSONBacklog(config.backlog)
    return config, backlog, log_file


async def _seed_backlog(backlog: JSONBacklog) -> None:
    await backlog.create_task(
        make_task(
            id="TASK-001",
            title="First",
            status=TaskStatus.OPEN,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    await backlog.create_task(
        make_task(
            id="TASK-002",
            title="Done",
            status=TaskStatus.COMPLETED,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )


class _QuietFactory:
    async def run_cycle(self) -> str:
        return "task"


@pytest.fixture
async def running_server(api_env):
    import socket

    config, backlog, _log = api_env
    await _seed_backlog(backlog)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()
    config = config.model_copy(update={"web_port": free_port})

    daemon = FactoryDaemon(config, _QuietFactory())  # type: ignore[arg-type]
    daemon.last_outcome = "task"
    daemon.next_run_at = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    daemon.pid = 4242

    server = WebServer(config, backlog, daemon)
    assert server.start(loop=None) is True
    try:
        yield server, config, backlog
    finally:
        server.stop()


def test_web_port_default():
    from factory.models import FactoryConfig

    assert FactoryConfig(agent_command="echo").web_port == 8787


def test_web_port_zero_disables(api_env, caplog):
    config, backlog, _ = api_env
    config = config.model_copy(update={"web_port": 0})
    server = WebServer(config, backlog)
    with caplog.at_level(logging.INFO, logger="factory.server"):
        assert server.start() is False
    assert "disabled" in caplog.text.lower()
    assert server.port is None


def test_busy_port_logs_error_and_returns_false(api_env, caplog):
    config, backlog, _ = api_env
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    first = WebServer(config.model_copy(update={"web_port": port}), backlog)
    assert first.start() is True
    try:
        second = WebServer(config.model_copy(update={"web_port": port}), backlog)
        with caplog.at_level(logging.ERROR, logger="factory.server"):
            assert second.start() is False
        assert "failed to bind" in caplog.text.lower()
    finally:
        first.stop()


def test_custom_bind_host(api_env):
    config, backlog, _ = api_env
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = WebServer(
        config.model_copy(update={"web_host": "0.0.0.0", "web_port": port}),
        backlog,
    )
    assert server.start() is True
    try:
        status, data = _get(f"http://127.0.0.1:{server.port}/api/status")
        assert status == 200
        assert isinstance(data, dict)
    finally:
        server.stop()


def test_api_tasks_list(running_server):
    server, _config, _backlog = running_server
    status, data = _get(f"http://127.0.0.1:{server.port}/api/tasks")
    assert status == 200
    assert isinstance(data, list)
    assert [t["id"] for t in data] == ["TASK-001", "TASK-002"]
    assert data[0]["title"] == "First"
    assert data[1]["status"] == "COMPLETED"


def test_api_tasks_by_id(running_server):
    server, _config, _backlog = running_server
    status, data = _get(f"http://127.0.0.1:{server.port}/api/tasks/TASK-001")
    assert status == 200
    assert data["id"] == "TASK-001"
    assert data["title"] == "First"

    status, data = _get(f"http://127.0.0.1:{server.port}/api/tasks/MISSING")
    assert status == 404


def test_api_status(running_server):
    server, config, _backlog = running_server
    status, data = _get(f"http://127.0.0.1:{server.port}/api/status")
    assert status == 200
    assert data["pid"] == 4242
    assert data["interval_minutes"] == config.interval_minutes
    assert data["last_outcome"] == "task"
    assert data["next_run_at"] == "2026-08-01T12:00:00+00:00"
    assert data["name"] == config.name


def test_api_config(running_server):
    server, config, _backlog = running_server
    status, data = _get(f"http://127.0.0.1:{server.port}/api/config")
    assert status == 200
    assert data["name"] == config.name
    assert data["web_port"] == config.web_port
    assert data["interval_minutes"] == config.interval_minutes
    assert str(config.repo) in data["repo"] or data["repo"] == str(config.repo)


def test_api_logs(running_server):
    server, _config, _backlog = running_server
    status, data = _get(f"http://127.0.0.1:{server.port}/api/logs?lines=2")
    assert status == 200
    assert data["lines"] == [
        "2026-08-01 02:00:00 INFO     factory.daemon: Run finished: task",
        "trailing line",
    ]

    status, data = _get(f"http://127.0.0.1:{server.port}/api/logs")
    assert status == 200
    assert len(data["lines"]) == 3


def test_api_blocker_null_and_content(running_server, tmp_path):
    server, config, _backlog = running_server
    status, data = _get(f"http://127.0.0.1:{server.port}/api/blocker")
    assert status == 200
    assert data["content"] is None

    Path(config.blocker_file).write_text("# Blocked\nDo the thing.\n", encoding="utf-8")
    status, data = _get(f"http://127.0.0.1:{server.port}/api/blocker")
    assert status == 200
    assert data["content"] == "# Blocked\nDo the thing.\n"


def test_api_runs_empty_when_missing(running_server):
    server, config, _backlog = running_server
    assert not runs_path_for(config.backlog).exists()

    status, data = _get(f"http://127.0.0.1:{server.port}/api/runs")
    assert status == 200
    assert data == []


def test_api_runs_newest_first_and_limited(running_server):
    server, config, _backlog = running_server
    recorder = RunRecorder(runs_path_for(config.backlog))
    recorder.append(
        RunRecord(
            started_at=datetime(2026, 8, 1, tzinfo=UTC),
            finished_at=datetime(2026, 8, 1, 0, 0, 10, tzinfo=UTC),
            kind=RunKind.TASK,
            task_id="OLD",
            task_title="Older",
            outcome=RunOutcome.SUCCESS,
            agent_exit_code=0,
            duration_seconds=1.0,
        )
    )
    recorder.append(
        RunRecord(
            started_at=datetime(2026, 8, 2, tzinfo=UTC),
            finished_at=datetime(2026, 8, 2, 0, 0, 10, tzinfo=UTC),
            kind=RunKind.REFACTOR,
            outcome=RunOutcome.ERROR,
            agent_exit_code=3,
            duration_seconds=2.0,
        )
    )

    status, data = _get(f"http://127.0.0.1:{server.port}/api/runs")
    assert status == 200
    assert [r["task_id"] for r in data] == [None, "OLD"]
    assert data[0]["kind"] == "refactor"
    assert data[0]["outcome"] == "ERROR"

    status, data = _get(f"http://127.0.0.1:{server.port}/api/runs?limit=1")
    assert status == 200
    assert [r["task_id"] for r in data] == [None]

    status, data = _get(f"http://127.0.0.1:{server.port}/api/runs?limit=nope")
    assert status == 200
    assert len(data) == 2


def test_api_runs_skips_corrupt_lines(running_server, caplog):
    import logging

    server, config, _backlog = running_server
    recorder = RunRecorder(runs_path_for(config.backlog))
    recorder.append(
        RunRecord(
            started_at=datetime(2026, 8, 1, tzinfo=UTC),
            finished_at=datetime(2026, 8, 1, 0, 0, 10, tzinfo=UTC),
            kind=RunKind.TASK,
            task_id="GOOD",
            outcome=RunOutcome.SUCCESS,
            duration_seconds=1.0,
        )
    )
    with recorder.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")

    with caplog.at_level(logging.WARNING, logger="factory.runs"):
        status, data = _get(f"http://127.0.0.1:{server.port}/api/runs")

    assert status == 200
    assert [r["task_id"] for r in data] == ["GOOD"]
    assert "corrupt" in caplog.text


def test_binds_localhost_only(running_server):
    server, _config, _backlog = running_server
    assert server._httpd is not None
    host, _port = server._httpd.server_address
    assert host in ("127.0.0.1", "localhost")


def test_web_root_exists():
    assert WEB_ROOT.is_dir()


def test_web_root_serves_index(running_server):
    server, _config, _backlog = running_server
    status, body = _get(f"http://127.0.0.1:{server.port}/")
    assert status == 200
    assert isinstance(body, str)
    assert "<!doctype html" in body.lower()
    assert 'href="style.css"' in body
    assert 'src="app.js"' in body


def test_web_root_index_is_html(running_server):
    server, _config, _backlog = running_server
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/", timeout=5) as resp:
        assert resp.headers.get_content_type() == "text/html"


def test_web_root_index_references_existing_assets():
    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    referenced = re.findall(r'(?:href|src)="([^"]+\.(?:css|js))"', html)
    assert referenced
    for asset in referenced:
        assert (WEB_ROOT / asset).is_file(), f"missing asset referenced by index.html: {asset}"
    for asset in ("index.html", "style.css", "app.js"):
        assert (WEB_ROOT / asset).is_file(), f"missing web asset: {asset}"


def test_config_defaults_include_web_port():
    from factory.models import FactoryConfig

    cfg = FactoryConfig(agent_command="x")
    assert cfg.web_port == 8787
