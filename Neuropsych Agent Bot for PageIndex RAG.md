# Neuropsych Agent Bot for PageIndex RAG
## Problem
The existing Telegram bot (`telegram_bot.py`) is a generic Q&A wrapper over the PageIndex RAG service. It has no neuropsych domain awareness, no patient/case scoping, and contains several bugs. The goal is to transform it into a **neuropsych-specialized Agent Bot** that leverages the PageIndex tree-search RAG with clinical intelligence informed by the neuro-intake-agent design.
## Current State
**Working:**
* PageIndex RAG pipeline: PDF upload → tree indexing → tree-search retrieval → LLM answer (text + vision modes)
* Flask + SocketIO web UI at `:5001`
* Multi-document RAG support (`chat_stream_multi`)
* Document store with persistence, chat history, tree/node caching
* OpenAI-compatible LLM integration (configurable model/key/base_url)
**Broken:**
* `telegram_bot.py` lines 215–246: `run()` and `_run_polling()` are not indented inside `TelegramBot` class
* `app.py` lines 47–55 and 84–94: duplicate Telegram bot startup blocks
* `start.sh`: sources fish activate inside bash script, then has unreachable setup logic after `wait`
**Missing for neuropsych use:**
* No clinical system prompts or domain-specific answer constraints
* No patient/case-scoped document selection
* No clinical section awareness (referral, history domains, diagnoses, etc.)
* No provenance/citation formatting for clinical use
* No multi-document case management via bot
* No integration with neuro-intake-agent concepts
## Proposed Changes
### 1. Fix existing bugs
* **`telegram_bot.py`**: Fix indentation of `run()` and `_run_polling()` so they are proper methods of `TelegramBot`
* **`app.py`**: Remove duplicate Telegram bot startup block (lines 84–94)
* **`start.sh`**: Fix to use bash-compatible activate and remove unreachable code after `wait`
### 2. Create `neuropsych_agent.py` — the Agent layer
New module that wraps the RAG service with neuropsych domain intelligence.
**Key components:**
* `NeuropsychAgent` class that sits between the Telegram bot and `rag_service`
* **System prompt** with clinical constraints: extract-only, no unsupported diagnoses, provenance required, flag uncertainty, neutral clinical prose
* **Case context manager**: tracks which documents belong to a patient case, maintains case-level conversation state
* **Answer formatter**: adds page citations, document attribution, clinical section tags
* **Domain-aware query routing**: detects query intent (history lookup, score lookup, section summary, cross-doc comparison) and adjusts the answer prompt accordingly
The system prompt will be based on the neuro-intake-agent's operating principles (spec sections 5.1–5.5):
```warp-runnable-command
You are a neuropsychology document assistant. Answer questions based strictly on
the uploaded clinical documents. You must:
1. Only state information supported by the source text
2. Cite the source document and page for key facts
3. Distinguish documented facts from reported history
4. Flag conflicts across documents rather than resolving silently
5. Never infer diagnoses unless explicitly documented
6. Note uncertainty when evidence is ambiguous
```
### 3. Enhance `telegram_bot.py` — neuropsych commands
Add commands for clinical workflows:
* `/case [name]` — create or switch patient case context, group documents
* `/docs` — list documents in current case (replaces generic `/documents`)
* `/sections [doc]` — list detected clinical sections in a document
* `/summary` — generate structured intake summary from case documents
* `/flags` — show quality/uncertainty flags for current case
* `/compare <field>` — cross-document comparison for a specific field (e.g., diagnoses, medications)
* `/cite` — toggle citation mode (inline page references in answers)
Retain existing: `/start`, `/help`, `/status`
### 4. Add `neuropsych_prompts.py` — prompt templates
Centralized prompt templates for:
* Clinical Q&A system prompt
* Section-aware retrieval prompt (enhances tree search with clinical section context)
* Cross-document comparison prompt
* Intake summary generation prompt
* Citation formatting instructions
These are loaded at agent init and injected into the RAG pipeline's answer step.
### 5. Enhance RAG answer generation in `rag_service.py`
Modify `chat_stream()` and `chat_stream_multi()` to accept an optional `system_prompt` parameter. Currently the answer prompt is hardcoded inline. The change is:
* Add `system_prompt: Optional[str] = None` parameter
* If provided, prepend it to the LLM messages as a system message
* The `NeuropsychAgent` passes its clinical system prompt through this parameter
This keeps `rag_service.py` generic while allowing domain-specific prompting.
### 6. Add per-user case state to `config.json`
Extend the config schema to support:
```json
{
  "telegram": {
    "token": "...",
    "cases": {
      "user_12345": {
        "active_case": "case_001",
        "cases": {
          "case_001": {
            "name": "Patient A",
            "doc_ids": ["20260228_140000_a1b2", "20260301_100000_c3d4"]
          }
        }
      }
    }
  }
}
```
This lets the bot remember which documents a user has grouped into a case and which case is active.
### 7. Update `start.sh`
Fix the startup script to work correctly:
* Remove fish-specific activate line
* Remove duplicate/unreachable logic
* Use `uv run` consistently
## File Changes Summary
* **Fix**: `telegram_bot.py` — indentation, enhance with neuropsych commands
* **Fix**: `app.py` — remove duplicate bot startup
* **Fix**: `start.sh` — fix shell script logic
* **New**: `neuropsych_agent.py` — agent layer with clinical prompting and case management
* **New**: `neuropsych_prompts.py` — prompt templates
* **Modify**: `services/rag_service.py` — add optional `system_prompt` parameter to `call_llm_stream` and `chat_stream`/`chat_stream_multi`
* **Modify**: `config.py` — add telegram/case config support
## Architecture After Changes
```warp-runnable-command
Telegram Bot (telegram_bot.py)
       │
       ▼
Neuropsych Agent (neuropsych_agent.py)
  ├── Clinical system prompts (neuropsych_prompts.py)
  ├── Case context manager (config.json state)
  └── Domain-aware query routing
       │
       ▼
RAG Service (rag_service.py) — with system_prompt injection
       │
       ▼
PageIndex Core (tree search → LLM answer)
```
