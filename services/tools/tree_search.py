"""
Tree search tool - searches the document tree structure to find relevant sections
"""

import json
import logging
from .base import BaseTool

logger = logging.getLogger(__name__)


class TreeSearchTool(BaseTool):
    name = "tree_search"
    description = (
        "Search the document's hierarchical tree structure to find sections "
        "relevant to a query. Returns matching node IDs with titles and summaries."
    )
    parameters_schema = {
        "query": {
            "type": "string",
            "description": "The search query to find relevant document sections",
        }
    }

    def __init__(self, pageindex_service):
        self.pageindex = pageindex_service

    async def execute(self, params: dict, context: dict) -> dict:
        query = params.get("query", "")
        tree = context.get("tree")
        model_type = context.get("model_type", "text")

        if not tree or not query:
            return {"summary": "No tree or query provided", "nodes": []}

        result = await self.pageindex.tree_search(query, tree, model_type)

        node_list = result.get("node_list", [])
        thinking = result.get("thinking", "")

        node_map = context.get("node_map", {})
        node_details = []
        for nid in node_list:
            info = node_map.get(nid, {})
            node = info.get("node", info)
            if isinstance(node, dict):
                node_details.append(
                    f"- {nid}: {node.get('title', 'N/A')} "
                    f"(summary: {node.get('summary', 'N/A')[:100]})"
                )

        summary = (
            f"Found {len(node_list)} relevant nodes: {', '.join(node_list)}.\n"
            + "\n".join(node_details)
        )

        return {
            "summary": summary,
            "nodes": node_list,
            "thinking": thinking,
            "node_details": node_details,
        }
