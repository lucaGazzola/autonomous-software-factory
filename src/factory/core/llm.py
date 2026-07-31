"""Minimal LLM chat-completion client shared by the factory's AI components.

Only two things are required of an LLM backend: send a chat message list and
return the assistant's text (optionally in JSON mode). The generator's
interview/decomposition pipeline and the refactoring scanner both speak this
``LLMClient`` protocol, so tests can inject a scripted fake.
"""

from __future__ import annotations

import os
from typing import Protocol


class LLMError(RuntimeError):
    """Raised when the LLM backend cannot produce a response."""


class LLMClient(Protocol):
    """Minimal chat-completion contract used by factory components."""

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
