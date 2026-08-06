"""Read-only local HTTP API served while the factory daemon is running.

Binds ``config.web_host`` (default ``127.0.0.1``) using the stdlib
``http.server`` / ``socketserver``. A bind failure is logged and the daemon
continues without the API.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from factory.backlog import JSONBacklog
from factory.daemon import FactoryDaemon
from factory.models import FactoryConfig
from factory.runs import RunRecorder, runs_path_for
from factory.web_common import (
    DEFAULT_LOG_LINES,
    DEFAULT_RUN_LIMIT,
    MAX_LOG_LINES,
    MAX_RUN_LIMIT,
    WEB_ROOT,
    clamp_query_int,
    guess_content_type,
    iso,
    json_bytes,
    safe_static_path,
    tail_lines,
)

__all__ = ["WEB_ROOT", "ApiState", "WebServer"]

logger = logging.getLogger(__name__)


class ApiState:
    """Shared state for request handlers."""

    def __init__(
        self,
        config: FactoryConfig,
        backlog: JSONBacklog,
        daemon: FactoryDaemon | None = None,
    ) -> None:
        self.config = config
        self.backlog = backlog
        self.daemon = daemon
        self.loop: asyncio.AbstractEventLoop | None = None


def make_handler(state: ApiState) -> type[BaseHTTPRequestHandler]:
    """Build a request-handler class bound to ``state``."""

    class FactoryRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("web %s - %s", self.address_string(), format % args)

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

        def _run_async(self, coro: Any) -> Any:
            loop = state.loop
            if loop is None or not loop.is_running():
                return asyncio.run(coro)
            future = asyncio.run_coroutine_threadsafe(coro, loop)
            return future.result(timeout=30)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)

            try:
                if path == "/api/tasks":
                    tasks = self._run_async(state.backlog.list_tasks())
                    self._send_json(200, [t.model_dump(mode="json") for t in tasks])
                    return
                if path.startswith("/api/tasks/"):
                    task_id = unquote(path[len("/api/tasks/") :])
                    if not task_id or "/" in task_id:
                        self._send_json(404, {"error": "not found"})
                        return
                    task = self._run_async(state.backlog.get_task(task_id))
                    if task is None:
                        self._send_json(404, {"error": "not found"})
                        return
                    self._send_json(200, task.model_dump(mode="json"))
                    return
                if path == "/api/status":
                    self._send_json(200, self._status_payload())
                    return
                if path == "/api/config":
                    self._send_json(200, state.config.model_dump(mode="json"))
                    return
                if path == "/api/logs":
                    n = clamp_query_int(query, "lines", DEFAULT_LOG_LINES, MAX_LOG_LINES)
                    lines = tail_lines(Path(state.config.log_file), n)
                    self._send_json(200, {"lines": lines})
                    return
                if path == "/api/runs":
                    n = clamp_query_int(query, "limit", DEFAULT_RUN_LIMIT, MAX_RUN_LIMIT)
                    records = RunRecorder(runs_path_for(state.backlog.path)).read(limit=n)
                    self._send_json(200, [r.model_dump(mode="json") for r in records])
                    return
                if path == "/api/blocker":
                    blocker = Path(state.config.blocker_file)
                    content: str | None
                    if blocker.is_file():
                        try:
                            content = blocker.read_text(encoding="utf-8")
                        except OSError:
                            content = None
                    else:
                        content = None
                    self._send_json(200, {"content": content})
                    return

                static = safe_static_path(path)
                if static is not None:
                    data = static.read_bytes()
                    ctype = guess_content_type(static)
                    self._send_bytes(200, data, ctype)
                    return
                self._send_json(404, {"error": "not found"})
            except Exception:
                logger.exception("Web request failed: %s", path)
                self._send_json(500, {"error": "internal server error"})

        def _status_payload(self) -> dict[str, Any]:
            daemon = state.daemon
            pid = daemon.pid if daemon is not None else os.getpid()
            interval = state.config.interval_minutes
            last_outcome = daemon.last_outcome if daemon is not None else None
            next_run: str | None = None
            if daemon is not None and daemon.next_run_at is not None:
                next_run = iso(daemon.next_run_at)
            return {
                "pid": pid,
                "name": state.config.name,
                "interval_minutes": interval,
                "last_outcome": last_outcome,
                "next_run_at": next_run,
            }

    return FactoryRequestHandler


class WebServer:
    """Threading HTTP server lifecycle around the factory API."""

    def __init__(
        self,
        config: FactoryConfig,
        backlog: JSONBacklog,
        daemon: FactoryDaemon | None = None,
    ) -> None:
        self.config = config
        self.state = ApiState(config, backlog, daemon)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int | None:
        if self._httpd is None:
            return None
        return int(self._httpd.server_address[1])

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        """Bind and start serving. Returns False when disabled or bind fails."""
        port = self.config.web_port
        host = self.config.web_host
        if port == 0:
            logger.info("Web server disabled (web_port=0).")
            return False
        self.state.loop = loop
        handler = make_handler(self.state)
        try:
            httpd = ThreadingHTTPServer((host, port), handler)
        except OSError as exc:
            logger.error(
                "Web server failed to bind %s:%s: %s",
                host,
                port,
                exc,
            )
            return False
        self._httpd = httpd
        thread = threading.Thread(
            target=httpd.serve_forever,
            name="factory-web",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        logger.info(
            "Web server listening on http://%s:%s",
            host,
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
        logger.info("Web server stopped.")
