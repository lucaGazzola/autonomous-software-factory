"""Webhook feedback provider (delivery placeholder).

Posts structured JSON payloads to a configured webhook URL so external
chat/alerting systems (Slack, Mattermost, custom dashboards) can surface
factory events. The POST itself uses the standard library, so no extra
dependencies are required. When no URL is configured, payloads are only
logged — a safe default for development.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from factory.adapters.feedback.base import BaseFeedbackProvider
from factory.core.models import UserResponse

logger = logging.getLogger(__name__)


class WebhookFeedbackProvider(BaseFeedbackProvider):
    """Delivers notifications (and optionally input requests) via HTTP POST.

    Note: ``request_input`` cannot be answered through a one-way webhook, so
    this provider defaults to ABORT unless ``default_action`` is overridden.
    Pair it with a callback endpoint to enable true remote HITL.
    """

    name = "webhook"

    def __init__(self, url: str | None = None, default_action: UserResponse | None = None) -> None:
        """Create the provider.

        Args:
            url: Webhook endpoint; when ``None`` payloads are only logged.
            default_action: Fallback response used if ``request_input`` is
                ever invoked; defaults to ABORT.
        """
        self.url = url
        self.default_action = default_action or UserResponse(
            task_id="*", action="abort", message="webhook provider has no input channel"
        )

    async def request_input(self, task_id: str, prompt: str) -> UserResponse:
        """Log the prompt and return the configured default action."""
        logger.warning(
            "WebhookFeedbackProvider has no interactive input channel; "
            "task %s asked: %s (defaulting to %s)",
            task_id,
            prompt,
            self.default_action.action.value,
        )
        return self.default_action.model_copy(update={"task_id": task_id})

    async def notify(self, task_id: str, message: str) -> None:
        """POST a notification payload to the webhook, or log it locally."""
        payload: dict[str, Any] = {
            "event": "factory.notification",
            "task_id": task_id,
            "message": message,
        }
        if not self.url:
            logger.info("webhook notification (no URL configured): %s", payload)
            return
        try:
            await asyncio.to_thread(self._post, payload)
        except (urllib.error.URLError, OSError) as exc:
            logger.error("webhook delivery failed for task %s: %s", task_id, exc)

    def _post(self, payload: dict[str, Any]) -> None:
        """Synchronous HTTP POST, run in a worker thread."""
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 300:
                raise urllib.error.URLError(f"webhook returned HTTP {response.status}")
