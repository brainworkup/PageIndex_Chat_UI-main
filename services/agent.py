"""
Document Agent - ReAct loop, query decomposition, self-reflection, proactive analysis

Implements all five agent directions:
  1. ReAct loop (Think → Act → Observe)
  2. Multi-tool agent
  3. Query decomposition
  4. Self-reflection
  5. Proactive document analysis
"""

import json
import logging
import os
from typing import AsyncGenerator, List

from models.document import DocumentStore, Message
from services.tools.base import ToolRegistry
from services.tools.tree_search import TreeSearchTool
from services.tools.node_reader import NodeReaderTool
from services.tools.keyword_search import KeywordSearchTool
from services.tools.page_viewer import PageViewerTool
from services.tools.summarizer import SummarizerTool
from services.skill_manager import skill_manager

logger = logging.getLogger(__name__)

MAX_REACT_STEPS = 5
MAX_RETRY = 1
REFLECT_ACCEPT_THRESHOLD = 6

LANG_INSTRUCTION = (
    "Important: You MUST respond in English. All your output text, reasoning, analysis, and answers should be in English. "
    "When mentioning any mathematical symbol, variable, subscript, superscript, or formula, "
    "you MUST wrap them in LaTeX delimiters: use $...$ for inline math (e.g. $s_j$, $f_{MD}$, $t_{m,i}^{\\mathrm{loc}}$) "
    "and \\\\[...\\\\] for display/block math. NEVER output bare symbols like x_i or s_{j+1} without dollar signs."
)


class DocumentAgent:
    """
    Agentic document Q&A system.

    Instead of a single search-then-answer pipeline, the agent:
    - Decomposes complex questions into sub-questions
    - Uses a ReAct loop to iteratively gather information
    - Chooses from multiple tools each step
    - Self-reflects on answer quality and retries if needed
    - Proactively analyzes documents after indexing
    """

    def __init__(self, pageindex_service, store: DocumentStore):
        self.pageindex = pageindex_service
        self.store = store
        self.registry = ToolRegistry()
        self._register_tools()

    def _register_tools(self):
        self.registry.register(TreeSearchTool(self.pageindex))
        self.registry.register(NodeReaderTool())
        self.registry.register(KeywordSearchTool())
        self.registry.register(PageViewerTool(self.pageindex))
        self.registry.register(SummarizerTool(self.pageindex))

    # ------------------------------------------------------------------ #
    #  Direction 3: Query decomposition
    # ------------------------------------------------------------------ #
    async def decompose_query(self, query: str, tree_summary: str,
                              model_type: str = "text") -> dict:
        skill_section = skill_manager.build_skill_prompt()
        skill_hint = ""
        if skill_section:
            skill_hint = (
                "\n\nYou also have active custom skills. "
                "Consider them when decomposing:\n" + skill_section
            )

        prompt = f"""You are an intelligent document analysis agent.
Analyze the user's question and decide whether it should be broken into simpler sub-questions.

Question: {query}

Document structure overview (titles & summaries only):
{tree_summary[:4000]}

Rules:
- ONLY decompose if the question genuinely asks about MULTIPLE DIFFERENT topics/aspects that require searching DIFFERENT parts of the document.
- Do NOT decompose if the question is about a single topic, even if it seems complex.
- Do NOT decompose extraction tasks (e.g. "extract table X", "list the items in section Y", "what does figure Z show").
- Do NOT decompose lookup tasks (e.g. "what is X?", "find the definition of Y").
- Do NOT decompose if the answer is likely in one section/table/figure.
- When in doubt, do NOT decompose. A single well-targeted search is better than multiple overlapping searches.
- Generate at most 3 sub-questions, only when truly needed.
- If a custom skill is relevant, design sub-questions to match its workflow.
{skill_hint}

{LANG_INSTRUCTION}

Output JSON only:
{{
    "needs_decomposition": true or false,
    "reasoning": "brief reasoning",
    "sub_questions": ["sub-question 1", "sub-question 2"],
    "synthesis_strategy": "compare" | "aggregate" | "sequence" | "direct"
}}"""
        try:
            raw = await self.pageindex.call_llm(prompt, model_type)
            raw = self._extract_json_str(raw)
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Decomposition failed, using original query: {e}")
            return {
                "needs_decomposition": False,
                "reasoning": "fallback",
                "sub_questions": [query],
                "synthesis_strategy": "direct",
            }

    # ------------------------------------------------------------------ #
    #  Direction 1 & 2: ReAct step (think + pick a tool)
    # ------------------------------------------------------------------ #
    async def think_and_act(self, query: str, gathered: List[dict],
                            tree_summary: str,
                            model_type: str = "text") -> dict:
        tool_specs = self.registry.all_specs()
        tools_desc = "\n".join(
            f'{i+1}. {t["name"]}: {t["description"]}  '
            f'Params: {json.dumps(t["parameters"])}'
            for i, t in enumerate(tool_specs)
        )

        context_so_far = ""
        if gathered:
            context_so_far = "Information gathered so far:\n" + "\n".join(
                f"- [{g['tool']}] {g['observation'][:500]}" for g in gathered
            )

        skill_section = skill_manager.build_skill_prompt()

        prompt = f"""You are an intelligent document analysis agent with access to these tools:

{tools_desc}

{len(tool_specs)+1}. final_answer: You have gathered enough information to answer. Params: {{}}

Question: {query}

Document tree (titles & summaries):
{tree_summary[:3000]}

{context_so_far}

Based on the question and what you know so far, decide the next step.
If you already have enough information, choose "final_answer".
{skill_section}

{LANG_INSTRUCTION}

Output JSON only:
{{
    "thought": "describe your reasoning process",
    "action": {{
        "tool": "tool_name",
        "input": {{ ... }}
    }}
}}"""
        try:
            raw = await self.pageindex.call_llm(prompt, model_type)
            raw = self._extract_json_str(raw)
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Think-and-act parse failed: {e}")
            return {
                "thought": "Falling back to tree search",
                "action": {"tool": "tree_search", "input": {"query": query}},
            }

    # ------------------------------------------------------------------ #
    #  Direction 4: Self-reflection
    # ------------------------------------------------------------------ #
    async def reflect(self, query: str, answer: str,
                      context_summary: str,
                      model_type: str = "text",
                      is_vision: bool = False) -> dict:
        vision_note = ""
        if is_vision:
            vision_note = (
                "\nIMPORTANT: The answer was generated using a vision model that "
                "can directly read page images (figures, tables, charts). The text "
                "context below is only a partial summary of what the model could see. "
                "If the answer contains specific data (numbers, table rows) that are "
                "not in the text context, they may have been correctly read from "
                "page images — do NOT treat them as fabricated.\n"
            )

        prompt = f"""Evaluate this answer's quality.

Question: {query}
{vision_note}
Context used (tool observations):
{context_summary[:6000]}

Generated answer:
{answer[:3000]}

Check:
1. Does the answer address the question?
2. Is the answer supported by the context? (For vision mode, the model can see page images directly, so data from images is valid evidence.)
3. Are there factual inconsistencies between the answer and the context?
4. Is important information missing?

{LANG_INSTRUCTION}

Output JSON only:
{{
    "score": <1-10>,
    "issues": ["describe issue 1", ...],
    "missing_info": ["describe missing information"],
    "action": "accept" or "retry"
}}"""
        try:
            raw = await self.pageindex.call_llm(prompt, model_type)
            raw = self._extract_json_str(raw)
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"Reflection parse failed: {e}")
            return {"score": 7, "issues": [], "missing_info": [], "action": "accept"}

    # ------------------------------------------------------------------ #
    #  Direction 5: Proactive document analysis
    # ------------------------------------------------------------------ #
    async def analyze_document(self, doc_id: str,
                               model_type: str = "text") -> dict:
        tree = self.store.get_tree(doc_id)
        if not tree:
            return {}

        tree_summary = json.dumps(
            self.pageindex.remove_fields(tree, ["text"]),
            indent=2, ensure_ascii=False,
        )

        prompt = f"""You are analyzing a document based on its structure.
Provide a comprehensive analysis.

Document structure:
{tree_summary[:6000]}

{LANG_INSTRUCTION}

Output JSON only:
{{
    "summary": "2-3 sentence summary of the document's main content",
    "key_findings": ["key finding 1", "key finding 2", "key finding 3"],
    "main_topics": ["topic 1", "topic 2"],
    "suggested_questions": [
        "suggested question 1",
        "suggested question 2",
        "suggested question 3",
        "suggested question 4",
        "suggested question 5"
    ]
}}"""
        try:
            raw = await self.pageindex.call_llm(prompt, model_type)
            raw = self._extract_json_str(raw)
            analysis = json.loads(raw)
        except Exception as e:
            logger.error(f"Document analysis failed: {e}")
            analysis = {
                "summary": "Analysis could not be generated.",
                "key_findings": [],
                "main_topics": [],
                "suggested_questions": [],
            }

        doc = self.store.get_document(doc_id)
        if doc:
            analysis_path = os.path.join(doc.result_dir, "analysis.json")
            try:
                with open(analysis_path, "w", encoding="utf-8") as f:
                    json.dump(analysis, f, indent=2, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Failed to save analysis: {e}")

        return analysis

    # ------------------------------------------------------------------ #
    #  Main agent loop
    # ------------------------------------------------------------------ #
    async def run(self, doc_id: str, query: str,
                  model_type: str = "text",
                  use_memory: bool = True) -> AsyncGenerator[str, None]:
        """
        Main entry point. Yields streaming markers compatible with
        the socket handler protocol.
        """
        doc = self.store.get_document(doc_id)
        if not doc or doc.status != "ready":
            yield "[Error: Document not ready]"
            return

        tree = self.store.get_tree(doc_id)
        node_map = self.store.get_node_map(doc_id)
        page_images = self.store.get_page_images(doc_id)

        if not tree:
            yield "[Error: Tree structure not loaded]"
            return
        if not node_map:
            yield "[Error: Node mapping not available]"
            return

        tree_summary = json.dumps(
            self.pageindex.remove_fields(tree, ["text"]),
            indent=2, ensure_ascii=False,
        )

        tool_context = {
            "tree": tree,
            "node_map": node_map,
            "page_images": page_images or {},
            "doc_id": doc_id,
            "model_type": model_type,
        }

        # ---- Phase 1: Query decomposition ----
        yield "[SEARCHING]\n"

        decomposition = await self.decompose_query(query, tree_summary, model_type)
        yield f"[AGENT_DECOMPOSE]{json.dumps(decomposition, ensure_ascii=False)}\n"

        sub_questions = (
            decomposition.get("sub_questions", [query])
            if decomposition.get("needs_decomposition")
            else [query]
        )

        # ---- Phase 2: ReAct loop ----
        gathered: List[dict] = []
        all_nodes: List[str] = []

        for sq_idx, sub_q in enumerate(sub_questions):
            for step in range(MAX_REACT_STEPS):
                step_result = await self.think_and_act(
                    sub_q, gathered, tree_summary, model_type
                )

                thought = step_result.get("thought", "")
                action = step_result.get("action", {})
                tool_name = action.get("tool", "final_answer")
                tool_input = action.get("input", {})

                if tool_name == "final_answer":
                    yield self._step_marker(
                        sq_idx, step, thought, "final_answer", {}, "Ready to answer"
                    )
                    break

                tool = self.registry.get(tool_name)
                if tool:
                    try:
                        observation = await tool.execute(tool_input, tool_context)
                    except Exception as e:
                        logger.error(f"Tool {tool_name} error: {e}")
                        observation = {"summary": f"Tool error: {e}", "nodes": []}
                else:
                    observation = {"summary": f"Unknown tool: {tool_name}", "nodes": []}

                obs_nodes = observation.get("nodes", [])
                all_nodes.extend(obs_nodes)

                gathered.append({
                    "question": sub_q,
                    "thought": thought,
                    "tool": tool_name,
                    "input": tool_input,
                    "observation": observation.get("summary", ""),
                })

                yield self._step_marker(
                    sq_idx, step, thought, tool_name,
                    tool_input, observation.get("summary", "")
                )

        unique_nodes = list(dict.fromkeys(all_nodes))
        if unique_nodes:
            yield f"\n[NODES]{json.dumps(unique_nodes)}\n"

        # ---- Phase 3: Generate answer ----
        yield "[ANSWERING]\n"

        answer_context = self._build_answer_context(gathered, node_map)
        history_context = self._build_history_context(doc_id, use_memory)

        is_vision = model_type != "text"

        if is_vision:
            priority_nodes = self._get_priority_nodes(gathered, unique_nodes)
            image_paths = self.pageindex.get_page_images_for_nodes(
                priority_nodes, node_map, page_images or {}
            )
            vision_prompt = self._build_vision_answer_prompt(
                query, sub_questions, history_context,
                decomposition.get("synthesis_strategy", "direct"),
                gathered_context=answer_context,
            )
            if image_paths:
                full_answer = ""
                async for chunk in self.pageindex.call_vlm_stream(
                    vision_prompt, image_paths, model_type
                ):
                    full_answer += chunk
                    yield chunk
            else:
                answer_prompt = self._build_answer_prompt(
                    query, sub_questions, answer_context, history_context,
                    decomposition.get("synthesis_strategy", "direct")
                )
                full_answer = ""
                async for chunk in self.pageindex.call_llm_stream(answer_prompt, model_type):
                    full_answer += chunk
                    yield chunk
        else:
            answer_prompt = self._build_answer_prompt(
                query, sub_questions, answer_context, history_context,
                decomposition.get("synthesis_strategy", "direct")
            )
            full_answer = ""
            async for chunk in self.pageindex.call_llm_stream(answer_prompt, model_type):
                full_answer += chunk
                yield chunk

        # ---- Phase 4: Self-reflection ----
        is_vision = model_type != "text"
        context_summary = "\n".join(
            f"[{g['tool']}] {g['observation'][:600]}" for g in gathered
        )
        reflection = await self.reflect(
            query, full_answer, context_summary, model_type, is_vision
        )
        yield f"\n[AGENT_REFLECT]{json.dumps(reflection, ensure_ascii=False)}\n"

        # Retry if reflection says so (at most once)
        if (reflection.get("action") == "retry"
                and reflection.get("score", 10) < REFLECT_ACCEPT_THRESHOLD):
            yield "[AGENT_RETRY]\n"

            missing = reflection.get("missing_info", [])
            if missing:
                extra_query = "; ".join(missing)
                for step in range(MAX_REACT_STEPS):
                    step_result = await self.think_and_act(
                        extra_query, gathered, tree_summary, model_type
                    )
                    thought = step_result.get("thought", "")
                    action = step_result.get("action", {})
                    tool_name = action.get("tool", "final_answer")
                    tool_input = action.get("input", {})

                    if tool_name == "final_answer":
                        break

                    tool = self.registry.get(tool_name)
                    if tool:
                        try:
                            observation = await tool.execute(tool_input, tool_context)
                        except Exception as e:
                            observation = {"summary": f"Error: {e}", "nodes": []}
                    else:
                        observation = {"summary": "Unknown tool", "nodes": []}

                    all_nodes.extend(observation.get("nodes", []))
                    gathered.append({
                        "question": extra_query,
                        "thought": thought,
                        "tool": tool_name,
                        "input": tool_input,
                        "observation": observation.get("summary", ""),
                    })
                    yield self._step_marker(
                        len(sub_questions), step, thought, tool_name,
                        tool_input, observation.get("summary", "")
                    )

                answer_context = self._build_answer_context(gathered, node_map)
                unique_nodes = list(dict.fromkeys(all_nodes))
                if unique_nodes:
                    yield f"\n[NODES]{json.dumps(unique_nodes)}\n"

                # Signal frontend to REPLACE previous answer, not append
                yield "[RETRY_ANSWERING]\n"

                if is_vision:
                    retry_priority = self._get_priority_nodes(gathered, unique_nodes)
                    retry_image_paths = self.pageindex.get_page_images_for_nodes(
                        retry_priority, node_map, page_images or {}
                    )
                    retry_vision_prompt = self._build_vision_answer_prompt(
                        query, sub_questions, history_context,
                        decomposition.get("synthesis_strategy", "direct"),
                        gathered_context=answer_context,
                    )
                    if retry_image_paths:
                        full_answer = ""
                        async for chunk in self.pageindex.call_vlm_stream(
                            retry_vision_prompt, retry_image_paths, model_type
                        ):
                            full_answer += chunk
                            yield chunk
                    else:
                        retry_prompt = self._build_answer_prompt(
                            query, sub_questions, answer_context, history_context,
                            decomposition.get("synthesis_strategy", "direct")
                        )
                        full_answer = ""
                        async for chunk in self.pageindex.call_llm_stream(retry_prompt, model_type):
                            full_answer += chunk
                            yield chunk
                else:
                    retry_prompt = self._build_answer_prompt(
                        query, sub_questions, answer_context, history_context,
                        decomposition.get("synthesis_strategy", "direct")
                    )
                    full_answer = ""
                    async for chunk in self.pageindex.call_llm_stream(retry_prompt, model_type):
                        full_answer += chunk
                        yield chunk

        # ---- Save to history ----
        thinking_summary = "\n".join(
            f"Step {i+1} [{g['tool']}]: {g['thought']}"
            for i, g in enumerate(gathered)
        )
        self.store.add_message(doc_id, Message(role="user", content=query))
        self.store.add_message(doc_id, Message(
            role="assistant",
            content=full_answer,
            nodes=list(dict.fromkeys(all_nodes)),
            thinking=thinking_summary,
        ))

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #
    def _step_marker(self, sq_idx, step, thought, tool, tool_input, observation):
        data = {
            "sub_question_idx": sq_idx,
            "step": step + 1,
            "thought": thought,
            "tool": tool,
            "tool_input": tool_input,
            "observation": observation[:500],
        }
        return f"[AGENT_STEP]{json.dumps(data, ensure_ascii=False)}\n"

    def _build_answer_context(self, gathered: list, node_map: dict) -> str:
        """Build answer context with strict priority ordering.

        Nodes processed by summarize_nodes are not re-included as raw text —
        the summarized output supersedes the raw content.
        """
        import re

        ANALYTICAL_TOOLS = {"summarize_nodes"}

        # --- Pass 1: collect analytical outputs and track which nodes they cover ---
        analytically_processed_nodes = set()
        analytical_outputs = []
        seen_obs_keys = set()

        for g in gathered:
            tool = g["tool"]
            if tool not in ANALYTICAL_TOOLS:
                continue
            obs = g.get("observation", "")
            if not obs:
                continue
            dedup_key = f"{tool}:{obs[:300]}"
            if dedup_key in seen_obs_keys:
                continue
            seen_obs_keys.add(dedup_key)
            analytical_outputs.append(f"[{tool}] {obs}")
            for nid in g.get("input", {}).get("node_ids", []):
                analytically_processed_nodes.add(nid)
            single = g.get("input", {}).get("node_id", "")
            if single:
                analytically_processed_nodes.add(single)

        # --- Pass 2: collect raw node texts (skip analytically processed nodes) ---
        seen_nodes = set()
        raw_node_texts = []
        visual_observations = []

        def _add_node_text(nid):
            if nid in seen_nodes or nid not in node_map:
                return
            if nid in analytically_processed_nodes:
                return
            info = node_map[nid]
            node = info.get("node", info)
            text = node.get("text", "") if isinstance(node, dict) else ""
            if text:
                raw_node_texts.append(text)
                seen_nodes.add(nid)

        for g in gathered:
            tool = g["tool"]

            if tool == "read_node":
                single = g["input"].get("node_id", "")
                batch = g["input"].get("node_ids", [])
                for nid in ([single] if single else []) + (batch or []):
                    _add_node_text(nid)

            elif tool == "tree_search":
                obs = g.get("observation", "")
                for nid in re.findall(r"(node_\S+)", obs):
                    _add_node_text(nid)

            elif tool == "view_pages":
                obs = g.get("observation", "")
                if obs and "Visual analysis" in obs:
                    visual_observations.append(obs)

            elif tool == "keyword_search":
                obs = g.get("observation", "")
                if obs:
                    raw_node_texts.append(f"[{tool}] {obs}")

        if not raw_node_texts and not analytical_outputs and not visual_observations:
            for nid in node_map:
                if nid not in analytically_processed_nodes:
                    _add_node_text(nid)
                if len(raw_node_texts) >= 3:
                    break

        # --- Assemble: analytical first, raw as supplement ---
        parts = []
        if analytical_outputs:
            parts.append(
                "[Tool Analysis Results — processed by AI, use as primary reference]\n"
                + "\n\n".join(analytical_outputs)
            )
        if raw_node_texts:
            raw_combined = "\n\n".join(raw_node_texts)
            budget = 4000 if analytical_outputs else 12000
            if len(raw_combined) > budget:
                raw_combined = raw_combined[:budget] + "\n...(truncated)"
            parts.append("[Source Text Supplement]\n" + raw_combined)
        if visual_observations:
            parts.append("[Visual Analysis]\n" + "\n\n".join(visual_observations))
        return "\n\n".join(parts)

    @staticmethod
    def _get_priority_nodes(gathered: list, all_unique_nodes: list) -> list:
        """Return nodes most relevant for image retrieval.

        Strategy: take nodes from the LAST view_pages or tree_search call
        (which reflects the agent's most refined understanding of what's
        relevant) and fall back to all unique nodes.
        """
        import re
        last_visual_nodes = []
        last_search_nodes = []

        for g in reversed(gathered):
            if g["tool"] == "view_pages" and not last_visual_nodes:
                ids = g["input"].get("node_ids", [])
                if ids:
                    last_visual_nodes = ids
            if g["tool"] == "tree_search" and not last_search_nodes:
                obs = g.get("observation", "")
                found = re.findall(r"(node_\S+)", obs)
                if found:
                    last_search_nodes = found
            if last_visual_nodes and last_search_nodes:
                break

        priority = last_visual_nodes or last_search_nodes
        if priority:
            seen = set()
            result = []
            for nid in priority:
                if nid not in seen:
                    result.append(nid)
                    seen.add(nid)
            return result

        return all_unique_nodes

    def _build_history_context(self, doc_id: str, use_memory: bool) -> str:
        if not use_memory:
            return ""
        history = self.store.get_chat_history(doc_id)
        if not history:
            return ""
        ctx = "\nPrevious conversation:\n"
        for msg in history[-5:]:
            ctx += f"{msg.role}: {msg.content[:200]}\n"
        return ctx

    def _build_vision_answer_prompt(self, query, sub_questions,
                                    history_context, strategy,
                                    gathered_context: str = ""):
        sub_q_note = ""
        if len(sub_questions) > 1:
            sub_q_note = (
                f"\nThe question was decomposed into sub-questions: "
                f"{json.dumps(sub_questions, ensure_ascii=False)}\n"
                f"Synthesis strategy: {strategy}\n"
            )

        context_section = ""
        if gathered_context:
            context_section = (
                f"\nAnalysis results from the reasoning process "
                f"(IMPORTANT — use these findings as primary reference, "
                f"they reflect what was actually discovered in the document):\n"
                f"{gathered_context[:10000]}\n"
            )

        skill_section = skill_manager.build_skill_prompt()
        skill_note = ""
        if skill_section:
            skill_note = (
                "\n\nFollow the output format and workflow of any matching "
                "custom skill below:\n" + skill_section
            )

        return f"""Answer the question based on the images AND the analysis context below.
The analysis context contains findings from previous reasoning steps — treat it as authoritative.
If the analysis context contradicts your initial impression of the images, trust the analysis context.
Examine the images carefully for visual details including figures, tables, diagrams, colors, and layouts.

Question: {query}
{sub_q_note}
{context_section}
{history_context}
{skill_note}

{LANG_INSTRUCTION}
Provide a clear, comprehensive answer in English.
Base your answer primarily on the analysis context, supplemented by what you can see in the images.
If sub-questions were used, synthesize a unified answer.
Use Markdown formatting for better readability."""

    def _build_answer_prompt(self, query, sub_questions, context,
                             history_context, strategy):
        sub_q_note = ""
        if len(sub_questions) > 1:
            sub_q_note = (
                f"\nThe question was decomposed into sub-questions: "
                f"{json.dumps(sub_questions, ensure_ascii=False)}\n"
                f"Synthesis strategy: {strategy}\n"
            )

        skill_section = skill_manager.build_skill_prompt()
        skill_note = ""
        if skill_section:
            skill_note = (
                "\n\nFollow the output format and workflow of any matching "
                "custom skill below:\n" + skill_section
            )

        return f"""Answer the question based on the context below.
The context contains tool analysis results (processed by AI, highly reliable) and may
also contain raw source text. You MUST use the information available in the context to
answer the question. If tool analysis results are present, base your answer primarily
on those results — they have already been extracted and verified from the document.

Question: {query}
{sub_q_note}
Context:
{context[:12000]}
{history_context}
{skill_note}

{LANG_INSTRUCTION}
Provide a clear, comprehensive answer in English. Reference specific sections if helpful.
If sub-questions were used, synthesize a unified answer.
Use Markdown formatting for better readability."""

    @staticmethod
    def _extract_json_str(text: str) -> str:
        text = text.strip()
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.rfind("```")
            if end > start:
                return text[start:end].strip()
        if "```" in text:
            start = text.find("```") + 3
            end = text.rfind("```")
            if end > start:
                return text[start:end].strip()
        brace_start = text.find("{")
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        return text[brace_start:i+1]
        return text
