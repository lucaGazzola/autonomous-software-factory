"""Central multi-instance web dashboard (``factory web``).

A standalone, read-only server that aggregates every factory registered in
the instance registry (:mod:`factory.instances`). Unlike the per-daemon
dashboard (:mod:`factory.server`) — which is embedded in one daemon and only
shows that single factory — the central dashboard reads each instance's data
straight from its files (``backlog.json``, ``runs.jsonl``, ``factory.log``,
``BLOCKER.md``), so it works whether or not that instance's daemon is
running.

Routes:

* ``GET /`` — home page listing every registered instance: name, repo,
  daemon state, last outcome, next run, and backlog counts.
* ``GET /instances/<name>/`` — per-instance page: that instance's kanban
  backlog plus tabs for logs, runs, blocker, and config.
* ``GET /api/instances`` — JSON summary of every registered instance.
* ``GET /api/instances/<name>/tasks``, ``/tasks/<id>``, ``/status``,
  ``/logs?lines=N``, ``/runs?limit=N``, ``/blocker``, ``/config`` — the
  per-instance API, mirroring the embedded daemon's endpoints.

An unknown instance name returns ``404``; a registered instance with missing
data files renders with empty data and ``daemon_running=false`` rather than
erroring. Everything stays read-only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from factory.backlog import backlog_status_counts
from factory.daemon import read_lock_pid
from factory.instances import (
    InstanceInfo,
    get_instance,
    list_instances,
    registry_path,
)
from factory.models import Task, TaskStatus
from factory.runs import RunRecorder, runs_path_for
from factory.web_common import (
    DEFAULT_LOG_LINES,
    DEFAULT_RUN_LIMIT,
    MAX_LOG_LINES,
    MAX_RUN_LIMIT,
    clamp_query_int,
    guess_content_type,
    iso,
    json_bytes,
    safe_static_path,
    tail_lines,
)

logger = logging.getLogger(__name__)

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8790

_HOME_PAGE = "/central/index.html"
_INSTANCE_PAGE = "/central/instance.html"


def _read_tasks(config: Any) -> list[Task]:
    """All tasks for ``config``, tolerating a missing or corrupt backlog.

    Reads the file directly so the dashboard never writes to an instance's
    files (``JSONBacklog`` renames a corrupt backlog; here it is skipped).
    """
    if config is None:
        return []
    try:
        data = json.loads(Path(config.backlog).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return []
    parsed: list[Task] = []
    for entry in tasks:
        if not isinstance(entry, dict):
            continue
        try:
            parsed.append(Task.model_validate(entry))
        except ValidationError:
            continue
    return parsed


def _blocker_content(config: Any) -> str | None:
    """The ``BLOCKER.md`` contents, or ``None`` when absent or unreadable."""
    if config is None:
        return None
    blocker = Path(config.blocker_file)
    if not blocker.is_file():
        return None
    try:
        return blocker.read_text(encoding="utf-8")
    except OSError:
        return None


def _last_outcome(config: Any) -> str | None:
    """The most recent run's outcome string from ``runs.jsonl``, or ``None``."""
    if config is None:
        return None
    last_run = RunRecorder(runs_path_for(config.backlog)).read_last()
    return last_run.outcome.value if last_run is not None else None


def _next_run(info: InstanceInfo, config: Any) -> str | None:
    """An estimate of the next scheduled run, when it can be derived.

    With the daemon running and at least one recorded run, the next run is
    approximated as the last run's finish time plus the interval. Otherwise
    there is no way to know the next run from the files, so ``None``.
    """
    if not info.daemon_running or config is None:
        return None
    last_run = RunRecorder(runs_path_for(config.backlog)).read_last()
    if last_run is None:
        return None
    estimate = last_run.finished_at + timedelta(minutes=config.interval_minutes)
    return iso(estimate)


def _status_payload(info: InstanceInfo) -> dict[str, Any]:
    """The per-instance status payload, mirroring the embedded ``/api/status``."""
    config = info.config
    if config is None:
        return {
            "name": info.name,
            "repo": None,
            "interval_minutes": None,
            "daemon_running": False,
            "pid": None,
            "last_outcome": None,
            "next_run_at": None,
        }
    return {
        "name": config.name,
        "repo": str(config.repo),
        "interval_minutes": config.interval_minutes,
        "daemon_running": info.daemon_running,
        "pid": read_lock_pid(config.backlog.with_suffix(".lock")),
        "last_outcome": _last_outcome(config),
        "next_run_at": _next_run(info, config),
    }


def _summary(info: InstanceInfo) -> dict[str, Any]:
    """One home-page/API row for a registered instance."""
    config = info.config
    if config is None:
        return {
            "name": info.name,
            "config_path": str(info.config_path),
            "repo": None,
            "daemon_running": False,
            "last_outcome": None,
            "next_run_at": None,
            "backlog_counts": {status.value: 0 for status in TaskStatus},
        }
    counts = backlog_status_counts(_read_tasks(config))
    return {
        "name": info.name,
        "config_path": str(info.config_path),
        "repo": str(config.repo),
        "daemon_running": info.daemon_running,
        "last_outcome": _last_outcome(config),
        "next_run_at": _next_run(info, config),
        "backlog_counts": counts,
    }


def make_handler() -> type[BaseHTTPRequestHandler]:
    """Build the request-handler class for the central dashboard."""

    class CentralRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("central web %s - %s", self.address_string(), format % args)

        def _send_json(self, status: int, payload: Any) -> None:
            body = json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_static(self, static: Path | None) -> None:
            if static is None:
                self._send_json(404, {"error": "not found"})
                return
            self._send_bytes(200, static.read_bytes(), guess_content_type(static))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            try:
                if path == "/api/instances":
                    self._send_json(200, [_summary(info) for info in list_instances()])
                    return
                if path.startswith("/api/instances/"):
                    self._handle_instance_api(path, query)
                    return
                if path == "/":
                    self._send_static(safe_static_path(_HOME_PAGE))
                    return
                if path.startswith("/instances/"):
                    self._handle_instance_page(path)
                    return
                static = safe_static_path(path)
                if static is not None:
                    self._send_static(static)
                    return
                self._send_json(404, {"error": "not found"})
            except Exception:
                logger.exception("Web request failed: %s", path)
                self._send_json(500, {"error": "internal server error"})

        def _handle_instance_page(self, path: str) -> None:
            name = unquote(path[len("/instances/") :]).strip("/")
            if not name or "/" in name or get_instance(name) is None:
                self._send_json(404, {"error": "unknown instance"})
                return
            self._send_static(safe_static_path(_INSTANCE_PAGE))

        def _handle_instance_api(self, path: str, query: dict[str, list[str]]) -> None:
            parts = path[len("/api/instances/") :].split("/")
            name = unquote(parts[0])
            info = get_instance(name)
            if info is None:
                self._send_json(404, {"error": "unknown instance"})
                return
            if len(parts) < 2:
                self._send_json(404, {"error": "not found"})
                return
            endpoint = parts[1]

            if endpoint == "tasks":
                if len(parts) == 2:
                    tasks = _read_tasks(info.config)
                    self._send_json(200, [t.model_dump(mode="json") for t in tasks])
                    return
                if len(parts) == 3:
                    task_id = unquote(parts[2])
                    for task in _read_tasks(info.config):
                        if task.id == task_id:
                            self._send_json(200, task.model_dump(mode="json"))
                            return
                    self._send_json(404, {"error": "not found"})
                    return
                self._send_json(404, {"error": "not found"})
                return
            if len(parts) > 2:
                self._send_json(404, {"error": "not found"})
                return

            if endpoint == "status":
                self._send_json(200, _status_payload(info))
                return
            if endpoint == "logs":
                n = clamp_query_int(query, "lines", DEFAULT_LOG_LINES, MAX_LOG_LINES)
                if info.config is None:
                    self._send_json(200, {"lines": []})
                    return
                lines = tail_lines(Path(info.config.log_file), n)
                self._send_json(200, {"lines": lines})
                return
            if endpoint == "runs":
                n = clamp_query_int(query, "limit", DEFAULT_RUN_LIMIT, MAX_RUN_LIMIT)
                if info.config is None:
                    self._send_json(200, [])
                    return
                records = RunRecorder(runs_path_for(info.config.backlog)).read(limit=n)
                self._send_json(200, [r.model_dump(mode="json") for r in records])
                return
            if endpoint == "blocker":
                self._send_json(200, {"content": _blocker_content(info.config)})
                return
            if endpoint == "config":
                if info.config is None:
                    self._send_json(200, {"error": "config not available"})
                    return
                self._send_json(200, info.config.model_dump(mode="json"))
                return
            self._send_json(404, {"error": "not found"})

    return CentralRequestHandler


class CentralWebServer:
    """Threading HTTP server lifecycle around the central dashboard."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int | None:
        """The port actually bound, once started (port 0 picks a free one)."""
        if self._httpd is None:
            return None
        return int(self._httpd.server_address[1])

    def start(self) -> bool:
        """Bind and start serving in a background thread. False on bind failure."""
        handler = make_handler()
        try:
            httpd = ThreadingHTTPServer((self.host, self._port), handler)
        except OSError as exc:
            logger.error(
                "Central web server failed to bind %s:%s: %s",
                self.host,
                self._port,
                exc,
            )
            return False
        self._httpd = httpd
        thread = threading.Thread(
            target=httpd.serve_forever,
            name="factory-central-web",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        logger.info(
            "Central web server listening on http://%s:%s",
            self.host,
            httpd.server_address[1],
        )
        return True

    def stop(self) -> None:
        """Stop the server and join the serve thread."""
        httpd = self._httpd
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._httpd = None
        self._thread = None
        logger.info("Central web server stopped.")


def _instance_count() -> int:
    """The number of registered instances (used by the CLI banner)."""
    return len(list_instances())


async def _serve_forever(server: CentralWebServer, host: str) -> None:
    """Run the foreground server until SIGINT/SIGTERM, then stop it."""
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    Console().print(
        Panel.fit(
            f"[bold]Forgeo central dashboard[/bold]\n"
            f"[bold]Listening:[/bold] http://{host}:{server.port}\n"
            f"[bold]Instances:[/bold] {_instance_count()} registered "
            f"(registry: {registry_path()})",
            title="Forgeo Web",
            border_style="green",
        )
    )
    await stop_event.wait()


def run_foreground(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> int:
    """Start ``factory web`` in the foreground; returns the process exit code.

    Binds the dashboard, prints the listening banner, and blocks until the
    user interrupts it with Ctrl-C or a SIGTERM arrives.
    """
    server = CentralWebServer(host=host, port=port)
    if not server.start():
        Console().print(f"[red]Central dashboard failed to bind {host}:{port}.[/red]")
        return 1
    try:
        asyncio.run(_serve_forever(server, host))
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0
