"""Optional Telegram notifications for blocked runs.

The feature is disabled unless both ``telegram_bot_token`` and
``telegram_chat_id`` are set in Forgeo config. Uses only the standard
library and never raises: a failing notification is logged as a warning and
the outcome of Forgeo cycle is left unchanged.
"""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass

from forgeo.models import ForgeoConfig

logger = logging.getLogger(__name__)

SEND_MESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT = 5.0
REASON_LINES = 8


@dataclass
class BlockedNotice:
    """The payload of one blocked-run notification."""

    task_id: str
    task_title: str
    reason: str


def blocked_notice_text(forgeo_name: str, notice: BlockedNotice) -> str:
    """Compose the message body: forgeo name, task id/title, and the reason."""
    lines = [
        f"\u26d4 {forgeo_name} is blocked",
        f"Task {notice.task_id}: {notice.task_title}",
        "",
        *notice.reason.splitlines()[:REASON_LINES],
    ]
    return "\n".join(lines)


def send_blocked_notice(config: ForgeoConfig, notice: BlockedNotice) -> bool:
    """Send one ``sendMessage`` request; returns True when delivered.

    Returns ``False`` without a warning when the feature is not configured
    (no notification is expected). Returns ``False`` and logs a warning when
    Telegram rejects or is unreachable — a notification failure never changes
    the outcome of Forgeo cycle.
    """
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return False
    payload = {
        "chat_id": config.telegram_chat_id,
        "text": blocked_notice_text(config.name, notice),
    }
    url = SEND_MESSAGE_URL.format(token=config.telegram_bot_token)
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(payload).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                logger.warning(
                    "Telegram notification failed: HTTP %s from %s.",
                    response.status,
                    url,
                )
                return False
    except (OSError, ValueError) as exc:
        logger.warning("Telegram notification failed: %s", exc)
        return False
    logger.info("Telegram notification sent for blocked run of task %s.", notice.task_id)
    return True
