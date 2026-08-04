"""Project configuration loading: YAML <-> :class:`FactoryConfig`.

Relative paths in the file are resolved against the file's own directory,
so a config file can live anywhere and still point at sibling directories.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from factory.models import FactoryConfig


def load_config(path: str | Path) -> FactoryConfig:
    """Load and validate a factory YAML file.

    Raises:
        FileNotFoundError: If the file does not exist.
        pydantic.ValidationError: If the payload does not match the schema.
    """
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config = FactoryConfig.model_validate(payload)
    base = config_path.parent.resolve()
    updates: dict[str, Path | str] = {}
    if not config.repo.is_absolute():
        updates["repo"] = base / config.repo
    if not config.backlog.is_absolute():
        updates["backlog"] = base / config.backlog
    if not config.blocker_file.is_absolute():
        updates["blocker_file"] = base / config.blocker_file
    if not Path(config.log_file).is_absolute():
        updates["log_file"] = str(base / config.log_file)
    return config if not updates else config.model_copy(update=updates)
