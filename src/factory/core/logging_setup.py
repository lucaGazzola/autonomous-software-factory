"""File logging setup for unattended factory processes.

The daemon runs in the background without a terminal, so every cycle,
failure, and block must be traceable afterwards. ``setup_logging`` installs
a rotating ``factory.log`` file handler (default) plus a stderr handler so
interactive ``factory run`` still sees logs.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: Kept so tests can uninstall the handlers they install.
_FACTORY_HANDLERS: list[logging.Handler] = []


def setup_logging(
    log_file: str | Path | None = None, *, level: int = logging.INFO
) -> logging.Logger:
    """Configure the ``factory`` logger with file and console handlers.

    Args:
        log_file: Path of the log file; ``factory.log`` in the current
            directory when omitted, ``None`` disables file logging.
        level: Root log level for the factory logger.

    Returns:
        The configured ``factory`` logger.
    """
    logger = logging.getLogger("factory")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    for handler in _FACTORY_HANDLERS:
        handler.close()
    _FACTORY_HANDLERS.clear()

    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if log_file is not None:
        file_path = Path(log_file)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            file_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        _FACTORY_HANDLERS.append(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    _FACTORY_HANDLERS.append(console_handler)

    return logger
