"""Feedback providers: the Human-in-the-Loop (HITL) channel.

When an agent blocks or fails, the factory asks a human operator through
one of these providers (interactive terminal, webhook, ...) and uses the
response to decide whether to retry, apply guidance, or abort.
"""

from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.adapters.feedback.console_feedback import ConsoleFeedbackProvider
from factory.adapters.feedback.deferred_feedback import DeferredFeedbackProvider
from factory.adapters.feedback.webhook_feedback import WebhookFeedbackProvider

__all__ = [
    "BaseFeedbackProvider",
    "ConsoleFeedbackProvider",
    "DeferredFeedbackProvider",
    "WebhookFeedbackProvider",
]
