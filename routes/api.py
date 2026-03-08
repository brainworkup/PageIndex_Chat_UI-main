#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
API routes for PageIndex Chat UI
"""

import os
import uuid
import json
import logging
from datetime import datetime
from flask import Blueprint, request, jsonify, current_app
from flask_socketio import emit
from werkzeug.utils import secure_filename

from models.document import Document, document_store, UPLOADS_DIR, RESULTS_DIR
from services.rag_service import rag_service
from services.indexing_service import indexing_service
from services.skill_manager import skill_manager, Skill
from config import config_manager

logger = logging.getLogger(__name__)

api_bp = Blueprint('api', __name__)


# ============= Configuration Routes =============

@api_bp.route('/config/models', methods=['GET'])
def get_models():
    """Get all model configurations"""
    return jsonify({
        'models': config_manager.get_all_models(),
        'default_type': config_manager.get_default_model_type()
    })


@api_bp.route('/config/models/<model_type>', methods=['GET', 'PUT'])
def model_config(model_type):
    """Get or update model configuration"""
    if request.method == 'GET':
        return jsonify(config_manager.get_model_config(model_type))
    
    elif request.method == 'PUT':
        data = request.json
        config_manager.set_model_config(model_type, data)
        return jsonify({'success': True, 'message': f'{model_type} model config updated'})


@api_bp.route('/config/default-model', methods=['PUT'])
def set_default_model():
    """Set default model type"""
    data = request.json
    model_type = data.get('model_type')
    if model_type not in ['text', 'vision']:
        return jsonify({'error': 'Invalid model type'}), 400
    
    config_manager.set_default_model_type(model_type)
    return jsonify({'success': True, 'default_type': model_type})


# ============= Document Routes =============

@api_bp.route('/documents', methods=['GET'])
def list_documents():
    """List all documents"""
    docs = [doc.to_dict() for doc in document_store.get_all_documents()]
    return jsonify({'documents': docs})


@api_bp.route('/documents/upload', methods=['POST'])
def upload_document():
    """Upload and index a PDF document"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are supported'}), 400
    
    try:
        # Generate document ID using datetime prefix
        now = datetime.now()
        datetime_prefix = now.strftime("%Y%m%d_%H%M%S")
        doc_id = f"{datetime_prefix}_{str(uuid.uuid4())[:4]}"  # e.g., 20260228_140000_a1b2
        
        # Save file with datetime prefix
        filename = secure_filename(file.filename)
        os.makedirs(UPLOADS_DIR, exist_ok=True)
        
        file_path = os.path.join(UPLOADS_DIR, f"{doc_id}_{filename}")
        file.save(file_path)
        
        # Create document record
        # result_dir will be results/{doc_id}_{filename} to match uploads naming
        doc = Document(
            doc_id=doc_id,
            filename=filename,
            file_path=file_path,
            status='pending'
        )
        document_store.add_document(doc)
        
        # Start indexing in background
        from threading import Thread
        
        def run_indexing():
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success = loop.run_until_complete(
                    indexing_service.index_pdf(doc_id, file_path, filename)
                )
                if success:
                    from services.rag_service import rag_service
                    doc = document_store.get_document(doc_id)
                    if doc and os.path.exists(doc.structure_path):
                        loop.run_until_complete(
                            rag_service.prepare_document(doc_id, file_path, doc.structure_path)
                        )
                        # Direction 5: Proactive document analysis
                        try:
                            loop.run_until_complete(
                                rag_service.auto_analyze_document(doc_id)
                            )
                        except Exception as e:
                            logger.warning(f"Auto-analysis failed (non-fatal): {e}")
            finally:
                loop.close()
        
        thread = Thread(target=run_indexing)
        thread.start()
        
        return jsonify({
            'success': True,
            'document': doc.to_dict(),
            'message': 'Document uploaded, indexing started'
        })
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return jsonify({'error': str(e)}), 500


@api_bp.route('/documents/<doc_id>', methods=['GET'])
def get_document(doc_id):
    """Get document details"""
    doc = document_store.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    
    return jsonify({'document': doc.to_dict()})


@api_bp.route('/documents/<doc_id>', methods=['DELETE'])
def delete_document(doc_id):
    """Delete a document"""
    try:
        document_store.delete_document(doc_id)
        return jsonify({'success': True, 'message': 'Document deleted'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@api_bp.route('/documents/<doc_id>/status', methods=['GET'])
def get_document_status(doc_id):
    """Get document indexing status"""
    doc = document_store.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    
    return jsonify({
        'status': doc.status,
        'error_message': doc.error_message
    })


# ============= Chat Routes =============

@api_bp.route('/chat/<doc_id>/history', methods=['GET'])
def get_chat_history(doc_id):
    """Get chat history for a document"""
    history = rag_service.get_chat_history(doc_id)
    return jsonify({'history': history})


@api_bp.route('/chat/<doc_id>/clear', methods=['POST'])
def clear_chat_history(doc_id):
    """Clear chat history for a document"""
    rag_service.clear_chat_history(doc_id)
    return jsonify({'success': True, 'message': 'Chat history cleared'})


# ============= Tree Structure Routes =============

@api_bp.route('/documents/<doc_id>/tree', methods=['GET'])
def get_tree_structure(doc_id):
    """Get document tree structure"""
    tree = document_store.get_tree(doc_id)
    if not tree:
        return jsonify({'error': 'Tree structure not found'}), 404
    
    # Remove text field to reduce response size
    from services.rag_service import PageIndexService
    service = PageIndexService(document_store)
    clean_tree = service.remove_fields(tree, ['text'])
    
    return jsonify({'tree': clean_tree})


@api_bp.route('/documents/<doc_id>/analysis', methods=['GET'])
def get_document_analysis(doc_id):
    """Get proactive document analysis (Direction 5)"""
    doc = document_store.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404

    analysis = document_store.get_analysis(doc_id)
    if not analysis:
        return jsonify({'error': 'Analysis not available yet'}), 404

    return jsonify({'analysis': analysis})


@api_bp.route('/documents/<doc_id>/node-info', methods=['GET'])
def get_node_info(doc_id):
    """Get node mapping and page image URLs for preview functionality"""
    doc = document_store.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    
    tree = document_store.get_tree(doc_id)
    node_map = document_store.get_node_map(doc_id)
    page_images = document_store.get_page_images(doc_id)
    
    # If node_map doesn't exist but tree does, create it
    if tree and not node_map:
        from services.rag_service import PageIndexService
        service = PageIndexService(document_store)
        page_count = doc.page_count or 0
        
        # Get page count from tree structure
        if not page_count:
            def count_pages(node):
                max_page = 0
                if isinstance(node, dict):
                    if 'page' in node:
                        max_page = max(max_page, node.get('page', 0))
                    for child in node.get('children', []):
                        max_page = max(max_page, count_pages(child))
                elif isinstance(node, list):
                    for item in node:
                        max_page = max(max_page, count_pages(item))
                return max_page
            page_count = count_pages(tree)
        
        node_map = service.create_node_mapping(tree, include_page_ranges=True, max_page=page_count)
        document_store.cache_node_map(doc_id, node_map)
    
    if not node_map:
        return jsonify({'error': 'Node mapping not available'}), 404
    
    # Build response with node info and all page image URLs
    # Convert absolute paths to relative URLs
    node_info = {}
    for node_id, info in node_map.items():
        node = info.get('node', {})
        start_index = info.get('start_index')
        end_index = info.get('end_index')
        
        node_info[node_id] = {
            'title': node.get('title', ''),
            'summary': node.get('summary', ''),
            'start_index': start_index,
            'end_index': end_index
        }
    
    # Build all pages URLs for the entire document
    all_pages = []
    page_count = doc.page_count or 0
    for page_num in range(1, page_count + 1):
        page_url = f"/api/results/{doc_id}_{doc.filename}/images/page_{page_num}.jpg"
        all_pages.append({'page': page_num, 'url': page_url})
    
    return jsonify({
        'node_map': node_info,
        'page_count': page_count,
        'all_pages': all_pages
    })


@api_bp.route('/documents/<doc_id>/text-highlights', methods=['GET'])
def get_text_highlights(doc_id):
    """Return per-page text block positions with node ownership for overlay highlighting."""
    doc = document_store.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Document not found'}), 404
    if doc.status != 'ready':
        return jsonify({'error': 'Document not ready'}), 400

    cache_path = os.path.join(doc.result_dir, 'text_highlights.json')
    if os.path.exists(cache_path):
        with open(cache_path, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))

    node_map = document_store.get_node_map(doc_id)
    if not node_map:
        return jsonify({'error': 'Node mapping not available'}), 404

    from services.rag_service import PageIndexService
    service = PageIndexService(document_store)

    try:
        highlights = service.extract_text_highlights(doc.file_path, node_map)
    except Exception as e:
        logger.error(f"Text highlight extraction error: {e}")
        return jsonify({'error': str(e)}), 500

    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(highlights, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to cache highlights: {e}")

    return jsonify(highlights)


# ============= Skill Routes =============

@api_bp.route('/skills', methods=['GET'])
def list_skills():
    """List all custom agent skills"""
    skills = skill_manager.list_skills()
    return jsonify({'skills': [s.to_dict() for s in skills]})


@api_bp.route('/skills', methods=['POST'])
def create_skill():
    """Create a new skill"""
    data = request.json or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Skill name is required'}), 400
    skill = skill_manager.create_skill(
        name=name,
        description=data.get('description', ''),
        content=data.get('content', ''),
        enabled=data.get('enabled', True),
    )
    return jsonify({'success': True, 'skill': skill.to_dict()})


@api_bp.route('/skills/<skill_id>', methods=['GET'])
def get_skill(skill_id):
    """Get a single skill"""
    skill = skill_manager.get_skill(skill_id)
    if not skill:
        return jsonify({'error': 'Skill not found'}), 404
    return jsonify({'skill': skill.to_dict()})


@api_bp.route('/skills/<skill_id>', methods=['PUT'])
def update_skill(skill_id):
    """Update a skill"""
    data = request.json or {}
    skill = skill_manager.update_skill(skill_id, **data)
    if not skill:
        return jsonify({'error': 'Skill not found'}), 404
    return jsonify({'success': True, 'skill': skill.to_dict()})


@api_bp.route('/skills/<skill_id>', methods=['DELETE'])
def delete_skill(skill_id):
    """Delete a skill"""
    if skill_manager.delete_skill(skill_id):
        return jsonify({'success': True})
    return jsonify({'error': 'Skill not found'}), 404


@api_bp.route('/skills/upload', methods=['POST'])
def upload_skill():
    """Upload a skill from a .md file"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename or not file.filename.endswith('.md'):
        return jsonify({'error': 'Only .md files are supported'}), 400

    content = file.read().decode('utf-8')
    skill_id = secure_filename(file.filename)[:-3]
    skill = Skill.from_markdown(content, skill_id)
    skill_manager.save_skill(skill)
    return jsonify({'success': True, 'skill': skill.to_dict()})
