"""CLI entry point for ``factory generate-backlog``.

Runs the full pipeline: interactive interview -> LLM decomposition -> writes a
validated, topologically ordered backlog through the ``JSONBacklog``.
Ctrl+C anywhere during the interview saves progress to
``artifacts/interview_progress.json`` and exits cleanly.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from factory.backlog import JSONBacklog
from factory.generator.decomposer import DecompositionError, TaskDecomposer
from factory.generator.interview import InterviewSession, LiteLLMClient, LLMError
from factory.models import Task

DEFAULT_PROGRESS_PATH = Path("artifacts/interview_progress.json")

console = Console()


def resolve_model(args) -> str:
    """Pick the LLM model: CLI flag > ``FACTORY_LLM_MODEL`` > ``gpt-4o``."""
    return args.model or os.environ.get("FACTORY_LLM_MODEL", "gpt-4o")


async def _count_tasks(path: Path) -> int:
    """Number of tasks already stored in the backlog file (0 when absent)."""
    try:
        return len(await JSONBacklog(path).list_tasks())
    except OSError:
        return 0


async def _populate(path: Path, tasks: list[Task], force: bool) -> int:
    """Persist the generated tasks, atomically replacing the file under ``force``."""
    if force:
        path.unlink(missing_ok=True)
    adapter = JSONBacklog(path)
    for task in tasks:
        await adapter.create_task(task)
    return len(tasks)


def _render_backlog(tasks: list[Task]) -> None:
    """Print the generated tasks as a Rich table."""
    table = Table(title=f"Generated backlog ({len(tasks)} tasks)")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Dependencies", style="dim")
    table.add_column("Acceptance criteria", style="green")
    for task in tasks:
        criteria = task.acceptance_criteria[0] if task.acceptance_criteria else "—"
        table.add_row(
            task.id,
            task.title,
            ", ".join(task.dependencies) or "—",
            criteria,
        )
    console.print(table)


def cmd_generate_backlog(args) -> int:
    """Handle ``factory generate-backlog``."""
    output = Path(args.output)

    if args.force:
        console.print(f"[yellow]--force: existing backlog at {output} will be replaced.[/yellow]")
    elif asyncio.run(_count_tasks(output)):
        console.print(f"[red]{output} already contains tasks. Pass --force to overwrite it.[/red]")
        return 2

    idea = args.prompt or Prompt.ask("What are we building today?")
    llm = LiteLLMClient(model=resolve_model(args))

    session = InterviewSession(idea, llm, console=console)
    try:
        session.run()
    except KeyboardInterrupt:
        path = session.save_progress(DEFAULT_PROGRESS_PATH)
        console.print(
            f"[yellow]Interview interrupted. Progress saved to {path}. "
            "Rerun `factory generate-backlog` to start fresh.[/yellow]"
        )
        return 130
    except LLMError as exc:
        console.print(f"[red]Cannot start the interview:[/red] {escape(str(exc))}")
        return 1

    console.print(
        Panel.fit(
            "Decomposing the specification into executable tasks...",
            title="Backlog generation",
            border_style="cyan",
        )
    )
    try:
        tasks = TaskDecomposer(llm).decompose(session.specification())
    except (DecompositionError, LLMError) as exc:
        console.print(f"[red]Decomposition failed:[/red] {escape(str(exc))}")
        return 1

    created = asyncio.run(_populate(output, tasks, args.force))
    _render_backlog(tasks)
    console.print(
        Panel.fit(
            f"[green]{created} task(s) written to {output}[/green] — "
            "run `factory run --backlog "
            f"{output} --agent shell --command <agent command>` to execute.",
            border_style="green",
        )
    )
    return 0
