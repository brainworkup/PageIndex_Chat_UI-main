"""
Page viewer tool - reads page images via VLM for visual content understanding.

In text mode: returns textual content of the nodes (delegates to node text).
In vision mode: sends page images to VLM and returns the visual description,
enabling the Agent to "see" figures, tables, colors, layouts during ReAct.
"""

import logging
from .base import BaseTool

logger = logging.getLogger(__name__)


class PageViewerTool(BaseTool):
    name = "view_pages"
    description = (
        "Visually read page images corresponding to document nodes. "
        "In vision mode, this sends page images to the vision model so you can "
        "understand figures, tables, charts, colors, and visual layouts. "
        "Provide node_ids and an optional focus query describing what to look for."
    )
    parameters_schema = {
        "node_ids": {
            "type": "array",
            "description": "List of node IDs whose pages you want to visually inspect.",
        },
        "focus": {
            "type": "string",
            "description": "Optional: what to focus on when reading the pages (e.g. 'describe the chart', 'what color is the logo').",
        },
    }

    def __init__(self, pageindex_service=None):
        self.pageindex = pageindex_service

    async def execute(self, params: dict, context: dict) -> dict:
        node_ids = params.get("node_ids", [])
        focus = params.get("focus", "")
        node_map = context.get("node_map", {})
        page_images = context.get("page_images", {})
        model_type = context.get("model_type", "text")

        if not node_ids:
            return {"summary": "No node IDs provided", "nodes": [], "pages": []}

        all_pages = set()
        image_paths = []
        seen_pages = set()
        node_titles = []

        for nid in node_ids:
            info = node_map.get(nid, {})
            start = info.get("start_index", 0)
            end = info.get("end_index", start)
            node_obj = info.get("node", info)
            title = node_obj.get("title", nid) if isinstance(node_obj, dict) else nid
            node_titles.append(f"{nid} ({title})")

            if start and end:
                for p in range(start, end + 1):
                    all_pages.add(p)
                    if p not in seen_pages and p in (page_images or {}):
                        image_paths.append(page_images[p])
                        seen_pages.add(p)

        is_vision = model_type != "text"

        if is_vision and image_paths and self.pageindex:
            focus_instruction = ""
            if focus:
                focus_instruction = f"\nFocus on: {focus}"

            prompt = (
                f"You are examining pages from a document. "
                f"Describe the visual content you see in detail, including any "
                f"figures, tables, charts, diagrams, images, colors, and layouts.{focus_instruction}\n\n"
                f"Nodes being examined: {', '.join(node_titles)}\n"
                f"Pages: {sorted(all_pages)}\n\n"
                f"Provide a thorough description in Chinese (简体中文)."
            )

            try:
                vlm_response = await self.pageindex.call_vlm(
                    prompt, image_paths, model_type
                )
                summary = (
                    f"Visual analysis of {len(node_ids)} nodes "
                    f"({len(image_paths)} page images):\n{vlm_response}"
                )
                return {
                    "summary": summary,
                    "pages": sorted(all_pages),
                    "nodes": node_ids,
                    "visual_content": vlm_response,
                }
            except Exception as e:
                logger.error(f"VLM call failed in PageViewerTool: {e}")
                return {
                    "summary": f"Visual analysis failed: {e}. Falling back to text.",
                    "pages": sorted(all_pages),
                    "nodes": node_ids,
                }

        texts = []
        for nid in node_ids:
            info = node_map.get(nid, {})
            node_obj = info.get("node", info)
            text = node_obj.get("text", "") if isinstance(node_obj, dict) else ""
            if text:
                texts.append(text[:500])

        summary = (
            f"Page info for {len(node_ids)} nodes "
            f"(pages {sorted(all_pages)}, {len(image_paths)} images available):\n"
            + "\n".join(texts[:5])
        )

        return {
            "summary": summary,
            "pages": sorted(all_pages),
            "nodes": node_ids,
        }
