"""Agent adapters: the pluggable engines that actually do the work.

A factory is agent-agnostic: any adapter conforming to ``BaseAgentAdapter``
can execute tasks, whether it wraps Claude Code, Aider, AutoGen, a custom
script, or a simulated agent.
"""

from factory.adapters.agents.base import BaseAgentAdapter
from factory.adapters.agents.mock_agent import MockAgentAdapter
from factory.adapters.agents.shell_agent import ShellAgentAdapter

__all__ = ["BaseAgentAdapter", "MockAgentAdapter", "ShellAgentAdapter"]
