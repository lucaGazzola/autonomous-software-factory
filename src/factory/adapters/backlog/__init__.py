"""Backlog adapters: task sources the factory pulls work from.

A backlog adapter is the bridge between the factory and any system that
stores and tracks tasks (local JSON file, GitHub Issues, Jira, ...).
"""

from factory.adapters.backlog.base import BaseBacklogAdapter
from factory.adapters.backlog.json_backlog import JSONBacklogAdapter

__all__ = ["BaseBacklogAdapter", "JSONBacklogAdapter"]
