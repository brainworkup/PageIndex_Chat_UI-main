"""
Base tool interface and registry for the Agent tool system
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseTool(ABC):
    """Abstract base class for all agent tools"""

    name: str = ""
    description: str = ""
    parameters_schema: Dict[str, Any] = {}

    def get_spec(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters_schema,
        }

    @abstractmethod
    async def execute(self, params: dict, context: dict) -> dict:
        """
        Execute the tool.

        Args:
            params: Tool-specific parameters (from LLM action output)
            context: Shared context containing tree, node_map, page_images, etc.

        Returns:
            dict with at least a 'summary' key for the agent to consume,
            and optionally 'nodes', 'content', 'pages', etc.
        """
        raise NotImplementedError


class ToolRegistry:
    """Registry that manages all available tools"""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all_specs(self) -> List[dict]:
        return [t.get_spec() for t in self._tools.values()]

    def all_names(self) -> List[str]:
        return list(self._tools.keys())
