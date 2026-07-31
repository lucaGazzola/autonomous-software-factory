"""LLM task decomposition: specification -> validated, topological tasks.

``TaskDecomposer`` asks the LLM for a JSON array of tasks, validates the
payload against the factory ``Task`` schema (via a strict draft model), and
then normalizes the result:

* ids are renumbered ``TASK-001``, ``TASK-002``, ... in output order;
* ``dependencies`` are remapped to the new ids and checked for topology
  (a task may only depend on tasks listed before it).

Invalid output triggers a bounded retry loop that feeds the validation error
back to the LLM; after ``max_attempts`` the decomposition fails loudly with
``DecompositionError`` rather than writing a broken backlog.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from factory.core.models import Task
from factory.generator.interview import LLMClient
from factory.generator.prompts import DECOMPOSITION_SYSTEM_PROMPT

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class DecompositionError(RuntimeError):
    """Raised when the LLM cannot produce a valid, topological task list."""


class TaskDraft(BaseModel):
    """Strict validation shape for a single LLM-emitted task."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = ""
    dependencies: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    files_to_modify: list[str] = Field(default_factory=list)


def extract_json(text: str) -> Any:
    """Extract a JSON payload from an LLM reply.

    Tolerates markdown code fences and stray prose by scanning for the first
    ``[`` / ``{`` and the matching last ``]`` / ``}``.
    """
    stripped = text.strip()
    fenced = _JSON_FENCE_RE.findall(stripped)
    if fenced:
        stripped = fenced[0].strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for open_, close in (("[", "]"), ("{", "}")):
        start, end = stripped.find(open_), stripped.rfind(close)
        if start != -1 and end > start:
            return json.loads(stripped[start : end + 1])
    raise json.JSONDecodeError("No JSON payload found in LLM output", text, 0)


class TaskDecomposer:
    """Convert a finalized specification into a validated ``Task`` backlog.

    Args:
        llm: An ``LLMClient`` producing the task JSON.
        max_attempts: How many times the LLM may retry after invalid output.
    """

    def __init__(self, llm: LLMClient, max_attempts: int = 3) -> None:
        self.llm = llm
        self.max_attempts = max(1, max_attempts)

    def decompose(self, specification: str) -> list[Task]:
        """Return tasks ordered topologically, or raise ``DecompositionError``."""
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            user_content = specification
            if last_error is not None:
                user_content += (
                    "\n\nYour previous output was rejected. Fix it and re-emit the "
                    f"full corrected JSON array.\nRejection: {last_error}"
                )
            try:
                raw = self.llm.complete(
                    [
                        {"role": "system", "content": DECOMPOSITION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    json_mode=True,
                )
                tasks = self._normalize(extract_json(raw))
                self._check_tasks(tasks)
                return tasks
            except (json.JSONDecodeError, ValidationError, DecompositionError) as exc:
                last_error = exc
        assert last_error is not None
        raise DecompositionError(
            f"LLM failed to produce a valid backlog after {self.max_attempts} "
            f"attempt(s): {last_error}"
        ) from last_error

    # ------------------------------------------------------------------ #
    # Validation and normalization                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize(payload: Any) -> list[Task]:
        """Validate the raw payload and renumber tasks to ``TASK-NNN`` ids."""
        if isinstance(payload, dict) and isinstance(payload.get("tasks"), list):
            payload = payload["tasks"]
        if not isinstance(payload, list):
            raise DecompositionError("expected a JSON array of tasks")
        drafts = [TaskDraft.model_validate(item) for item in payload]
        if not drafts:
            raise DecompositionError("task array is empty")

        id_map: dict[str, str] = {}
        for index, draft in enumerate(drafts):
            if draft.id in id_map:
                raise DecompositionError(f"duplicate task id: {draft.id!r}")
            id_map[draft.id] = f"TASK-{index + 1:03d}"

        tasks: list[Task] = []
        for draft in drafts:
            try:
                dependencies = [id_map[dep] for dep in draft.dependencies]
            except KeyError as exc:
                raise DecompositionError(
                    f"task {draft.id!r} depends on unknown task {exc.args[0]!r}"
                ) from exc
            tasks.append(
                Task(
                    id=id_map[draft.id],
                    title=draft.title,
                    description=draft.description,
                    dependencies=dependencies,
                    acceptance_criteria=draft.acceptance_criteria,
                    files_to_modify=draft.files_to_modify,
                )
            )
        return tasks

    @staticmethod
    def _check_tasks(tasks: list[Task]) -> None:
        """Verify topological order: dependencies must precede their dependents."""
        positions = {task.id: index for index, task in enumerate(tasks)}
        for task in tasks:
            for dependency in task.dependencies:
                position = positions.get(dependency)
                if position is None:
                    raise DecompositionError(f"{task.id} depends on unknown task {dependency!r}")
                if position >= positions[task.id]:
                    raise DecompositionError(
                        f"{task.id} depends on {dependency!r} which is not listed earlier "
                        "(violates topological order)"
                    )
