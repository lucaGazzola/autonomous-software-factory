"""Guided first-time setup: ``factory init``.

Walks the user through the three decisions the factory needs before it can
work on a repository:

1. the factory folder — where the backlog, ``BLOCKER.md`` and the log live
   (inside the project, gitignored by default);
2. the coding agent command — any shell command that reads ``$FACTORY_TASK``
   and works in the repository (e.g. ``claude -p "$FACTORY_TASK"``);
3. the refactoring prompt — the default is offered; a custom one can be
   pasted instead.

The result is written as ``factory.yaml`` next to the project.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from factory.models import DEFAULT_REFACTOR_PROMPT

DEFAULT_FACTORY_DIR = ".factory"
DEFAULT_AGENT_COMMAND = 'aider --message "$FACTORY_TASK"'

SetupInput = Callable[[str], str]


def _ask_text(input_fn: SetupInput | None, prompt: str, default: str | None = None) -> str:
    """Free-text question; ``input_fn`` replaces the terminal in tests."""
    if input_fn is not None:
        return input_fn(prompt)
    if default is None:
        return Prompt.ask(prompt)
    return Prompt.ask(prompt, default=default)


def _ask_yes_no(input_fn: SetupInput | None, prompt: str, default: bool = True) -> bool:
    """Yes/no question; ``input_fn`` replaces the terminal in tests."""
    if input_fn is not None:
        return input_fn(prompt).strip().lower() in ("y", "yes")
    return Confirm.ask(prompt, default=default)


def _ask_multiline(input_fn: SetupInput | None, prompt: str, console: Console) -> str:
    """Multi-line answer; an empty line finishes it."""
    if input_fn is not None:
        lines = []
        while True:
            line = input_fn(prompt)
            if not line.strip():
                break
            lines.append(line.strip())
        return "\n".join(lines)
    console.print(prompt)
    lines = []
    while True:
        line = Prompt.ask("[dim](paste a line; an empty line finishes)[/dim]")
        if not line.strip():
            break
        lines.append(line.strip())
    return "\n".join(lines)


def add_gitignore(project_root: Path, line: str) -> bool:
    """Append ``line`` to ``<project_root>/.gitignore`` when absent."""
    path = project_root / ".gitignore"
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if line in content.splitlines():
            return False
        content = content.rstrip("\n") + "\n" + line + "\n"
    else:
        content = line + "\n"
    path.write_text(content, encoding="utf-8")
    return True


def run_setup(
    base_dir: Path,
    config_path: Path,
    *,
    console: Console | None = None,
    input_fn: SetupInput | None = None,
) -> dict[str, object] | None:
    """Interactively collect the configuration and write it to ``config_path``.

    Args:
        base_dir: Directory the config lives in (the project root); all
            generated paths are relative to it.
        config_path: Where to write the YAML config.
        console: Rich console for output (a new one when omitted).
        input_fn: Replacement for the terminal prompts (tests).

    Returns the written YAML payload, or ``None`` when the setup was aborted.
    """
    out = console or Console()
    root = base_dir.resolve()
    if not (root / ".git").exists():
        out.print(
            "[yellow]Warning: no .git directory here — the factory works on a git "
            "repository.[/yellow]"
        )

    factory_dir = _ask_text(
        input_fn,
        f"[bold]Factory folder[/bold] for backlog, BLOCKER.md and logs "
        f"[default {DEFAULT_FACTORY_DIR}]",
        default=DEFAULT_FACTORY_DIR,
    ).strip()
    factory_dir = factory_dir.removeprefix("./").rstrip("/") or DEFAULT_FACTORY_DIR
    if Path(factory_dir).is_absolute():
        out.print("[red]The factory folder must live inside the project. Aborting.[/red]")
        return None
    if ".." in Path(factory_dir).parts:
        out.print(
            "[yellow]Note: the factory folder escapes the project root — the "
            "gitignore rule will not protect it.[/yellow]"
        )

    command = _ask_text(
        input_fn,
        f"[bold]Coding agent command[/bold] [default {DEFAULT_AGENT_COMMAND}]",
        default=DEFAULT_AGENT_COMMAND,
    ).strip() or DEFAULT_AGENT_COMMAND
    if "$FACTORY_TASK" not in command:
        out.print(
            "[yellow]Note: the command never references $FACTORY_TASK, so the "
            "agent will not receive the task text.[/yellow]"
        )

    if _ask_yes_no(input_fn, "[bold]Use the default refactor prompt?[/bold]", default=True):
        refactor_prompt = DEFAULT_REFACTOR_PROMPT
    else:
        out.print("[bold]Your refactor prompt[/bold] (used when the backlog is empty):")
        refactor_prompt = (
            _ask_multiline(input_fn, "[dim](paste a line; empty line finishes)[/dim]", out)
            or DEFAULT_REFACTOR_PROMPT
        )

    if _ask_yes_no(
        input_fn,
        f"[bold]Add '{escape(factory_dir)}/' to .gitignore?[/bold]",
        default=True,
    ):
        if add_gitignore(root, factory_dir + "/"):
            out.print(f"[green]Added {factory_dir}/ to .gitignore.[/green]")
        else:
            out.print(f"[dim]{factory_dir}/ already in .gitignore.[/dim]")

    payload = {
        "name": root.name or "my-factory",
        "repo": ".",
        "interval_minutes": 60,
        "branch": "main",
        "backlog": f"{factory_dir}/backlog.json",
        "blocker_file": f"{factory_dir}/BLOCKER.md",
        "agent_command": command,
        "refactor_prompt": refactor_prompt,
        "log_file": f"{factory_dir}/factory.log",
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
    config_path.write_text(
        "# Software Factory configuration — generated by `factory init`.\n"
        "# Relative paths resolve against this file's directory.\n"
        "# Re-run `factory init --force` to regenerate. See README.md for all keys.\n\n"
        + body,
        encoding="utf-8",
    )
    (root / factory_dir).mkdir(parents=True, exist_ok=True)

    out.print(
        Panel.fit(
            f"[bold]Factory configured[/bold] in {config_path}\n"
            f"[bold]Repo:[/bold] {root}\n"
            f"[bold]Backlog:[/bold] {(root / factory_dir) / 'backlog.json'}\n"
            f"[bold]Agent:[/bold] {escape(command)}\n"
            f"[bold]Next:[/bold] factory start --config {config_path.name}",
            title="Software Factory",
            border_style="green",
        )
    )
    return payload
