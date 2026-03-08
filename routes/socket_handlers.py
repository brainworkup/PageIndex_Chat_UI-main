#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Socket.IO event handlers for streaming chat (legacy + agent mode)
"""

import json
import logging
import asyncio
from flask_socketio import emit
from flask import request

from models.document import document_store, Message
from services.rag_service import rag_service
from config import config_manager

logger = logging.getLogger(__name__)

_cancel_flags: dict[str, bool] = {}


def _is_cancelled(sid: str) -> bool:
    return _cancel_flags.get(sid, False)


def _clear_cancel(sid: str):
    _cancel_flags.pop(sid, None)


def _process_chunk(chunk):
    """Parse a streaming chunk and emit the appropriate socket event.
    Returns True if the chunk was a special marker, False if plain text."""
    c = chunk.strip()

    if c.startswith('[SEARCHING]'):
        emit('status', {'status': 'searching'})
    elif c.startswith('[PREPARING]'):
        emit('status', {'status': 'preparing'})
    elif c.startswith('[PREPARED]'):
        emit('status', {'status': 'prepared'})
    elif c.startswith('[THINKING_CHUNK]'):
        emit('thinking_chunk', {'content': c.replace('[THINKING_CHUNK]', '')})
    elif c.startswith('[THINKING]'):
        emit('thinking', {'content': c.replace('[THINKING]', '').strip()})
    elif c.startswith('[NODES]'):
        nodes_str = c.replace('[NODES]', '').strip()
        try:
            nodes = json.loads(nodes_str)
            emit('nodes', {'nodes': nodes})
        except Exception:
            pass
    elif c.startswith('[ANSWERING]'):
        emit('status', {'status': 'answering'})
    elif c.startswith('[AGENT_STEP]'):
        payload = c.replace('[AGENT_STEP]', '').strip()
        try:
            emit('agent_step', json.loads(payload))
        except Exception:
            emit('agent_step', {'raw': payload})
    elif c.startswith('[AGENT_DECOMPOSE]'):
        payload = c.replace('[AGENT_DECOMPOSE]', '').strip()
        try:
            emit('agent_decompose', json.loads(payload))
        except Exception:
            emit('agent_decompose', {'raw': payload})
    elif c.startswith('[AGENT_REFLECT]'):
        payload = c.replace('[AGENT_REFLECT]', '').strip()
        try:
            emit('agent_reflect', json.loads(payload))
        except Exception:
            emit('agent_reflect', {'raw': payload})
    elif c.startswith('[AGENT_RETRY]'):
        emit('status', {'status': 'retrying'})
    elif c.startswith('[RETRY_ANSWERING]'):
        emit('status', {'status': 'retry_answering'})
    elif c.startswith('[Error'):
        emit('error', {'message': c})
    else:
        return False
    return True


def register_socket_events(socketio):
    """Register Socket.IO event handlers"""

    @socketio.on('connect')
    def handle_connect():
        logger.info(f"Client connected: {request.sid}")
        emit('connected', {'status': 'connected'})

    @socketio.on('disconnect')
    def handle_disconnect():
        logger.info(f"Client disconnected: {request.sid}")
        _clear_cancel(request.sid)

    # ------------------------------------------------------------------ #
    #  Stop generation
    # ------------------------------------------------------------------ #
    @socketio.on('stop_generating')
    def handle_stop_generating():
        sid = request.sid
        logger.info(f"Stop requested by {sid}")
        _cancel_flags[sid] = True

    # ------------------------------------------------------------------ #
    #  Legacy chat (simple RAG, non-agent)
    # ------------------------------------------------------------------ #
    @socketio.on('chat')
    def handle_chat(data):
        """Handle chat message with streaming response (legacy mode)"""
        doc_id = data.get('doc_id')
        query = data.get('query')
        model_type = data.get('model_type', 'text')
        use_memory = data.get('use_memory', True)

        if not doc_id or not query:
            emit('error', {'message': 'Missing doc_id or query'})
            return

        doc = document_store.get_document(doc_id)
        if not doc:
            emit('error', {'message': 'Document not found'})
            return
        if doc.status != 'ready':
            emit('error', {'message': f'Document not ready: {doc.status}'})
            return

        sid = request.sid
        _clear_cancel(sid)
        logger.info(f"Chat request - doc: {doc_id}, query: {query[:50]}..., model: {model_type}")

        async def stream_response():
            stopped = False
            try:
                async for chunk in rag_service.chat_stream(doc_id, query, model_type, use_memory):
                    if _is_cancelled(sid):
                        stopped = True
                        break
                    if not _process_chunk(chunk):
                        emit('chunk', {'content': chunk})
                if stopped:
                    emit('stopped', {'status': 'stopped'})
                else:
                    emit('done', {'status': 'completed'})
            except Exception as e:
                logger.error(f"Stream error: {e}")
                emit('error', {'message': str(e)})
            finally:
                _clear_cancel(sid)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(stream_response())
        finally:
            loop.close()

    # ------------------------------------------------------------------ #
    #  Agent chat (ReAct + decomposition + reflection)
    # ------------------------------------------------------------------ #
    @socketio.on('agent_chat')
    def handle_agent_chat(data):
        """Handle chat message using the full agent pipeline"""
        doc_id = data.get('doc_id')
        query = data.get('query')
        model_type = data.get('model_type', 'text')
        use_memory = data.get('use_memory', True)

        if not doc_id or not query:
            emit('error', {'message': 'Missing doc_id or query'})
            return

        doc = document_store.get_document(doc_id)
        if not doc:
            emit('error', {'message': 'Document not found'})
            return
        if doc.status != 'ready':
            emit('error', {'message': f'Document not ready: {doc.status}'})
            return

        sid = request.sid
        _clear_cancel(sid)
        logger.info(f"Agent chat - doc: {doc_id}, query: {query[:50]}..., model: {model_type}")

        async def stream_agent():
            stopped = False
            try:
                async for chunk in rag_service.agent_chat_stream(doc_id, query, model_type, use_memory):
                    if _is_cancelled(sid):
                        stopped = True
                        break
                    if not _process_chunk(chunk):
                        emit('chunk', {'content': chunk})
                if stopped:
                    emit('stopped', {'status': 'stopped'})
                else:
                    emit('done', {'status': 'completed'})
            except Exception as e:
                logger.error(f"Agent stream error: {e}")
                emit('error', {'message': str(e)})
            finally:
                _clear_cancel(sid)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(stream_agent())
        finally:
            loop.close()

    # ------------------------------------------------------------------ #
    #  Vision model (non-streaming, legacy compatible)
    # ------------------------------------------------------------------ #
    @socketio.on('chat_sync')
    def handle_chat_sync(data):
        """Handle chat message without streaming (for vision model)"""
        doc_id = data.get('doc_id')
        query = data.get('query')
        model_type = data.get('model_type', 'vision')
        use_memory = data.get('use_memory', True)
        use_agent = data.get('use_agent', False)

        if not doc_id or not query:
            emit('error', {'message': 'Missing doc_id or query'})
            return

        doc = document_store.get_document(doc_id)
        if not doc or doc.status != 'ready':
            emit('error', {'message': 'Document not ready'})
            return

        sid = request.sid
        _clear_cancel(sid)

        async def get_response():
            stopped = False
            try:
                stream_fn = (
                    rag_service.agent_chat_stream if use_agent
                    else rag_service.chat_stream
                )
                full_response = ""
                async for chunk in stream_fn(doc_id, query, model_type, use_memory):
                    if _is_cancelled(sid):
                        stopped = True
                        break
                    if not _process_chunk(chunk):
                        full_response += chunk

                if stopped:
                    if full_response:
                        emit('response', {'content': full_response})
                    emit('stopped', {'status': 'stopped'})
                else:
                    emit('response', {'content': full_response})
                    emit('done', {'status': 'completed'})
            except Exception as e:
                logger.error(f"Response error: {e}")
                emit('error', {'message': str(e)})
            finally:
                _clear_cancel(sid)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(get_response())
        finally:
            loop.close()

    # ------------------------------------------------------------------ #
    #  History management
    # ------------------------------------------------------------------ #
    @socketio.on('get_history')
    def handle_get_history(data):
        doc_id = data.get('doc_id')
        if not doc_id:
            emit('error', {'message': 'Missing doc_id'})
            return
        history = rag_service.get_chat_history(doc_id)
        emit('history', {'history': history})

    @socketio.on('clear_history')
    def handle_clear_history(data):
        doc_id = data.get('doc_id')
        if not doc_id:
            emit('error', {'message': 'Missing doc_id'})
            return
        rag_service.clear_chat_history(doc_id)
        emit('history_cleared', {'doc_id': doc_id})
