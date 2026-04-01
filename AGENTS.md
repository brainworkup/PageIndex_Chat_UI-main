# AGENTS.md

This file provides guidance to WARP (warp.dev) when working with code in this repository.

## Project Overview

PageIndex Chat UI is an Agentic RAG system for PDF document Q&A. It uses **tree-structure reasoning** instead of vector embeddings — PDFs are parsed into hierarchical trees (like a table of contents), and an LLM navigates the tree to locate answers. The system is a Flask + Socket.IO server with a single-page frontend.

## Build & Run Commands

```bash
# Install dependencies (uv recommended)
uv sync

# Run the server (default: http://localhost:5001)
uv run python app.py

# Alternative: use start.sh which handles venv creation + uv sync
./start.sh

# Alternate entry point with banner (reads host/port from config)
uv run python main.py

# Install with pip instead
pip install -r requirements.txt
python app.py
```

Python >= 3.11 required (pinned in `.python-version`).

There are **no tests, no linter config, and no type-checking setup** in this project.

## Configuration

- **`config.json`** (gitignored): Runtime model config saved by the Web UI. Contains API keys, model names, base URLs for text and vision models. Managed by the `ConfigManager` singleton in `config.py`.
- **`.env`** (gitignored): Environment variable fallbacks. `ConfigManager.get_runtime_model_config()` resolves values with a priority chain: `config.json` → env vars (e.g. `TEXT_MODEL_API_KEY`, `PAGEINDEX_API_KEY`, `OPENAI_API_KEY`) → hardcoded defaults.
- **`pageindex/config.yaml`**: Default parameters for the indexing engine (model, TOC detection pages, node splitting thresholds). Overridden at runtime by `IndexingService.index_pdf()`.

## Architecture

### Request Flow

1. **Frontend** (`templates/index.html` + `static/js/app.js`): Single-file SPA. Communicates via REST for CRUD and Socket.IO for streaming chat.
2. **Routes** (`routes/`):
   - `api.py`: REST endpoints for document upload/delete, config, skills, tree structure, analysis, text highlights.
   - `socket_handlers.py`: Socket.IO events — `chat` (legacy simple RAG), `agent_chat` (full agent pipeline), `chat_sync` (non-streaming). Each handler creates its own `asyncio` event loop in a thread.
3. **Services** (`services/`):
   - `rag_service.py`: Core orchestration. `RAGService` wraps `PageIndexService` (LLM/VLM calls, tree search, node mapping, PDF image extraction) and lazily creates a `DocumentAgent`. Exposes `chat_stream()` (legacy) and `agent_chat_stream()` (agent mode).
   - `agent.py`: `DocumentAgent` implements the full agent pipeline: query decomposition → ReAct loop (up to 5 steps) → answer generation → self-reflection (retry if score < 6). All phases yield streaming markers like `[AGENT_STEP]`, `[AGENT_DECOMPOSE]`, `[AGENT_REFLECT]`.
   - `indexing_service.py`: Calls the `pageindex` core engine to build the tree structure from a PDF. Runs in a background thread via `run_in_executor`.
   - `skill_manager.py`: Manages custom agent skills as Markdown files in `skills/`. Skills have YAML frontmatter (name, description, enabled) and are injected into agent prompts.
4. **PageIndex Core** (`pageindex/`): The indexing engine from [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex). `page_index.py` handles TOC detection, page alignment, recursive splitting. `utils.py` provides synchronous (`ChatGPT_API`) and async (`ChatGPT_API_async`) LLM wrappers with retry logic. Uses `openai.OpenAI` (sync) for indexing and `openai.AsyncOpenAI` for Q&A.
5. **Models** (`models/document.py`): `DocumentStore` is a singleton managing documents, chat history, and in-memory caches (tree, node_map, page_images). Persists metadata to `results/<doc_id>/metadata.json` and chat history to `chat_history.json`. Recovers state on startup by scanning the `results/` directory.

### Agent Tool System

Tools live in `services/tools/` and inherit from `BaseTool` (`base.py`). Each tool implements `async execute(params, context) -> dict` returning `{"summary": ..., "nodes": [...]}`. Registered in `DocumentAgent._register_tools()`.

To add a new tool: create a file in `services/tools/`, inherit `BaseTool`, implement `execute()`, and register it in `agent.py`'s `_register_tools()`.

### Streaming Protocol

The agent yields text interleaved with bracketed markers. `socket_handlers.py`'s `_process_chunk()` parses these and emits typed Socket.IO events:
- `[SEARCHING]`, `[ANSWERING]`, `[RETRY_ANSWERING]` → status events
- `[AGENT_STEP]{json}` → agent reasoning step
- `[AGENT_DECOMPOSE]{json}` → query decomposition result
- `[AGENT_REFLECT]{json}` → self-reflection score and issues
- `[NODES]{json}` → referenced node IDs
- `[THINKING_CHUNK]` → streaming tree-search thinking (legacy mode)
- Plain text chunks → streamed answer tokens

### Key Singletons

All instantiated at module level and imported throughout:
- `config_manager` (`config.py`)
- `document_store` (`models/document.py`)
- `rag_service` (`services/rag_service.py`)
- `indexing_service` (`services/indexing_service.py`)
- `skill_manager` (`services/skill_manager.py`)

### Data Layout

- `uploads/`: Raw uploaded PDFs, named `{doc_id}_{filename}`
- `results/{doc_id}_{filename}/`: Per-document output — `metadata.json`, `structure.json` (tree), `analysis.json`, `chat_history.json`, `text_highlights.json`, `images/` (rendered page JPEGs)

## Important Conventions

- All LLM prompts instruct the model to respond in **English** with LaTeX math delimiters. This is enforced by `LANG_INSTRUCTION` in `agent.py`.
- The project uses **no embedding models or vector databases**. All retrieval is LLM-driven tree navigation.
- `async_mode='threading'` for Flask-SocketIO — socket handlers spin up their own asyncio event loops.
- Node IDs follow the format `node_XXXX` (zero-padded).
