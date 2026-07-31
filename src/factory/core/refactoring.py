"""Proactive refactoring scanner: turns repository health into backlog tasks.

When the daemon wakes up to an empty backlog it runs ``RefactoringScanner``
instead of idling: the scanner collects a cheap repository snapshot (git
state + static heuristics), asks the LLM to review it, and — crucially —
*never executes any change itself*. The review is normalized into one or
more ``Task`` objects in ``OPEN`` state and injected into the backlog; the
orchestrator executes them on the next scheduled cycle. This keeps the
factory's own loop (backlog -> orchestrator -> agent) the single execution
path and the daemon purely generative.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from factory.adapters.backlog.base import BaseBacklogAdapter
from factory.adapters.git_manager import GitManager
from factory.core.llm import LiteLLMClient, LLMClient
from factory.core.models import RefactoringConfig, Task
from factory.generator.decomposer import extract_json
from factory.generator.prompts import REFACTORING_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

#: Lines that indicate unfinished work or known debt, used for cheap static findings.
_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)

#: File size (bytes) beyond which a file is flagged as a maintenance risk.
_LARGE_FILE_THRESHOLD = 500 * 1024


class ScanError(RuntimeError):
    """Raised when a refactoring scan cannot be completed."""


class RefactoringDraft(BaseModel):
    """Strict validation shape for one LLM-proposed improvement task."""

    title: str = Field(min_length=1)
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)


class RefactoringScanner:
    """Review a repository and propose improvement tasks to the backlog.

    Args:
        repo_path: Local repository the scanner reviews.
        backlog: Backlog the proposed tasks are injected into.
        git_manager: Git access for the repository snapshot (built lazily).
        llm: LLM backend; defaults to ``LiteLLMClient``.
        config: Policy (max tasks per scan, model).
    """

    def __init__(
        self,
        repo_path: str | Path,
        backlog: BaseBacklogAdapter,
        llm: LLMClient | None = None,
        config: RefactoringConfig | None = None,
        git_manager: GitManager | None = None,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.backlog = backlog
        self.config = config or RefactoringConfig()
        self.git_manager = git_manager or GitManager(self.repo_path)
        self.llm = llm or LiteLLMClient(model=self.config.model)

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    async def scan(self) -> list[Task]:
        """Review the repository and inject proposed tasks into the backlog.

        Returns the tasks created (empty when the review found nothing worth
        proposing). Never raises: review/LLM failures are logged and yield an
        empty list so a broken scan cannot take the daemon down.
        """
        try:
            snapshot = await self._collect_snapshot()
            drafts = await self._review(snapshot)
            return await self._inject(drafts)
        except ScanError as exc:
            logger.error("Refactoring scan failed: %s", exc)
            return []

    def cooldown_ok(self, last_scan_at: datetime | None, now: datetime | None = None) -> bool:
        """Return whether a new scan is allowed given the last one's timestamp."""
        if self.config.cooldown_minutes <= 0 or last_scan_at is None:
            return True
        now = now or datetime.now(UTC)
        if last_scan_at.tzinfo is None:
            last_scan_at = last_scan_at.replace(tzinfo=UTC)
        return (now - last_scan_at).total_seconds() >= self.config.cooldown_minutes * 60

    # ------------------------------------------------------------------ #
    # Pipeline                                                            #
    # ------------------------------------------------------------------ #

    async def _collect_snapshot(self) -> dict[str, Any]:
        """Gather git state plus cheap static findings about the repository."""
        git_state = await self.git_manager.a_snapshot()
        files = git_state.get("tracked_files", [])
        if not files:
            # Not a git repo (or nothing tracked): fall back to a filesystem
            # walk so the review still has evidence to work from.
            files = await asyncio.to_thread(self._walk_files)
        findings = await asyncio.to_thread(self._static_findings, files)
        return {
            "branch": git_state.get("branch"),
            "clean": git_state.get("clean"),
            "changes": git_state.get("changes", []),
            "findings": findings,
            "file_count": len(files),
        }

    def _walk_files(self) -> list[str]:
        """List repo files, skipping VCS/venv/artifacts noise directories."""
        skip = {
            ".git",
            ".venv",
            "__pycache__",
            "node_modules",
            ".pytest_cache",
            "dist",
            "build",
            "artifacts",
        }
        files: list[str] = []
        for path in sorted(self.repo_path.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.repo_path)
            if any(part in skip for part in rel.parts):
                continue
            files.append(rel.as_posix())
        return files

    def _static_findings(self, files: list[str]) -> list[dict[str, Any]]:
        """Cheap, deterministic repo health indicators (no LLM involved)."""
        findings: list[dict[str, Any]] = []
        todo_markers: dict[str, list[tuple[int, str]]] = {}
        largest: list[tuple[int, str]] = []

        for rel_path in files:
            path = self.repo_path / rel_path
            try:
                if not path.is_file() or path.stat().st_size > _LARGE_FILE_THRESHOLD * 20:
                    continue
                size = path.stat().st_size
                if size > _LARGE_FILE_THRESHOLD:
                    largest.append((size, rel_path))
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if _TODO_RE.search(line):
                    todo_markers.setdefault(rel_path, []).append((line_no, line.strip()[:120]))

        if todo_markers:
            findings.append(
                {
                    "type": "todo_markers",
                    "detail": {path: markers for path, markers in list(todo_markers.items())[:10]},
                }
            )
        if largest:
            findings.append(
                {
                    "type": "large_files",
                    "detail": sorted(((size, path) for size, path in largest), reverse=True)[:5],
                }
            )
        if not any("test" in path or "tests" in path for path in files):
            findings.append({"type": "no_tests", "detail": "no test files are tracked"})
        if not any(
            path.lower() == "readme.md" or path.lower().startswith("readme") for path in files
        ):
            findings.append({"type": "no_readme", "detail": "no README file is tracked"})
        return findings

    async def _review(self, snapshot: dict[str, Any]) -> list[RefactoringDraft]:
        """Ask the LLM for proposed improvements and validate them."""
        prompt = self._build_prompt(snapshot)
        try:
            raw = await asyncio.to_thread(
                self.llm.complete,
                [
                    {"role": "system", "content": REFACTORING_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:
            raise ScanError(f"LLM review failed: {exc}") from exc

        try:
            payload = extract_json(raw)
            if isinstance(payload, dict) and isinstance(payload.get("tasks"), list):
                payload = payload["tasks"]
            drafts = [RefactoringDraft.model_validate(item) for item in payload]
        except (ValidationError, ValueError) as exc:
            raise ScanError(f"LLM review output was not valid tasks: {exc}") from exc

        return drafts[: self.config.max_tasks_per_scan]

    def _build_prompt(self, snapshot: dict[str, Any]) -> str:
        """Render the repository snapshot for the LLM review."""
        findings = snapshot.get("findings", [])
        findings_text = (
            "\n".join(f"- {f.get('type')}: {f.get('detail')}" for f in findings) or "- none"
        )
        return (
            f"# Repository snapshot: {self.repo_path}\n"
            f"- branch: {snapshot.get('branch')}\n"
            f"- working tree clean: {snapshot.get('clean')}\n"
            f"- tracked files: {snapshot.get('file_count')}\n"
            f"- pending changes: {len(snapshot.get('changes', []))}\n\n"
            f"## Static findings\n{findings_text}\n\n"
            f"Review the repository and propose up to {self.config.max_tasks_per_scan} "
            "improvement tasks as JSON."
        )

    async def _inject(self, drafts: list[RefactoringDraft]) -> list[Task]:
        """Create backlog tasks with collision-free ``REFACTOR-NNN`` ids."""
        created: list[Task] = []
        existing = {task.id for task in await self.backlog.list_tasks()}
        sequence = 1
        for draft in drafts:
            task_id = f"REFACTOR-{sequence:03d}"
            while task_id in existing:
                sequence += 1
                task_id = f"REFACTOR-{sequence:03d}"
            existing.add(task_id)
            sequence += 1
            task = await self.backlog.create_task(
                Task(
                    id=task_id,
                    title=draft.title,
                    description=draft.description,
                    acceptance_criteria=draft.acceptance_criteria,
                    files_to_modify=draft.files_to_modify,
                    metadata={"source": "refactoring-scanner"},
                )
            )
            created.append(task)
        return created
