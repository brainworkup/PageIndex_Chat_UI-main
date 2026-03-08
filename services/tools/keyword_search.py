"""
Keyword search tool - searches for exact keywords across all document nodes
"""

import logging
from .base import BaseTool

logger = logging.getLogger(__name__)


class KeywordSearchTool(BaseTool):
    name = "keyword_search"
    description = (
        "Search for an exact keyword or phrase across all document nodes. "
        "Useful for finding specific terms, numbers, names, or technical concepts."
    )
    parameters_schema = {
        "keyword": {
            "type": "string",
            "description": "The exact keyword or phrase to search for",
        }
    }

    async def execute(self, params: dict, context: dict) -> dict:
        keyword = params.get("keyword", "").lower()
        node_map = context.get("node_map", {})

        if not keyword:
            return {"summary": "No keyword provided", "nodes": []}

        matches = []
        matched_nodes = []

        for nid, info in node_map.items():
            node = info.get("node", info)
            text = node.get("text", "") if isinstance(node, dict) else ""
            title = node.get("title", "") if isinstance(node, dict) else ""

            if keyword in text.lower() or keyword in title.lower():
                pos = text.lower().find(keyword)
                if pos >= 0:
                    start = max(0, pos - 60)
                    end = min(len(text), pos + len(keyword) + 60)
                    snippet = "..." + text[start:end] + "..."
                else:
                    snippet = f"(found in title: {title})"

                matches.append(f"- {nid} ({title}): {snippet}")
                matched_nodes.append(nid)

        if not matches:
            return {
                "summary": f"Keyword '{keyword}' not found in any document node",
                "nodes": [],
            }

        summary = (
            f"Found '{keyword}' in {len(matches)} nodes:\n" + "\n".join(matches[:10])
        )
        return {
            "summary": summary,
            "nodes": matched_nodes,
            "match_count": len(matches),
        }
