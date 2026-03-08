"""
Summarizer tool - generates LLM-powered summaries of document sections
"""

import logging
from .base import BaseTool

logger = logging.getLogger(__name__)


class SummarizerTool(BaseTool):
    name = "summarize_nodes"
    description = (
        "Generate a concise summary of one or more document nodes' content. "
        "Useful when you have too much text and need a quick overview."
    )
    parameters_schema = {
        "node_ids": {
            "type": "array",
            "description": "List of node IDs to summarize",
        }
    }

    def __init__(self, pageindex_service):
        self.pageindex = pageindex_service

    async def execute(self, params: dict, context: dict) -> dict:
        node_ids = params.get("node_ids", [])
        node_map = context.get("node_map", {})
        model_type = context.get("model_type", "text")

        if not node_ids:
            return {"summary": "No node IDs provided", "nodes": []}

        texts = []
        for nid in node_ids:
            info = node_map.get(nid, {})
            node = info.get("node", info)
            title = node.get("title", nid) if isinstance(node, dict) else nid
            text = node.get("text", "") if isinstance(node, dict) else ""
            if text:
                texts.append(f"[{title}]\n{text[:3000]}")

        if not texts:
            return {
                "summary": "No text content found in the specified nodes",
                "nodes": node_ids,
            }

        combined = "\n\n---\n\n".join(texts)
        prompt = (
            f"Summarize the following document sections concisely "
            f"(max 300 words):\n\n{combined}"
        )

        try:
            result = await self.pageindex.call_llm(prompt, model_type)
            return {
                "summary": result,
                "nodes": node_ids,
            }
        except Exception as e:
            logger.error(f"Summarizer error: {e}")
            return {
                "summary": f"Error generating summary: {e}",
                "nodes": node_ids,
            }
