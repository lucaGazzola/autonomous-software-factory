"""Project configuration loading: YAML <-> :class:`ProjectConfig`.

A project file describes one repository the factory automates: where the
repo lives, how often the daemon wakes up, where the backlog is, and how
git delivery should behave. Relative paths in the file are resolved
against the file's own directory, so a project file can be kept anywhere
(e.g. ``config/project.yaml``) and still refer to sibling directories.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from factory.core.models import ProjectConfig


def load_project_config(path: str | Path, *, resolve_paths: bool = True) -> ProjectConfig:
    """Load and validate a project YAML file.

    Args:
        path: Location of the ``project.yaml`` (or equivalent) file.
        resolve_paths: Resolve ``repo_path`` and ``log_file`` relative to the
            config file's directory (the backlog path is always resolved
            against the repository path through ``ProjectConfig.backlog_path``).

    Returns:
        The validated :class:`ProjectConfig`.

    Raises:
        FileNotFoundError: If the file does not exist.
        pydantic.ValidationError: If the payload does not match the schema.
    """
    config_path = Path(path)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    project = ProjectConfig.model_validate(payload)
    if resolve_paths:
        base = config_path.parent.resolve()
        if not project.repo_path.is_absolute():
            project = project.model_copy(update={"repo_path": base / project.repo_path})
        if project.log_file and not Path(project.log_file).is_absolute():
            project = project.model_copy(update={"log_file": str(base / project.log_file)})
    return project


def save_project_config(path: str | Path, project: ProjectConfig) -> Path:
    """Serialize a :class:`ProjectConfig` to a YAML project file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(project.model_dump(mode="json"), sort_keys=False), encoding="utf-8"
    )
    return target
