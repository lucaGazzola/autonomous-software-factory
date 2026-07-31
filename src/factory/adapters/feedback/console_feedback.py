"""Interactive terminal (stdin/stdout) feedback provider.

The default HITL channel: blocked tasks surface as a highlighted prompt on
the terminal, the operator picks ``retry``, ``reply`` (with guidance), or
``abort``, and the choice is returned to the orchestrator. Status alerts
render as colored panels.
"""

from __future__ import annotations

import asyncio

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.core.models import ResponseAction, UserResponse

_ACTION_DESCRIPTIONS = {
    ResponseAction.RETRY: "run the agent again unchanged",
    ResponseAction.REPLY: "provide guidance, then run the agent again",
    ResponseAction.ABORT: "give up and mark the task FAILED",
}


class ConsoleFeedbackProvider(BaseFeedbackProvider):
    """Prompts the human operator through the terminal."""

    name = "console"

    def __init__(self, console: Console | None = None) -> None:
        """Create the provider, optionally with a pre-configured Rich console."""
        self.console = console or Console()

    async def request_input(self, task_id: str, prompt: str) -> UserResponse:
        """Interactively ask the operator how to proceed on a blocked task."""
        self.console.print(
            Panel.fit(
                f"[yellow]{prompt}[/yellow]",
                title=f"Task {task_id} requires human input",
                border_style="yellow",
            )
        )
        for action, description in _ACTION_DESCRIPTIONS.items():
            self.console.print(f"  [bold cyan]{action.value}[/bold cyan] — {description}")

        chosen = await asyncio.to_thread(
            Prompt.ask,
            "How should the factory proceed?",
            choices=[a.value for a in ResponseAction],
            default=ResponseAction.RETRY.value,
        )
        action = ResponseAction(chosen)

        message = ""
        if action is ResponseAction.REPLY:
            message = await asyncio.to_thread(Prompt.ask, "Provide guidance for the agent")
        return UserResponse(task_id=task_id, action=action, message=message)

    async def notify(self, task_id: str, message: str) -> None:
        """Render a non-blocking status alert for a task."""
        self.console.print(
            Panel.fit(
                str(message),
                title=f"Task {task_id}",
                border_style="cyan",
            ),
            highlight=False,
        )
