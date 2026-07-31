"""Interactive Backlog Generator: interview a product idea into an executable backlog.

Pipeline: ``InterviewSession`` grills the user via an LLM Product Architect,
``TaskDecomposer`` converts the finalized specification into validated,
topologically ordered ``Task`` objects, and the CLI writes them through the
configured ``BacklogAdapter`` (``factory generate-backlog``).
"""

from factory.generator.decomposer import DecompositionError, TaskDecomposer, extract_json
from factory.generator.interview import (
    CRITICAL_TOPICS,
    EXIT_PHRASES,
    InterviewSession,
    LiteLLMClient,
    LLMClient,
    LLMError,
)
from factory.generator.prompts import DECOMPOSITION_SYSTEM_PROMPT, GRILLING_SYSTEM_PROMPT

__all__ = [
    "CRITICAL_TOPICS",
    "DECOMPOSITION_SYSTEM_PROMPT",
    "EXIT_PHRASES",
    "GRILLING_SYSTEM_PROMPT",
    "DecompositionError",
    "InterviewSession",
    "LLMClient",
    "LLMError",
    "LiteLLMClient",
    "TaskDecomposer",
    "extract_json",
]
