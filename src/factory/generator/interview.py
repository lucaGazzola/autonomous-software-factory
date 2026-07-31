"""Interactive interview engine for the backlog generator.

``InterviewSession`` runs a grilling chat loop between the user and an LLM
playing the Pragmatic Product Architect: the LLM asks 1-2 targeted questions
per turn (each with an explicit recommendation), the session tracks which
critical specs (Data, API, Security, UX, Testing) have been covered, and the
loop ends when the user triggers an exit ("/done", "let's build it", ...) or
confirms the automatic wrap-up suggestion.

The LLM backend is a small ``LLMClient`` protocol; ``LiteLLMClient`` is the
default implementation (model selected via the ``FACTORY_LLM_MODEL``
environment variable). Tests inject a scripted fake client instead.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Prompt

from factory.generator.prompts import GRILLING_SYSTEM_PROMPT

#: Spec areas the interview must cover before the session suggests wrapping up.
CRITICAL_TOPICS: tuple[str, ...] = ("Data", "API", "Security", "UX", "Testing")

#: Keyword groups used to detect topic coverage from the architect's messages.
TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Data": ("data", "storage", "database", "db", "schema", "sqlite", "postgres", "model"),
    "API": ("api", "endpoint", "rest", "graphql", "cli", "integration"),
    "Security": ("security", "auth", "authentication", "authorization", "login", "password",
                 "jwt", "oauth", "secret", "permission", "sso"),
    "UX": ("ux", "ui", "user interface", "user experience", "frontend", "design", "accessibility"),
    "Testing": ("test", "testing", "qa", "verification", "test suite", "pytest"),
}

#: Phrases that terminate the interview (normalized: lowercased, stripped).
EXIT_PHRASES: frozenset[str] = frozenset(
    {
        "done", "exit", "quit", "stop", "end", "enough", "that's enough", "that's all",
        "finalize", "proceed", "wrap it up", "build it", "lets build it", "let's build it",
        "start building", "move on", "good enough",
        "/done", "/exit", "/quit", "/stop", "/end",
    }
)

HELP_TEXT = """\
Available commands while interviewing:
  /done, "let's build it", "build it"   finish the interview and generate the backlog
  /help                                 show this help
Anything else is treated as an answer to the current question.
"""

#: Lines of the form "Recommendation: ..." get dedicated styling in the UI.
RECOMMENDATION_RE = re.compile(r"^\s*(?:\*\*)?[Rr]ecommendation[^\n:]*:\s*(.+)$", re.MULTILINE)


class LLMError(RuntimeError):
    """Raised when the LLM backend cannot produce a response."""


class LLMClient(Protocol):
    """Minimal chat-completion contract used by the generator components."""

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        """Send ``messages`` and return the assistant's text reply."""
        ...


class LiteLLMClient:
    """``LLMClient`` backed by the litellm SDK (OpenAI, Anthropic, ...).

    Args:
        model: Model name; falls back to the ``FACTORY_LLM_MODEL`` environment
            variable and finally to ``gpt-4o``.
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.environ.get("FACTORY_LLM_MODEL", "gpt-4o")

    def complete(self, messages: list[dict[str, str]], *, json_mode: bool = False) -> str:
        try:
            import litellm
        except ImportError as exc:
            raise LLMError(
                "The LLM backend requires litellm: `pip install 'software-factory[llm]'`"
            ) from exc
        kwargs: dict = {"model": self.model, "messages": messages, "temperature": 0.4}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:  # SDK raises a grab-bag of provider errors.
            raise LLMError(f"LLM call to {self.model!r} failed: {exc}") from exc
        content = response.choices[0].message.content
        if not content:
            raise LLMError(f"LLM call to {self.model!r} returned an empty response")
        return content


def _normalize_input(text: str) -> str:
    """Normalize free-form user input for exit-phrase matching."""
    return re.sub(r"[.!?,]+$", "", text.strip().lower().replace("\u2019", "'"))


class InterviewSession:
    """Interactive grilling loop between the user and the Product Architect.

    Args:
        idea: The initial raw product idea that starts the interview.
        llm: An ``LLMClient`` used to generate architect turns.
        console: Rich console for output (defaults to a new stdout console).
        input_fn: Callable receiving the prompt text and returning the user's
            reply; defaults to ``rich.prompt.Prompt.ask``. Tests inject a
            scripted callable.
    """

    MIN_LLM_TURNS = 2

    def __init__(
        self,
        idea: str,
        llm: LLMClient,
        console: Console | None = None,
        input_fn: Callable[..., str] | None = None,
    ) -> None:
        self.idea = idea
        self.llm = llm
        self.console = console or Console()
        self.input_fn = input_fn or Prompt.ask
        self.messages: list[dict[str, str]] = [
            {"role": "system", "content": GRILLING_SYSTEM_PROMPT},
            {"role": "user", "content": f"Initial idea: {idea}"},
        ]
        self.covered_topics: set[str] = set()
        self.decisions: list[tuple[str, str]] = []
        self.turns = 0
        self._wrapup_offered = False
        self.finalized = False

    # ------------------------------------------------------------------ #
    # Public API                                                          #
    # ------------------------------------------------------------------ #

    def run(self) -> InterviewSession:
        """Run the interview loop until the user ends it; returns ``self``."""
        self.console.print(
            Panel.fit(
                f"[bold]Idea:[/bold] {escape(self.idea)}\n"
                "[dim]Answer the architect's questions. Type [bold]/done[/bold] "
                "or \"let's build it\" anytime to finish.[/dim]",
                title="Interactive Backlog Generator",
                subtitle="Pragmatic Product Architect",
                border_style="blue",
            )
        )
        assistant = self._ask_llm()
        while True:
            self._render_assistant(assistant)
            self._update_topics(assistant)

            if self._ready_to_wrap_up() and not self._wrapup_offered:
                self._wrapup_offered = True
                if self._confirm("Specification coverage is complete.", default=True):
                    break

            reply = self.input_fn("You")
            if self._is_help(reply):
                self.console.print(Panel.fit(HELP_TEXT, title="Interview help", border_style="dim"))
                continue
            if self._is_exit(reply):
                if not self._all_critical_covered():
                    self.console.print(
                        f"[yellow]Not everything is pinned down yet — missing: "
                        f"{', '.join(self._remaining_topics())}.[/yellow]"
                    )
                    if not self._confirm("Finalize the specification anyway?", default=False):
                        continue
                break

            self.decisions.append((assistant, reply.strip()))
            self.messages.append(
                {
                    "role": "user",
                    "content": self._with_session_state(reply.strip()),
                }
            )
            assistant = self._ask_llm()

        self.finalized = True
        self.console.print("[green]Interview complete. Finalizing specification...[/green]")
        return self

    def specification(self) -> str:
        """Render the finalized spec (idea, decision log, raw transcript)."""
        lines = [
            "# Software specification",
            f"**Idea:** {self.idea}",
            f"**Topics covered:** {', '.join(sorted(self.covered_topics)) or 'none'}",
            "",
            "## Decision log",
        ]
        for question, answer in self.decisions:
            lines += [f"### Q: {question}", f"A: {answer}", ""]
        lines += ["## Raw transcript", json.dumps(self.messages, indent=2)]
        return "\n".join(lines)

    def save_progress(self, path: str | Path | None = None) -> Path:
        """Persist the current session state; returns the file written.

        Used on Ctrl+C so a half-finished interview is not lost.
        """
        target = Path(path or "artifacts/interview_progress.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "idea": self.idea,
            "covered_topics": sorted(self.covered_topics),
            "messages": self.messages,
        }
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return target

    # ------------------------------------------------------------------ #
    # Loop internals                                                      #
    # ------------------------------------------------------------------ #

    def _ask_llm(self) -> str:
        """Send accumulated messages, store and return the assistant reply."""
        content = self.llm.complete(self.messages)
        self.messages.append({"role": "assistant", "content": content})
        self.turns += 1
        return content

    def _update_topics(self, text: str) -> None:
        lowered = text.lower()
        for topic, keywords in TOPIC_KEYWORDS.items():
            if any(kw in lowered for kw in keywords):
                self.covered_topics.add(topic)

    def _remaining_topics(self) -> list[str]:
        return [t for t in CRITICAL_TOPICS if t not in self.covered_topics]

    def _all_critical_covered(self) -> bool:
        return not self._remaining_topics()

    def _ready_to_wrap_up(self) -> bool:
        return self.turns >= self.MIN_LLM_TURNS and self._all_critical_covered()

    def _with_session_state(self, reply: str) -> str:
        """Attach the coverage checklist so the architect steers remaining gaps."""
        state = (
            f"[Session state] Covered: {', '.join(sorted(self.covered_topics)) or 'none'} "
            f"| Remaining: {', '.join(self._remaining_topics()) or 'none'}"
        )
        return f"{reply}\n\n{state}"

    def _confirm(self, message: str, *, default: bool) -> bool:
        """Ask a yes/no wrap-up question; returns True when the user agrees."""
        choice = self.input_fn(
            f"{message} Finalize the specification and generate the backlog?",
            choices=["y", "n"],
            default="y" if default else "n",
        )
        return str(choice).strip().lower() in {"y", "yes"}

    @staticmethod
    def _is_exit(reply: str) -> bool:
        return _normalize_input(reply) in EXIT_PHRASES

    @staticmethod
    def _is_help(reply: str) -> bool:
        return reply.strip().lower() == "/help"

    # ------------------------------------------------------------------ #
    # Rendering                                                           #
    # ------------------------------------------------------------------ #

    def _render_assistant(self, text: str) -> None:
        """Print the architect's turn, with recommendations highlighted."""
        lines: list[str] = []
        recommendations: list[str] = []
        for line in text.splitlines():
            match = RECOMMENDATION_RE.match(line)
            if match:
                recommendations.append(match.group(1).strip())
                lines.append(f"[bold magenta]{escape(line)}[/bold magenta]")
            else:
                lines.append(escape(line))
        self.console.print(
            Panel("\n".join(lines), title="Product Architect", border_style="green")
        )
        for recommendation in recommendations:
            self.console.print(
                Panel.fit(
                    f"[bold magenta]{escape(recommendation)}[/bold magenta]",
                    title="Recommendation",
                    border_style="magenta",
                )
            )
