/**
 * PageIndex Agent — Frontend Application
 */

// ====================== State ======================
let socket;
let currentDocId = null;
let currentModelType = 'text';
let useMemory = true;
let useAgent = true;
let isStreaming = false;
let nodeMapCache = {};
let allPagesCache = {};
let currentAnalysis = null;   // persisted analysis for current doc
let _streamingRawText = '';   // accumulate raw text during streaming for markdown rendering
let highlightsCache = {};     // doc_id -> text highlight data from backend
let activeHighlightNodeId = null; // which node is highlighted (null = none)
let _highlightObserver = null; // ResizeObserver for canvas redraw

// ====================== Markdown Config ======================
if (typeof marked !== 'undefined') {
    marked.setOptions({
        breaks: true,
        gfm: true
    });
}

function normalizeMathDelimiters(text) {
    if (!text) return '';
    return text.replace(/(^|\n)\[\s*\n([\s\S]*?)\n\](?=\n|$)/g, '$1\\\\[\n$2\n\\\\]');
}

let _mathStore = [];

function protectMathDelimiters(text) {
    if (!text) return text;
    _mathStore = [];
    let idx = 0;
    text = text.replace(/\$\$([\s\S]*?)\$\$/g, (m) => {
        _mathStore.push(m);
        return `@@MATH_PLACEHOLDER_${idx++}@@`;
    });
    text = text.replace(/\\\[([\s\S]*?)\\\]/g, (m) => {
        _mathStore.push(m);
        return `@@MATH_PLACEHOLDER_${idx++}@@`;
    });
    text = text.replace(/\\\(([\s\S]*?)\\\)/g, (m) => {
        _mathStore.push(m);
        return `@@MATH_PLACEHOLDER_${idx++}@@`;
    });
    text = text.replace(/(?<![\\$])\$(?!\$)((?:[^$\\]|\\.)+?)\$/g, (m) => {
        _mathStore.push(m);
        return `@@MATH_PLACEHOLDER_${idx++}@@`;
    });
    return text;
}

function restoreMathDelimiters(html) {
    if (!html) return html;
    return html.replace(/@@MATH_PLACEHOLDER_(\d+)@@/g, (_, i) => {
        const original = _mathStore[parseInt(i)] || '';
        const d = document.createElement('div');
        d.textContent = original;
        return d.innerHTML;
    });
}

function renderMarkdown(text) {
    if (!text) return '';
    const normalized = normalizeMathDelimiters(text);
    const protectedText = protectMathDelimiters(normalized);
    if (typeof marked !== 'undefined') {
        let html = marked.parse(protectedText);
        html = restoreMathDelimiters(html);
        html = html.replace(/<table([\s\S]*?<\/table>)/g, '<div class="table-wrapper"><table$1</div>');
        return html;
    }
    return esc(normalized);
}

function renderMathInContainer(container) {
    if (!container || typeof renderMathInElement === 'undefined') return;
    renderMathInElement(container, {
        throwOnError: false,
        delimiters: [
            { left: '$$', right: '$$', display: true },
            { left: '\\[', right: '\\]', display: true },
            { left: '$', right: '$', display: false },
            { left: '\\(', right: '\\)', display: false }
        ]
    });
}

// ====================== Bootstrap ======================
document.addEventListener('DOMContentLoaded', () => {
    initSocket();
    loadDocuments();
    loadConfig();
    setupDragDrop();
    setupEventListeners();
});

// ====================== Event Listeners ======================
function setupEventListeners() {
    document.getElementById('textModelBtn')?.addEventListener('click', () => switchModel('text'));
    document.getElementById('visionModelBtn')?.addEventListener('click', () => switchModel('vision'));
    document.getElementById('settingsBtn')?.addEventListener('click', openSettingsModal);
    document.getElementById('memoryToggle')?.addEventListener('change', e => { useMemory = e.target.checked; });

    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('fileInput');
    if (uploadArea && fileInput) {
        uploadArea.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', e => { if (e.target.files?.[0]) uploadDocument(e.target.files[0]); });
    }

    document.getElementById('chatInput')?.addEventListener('keydown', handleKeyDown);
    const sendBtn = document.getElementById('sendBtn');
    if (sendBtn) sendBtn.onclick = sendMessage;
    document.getElementById('saveSettingsBtn')?.addEventListener('click', () => window.saveSettings?.());

    const skillFileInput = document.getElementById('skillFileInput');
    if (skillFileInput) skillFileInput.addEventListener('change', e => { if (e.target.files?.[0]) uploadSkillFile(e.target.files[0]); e.target.value = ''; });

    loadSkills();
}

// ====================== Agent Toggle ======================
function toggleAgent() {
    useAgent = !useAgent;
    const track = document.getElementById('agentToggle');
    const banner = document.getElementById('agentBanner');
    track?.classList.toggle('active', useAgent);
    banner?.classList.toggle('hidden', !useAgent);
}

// ====================== Analysis Bar ======================
function toggleAnalysisBar() {
    const bar = document.getElementById('analysisBar');
    const miniBtn = document.getElementById('analysisMiniBtn');
    if (!bar) return;
    const isOpen = bar.classList.contains('open');
    bar.classList.toggle('open', !isOpen);
    miniBtn?.classList.toggle('visible', isOpen); // show mini when bar closes
}

function showAnalysisBar(analysis) {
    currentAnalysis = analysis;
    const bar = document.getElementById('analysisBar');
    const body = document.getElementById('analysisBarBody');
    const miniBtn = document.getElementById('analysisMiniBtn');
    if (!bar || !body || !analysis) return;

    const findings = (analysis.key_findings || []).map(f => `<li>${esc(f)}</li>`).join('');
    const topics = (analysis.main_topics || []).map(t => `<li>${esc(t)}</li>`).join('');
    const questions = (analysis.suggested_questions || []).map(q =>
        `<button class="suggest-btn" onclick="askSuggested(this.textContent)">${esc(q)}</button>`
    ).join('');

    body.innerHTML = `
        <div class="analysis-summary-text">${esc(analysis.summary || '')}</div>
        <div class="analysis-grid">
            ${findings ? `<div class="analysis-card"><div class="analysis-card-title"><i class="bi bi-bookmark-star"></i> 关键发现</div><ul>${findings}</ul></div>` : ''}
            ${topics ? `<div class="analysis-card"><div class="analysis-card-title"><i class="bi bi-tags"></i> 主要主题</div><ul>${topics}</ul></div>` : ''}
        </div>
        ${questions ? `<div><div style="font-size:12px;font-weight:600;color:var(--text-primary);margin-bottom:8px"><i class="bi bi-chat-left-quote"></i> 建议提问</div><div class="suggest-questions">${questions}</div></div>` : ''}
    `;

    bar.classList.add('open');
    miniBtn?.classList.remove('visible');
}

function hideAnalysisBar() {
    const bar = document.getElementById('analysisBar');
    const miniBtn = document.getElementById('analysisMiniBtn');
    bar?.classList.remove('open');
    miniBtn?.classList.remove('visible');
    document.getElementById('analysisBarBody').innerHTML = '';
    currentAnalysis = null;
}

function askSuggested(question) {
    const input = document.getElementById('chatInput');
    if (input) { input.value = question; sendMessage(); }
}

async function loadAnalysis(docId, retries = 3) {
    for (let i = 0; i < retries; i++) {
        try {
            const r = await fetch(`/api/documents/${docId}/analysis`);
            if (r.ok) { const d = await r.json(); return d.analysis; }
        } catch { /* ignore */ }
        if (i < retries - 1) await new Promise(ok => setTimeout(ok, 2000));
    }
    return null;
}

// ====================== Text Highlights ======================

async function fetchTextHighlights(docId) {
    if (highlightsCache[docId]) return highlightsCache[docId];
    try {
        const r = await fetch(`/api/documents/${docId}/text-highlights`);
        if (r.ok) {
            const data = await r.json();
            highlightsCache[docId] = data;
            return data;
        }
    } catch (e) { console.error('Highlight fetch error:', e); }
    return null;
}

function drawHighlightsOnPage(container, pageNum, hlData, nodeMap, targetNodeId) {
    container.querySelectorAll('.highlight-canvas').forEach(c => c.remove());
    if (!targetNodeId || !hlData) return;

    const pageInfo = hlData.pages?.[String(pageNum)];
    if (!pageInfo || !pageInfo.blocks?.length) return;

    const hasTarget = pageInfo.blocks.some(b => b.node_id === targetNodeId);
    if (!hasTarget) return;

    const img = container.querySelector('.page-preview-image');
    if (!img || !img.naturalWidth) return;

    const canvas = document.createElement('canvas');
    canvas.className = 'highlight-canvas';
    const displayW = img.clientWidth;
    const displayH = img.clientHeight;
    canvas.width = displayW;
    canvas.height = displayH;
    canvas.style.width = displayW + 'px';
    canvas.style.height = displayH + 'px';
    container.insertBefore(canvas, img.nextSibling);

    const ctx = canvas.getContext('2d');
    const scale = hlData.scale || 2.0;
    const sx = displayW / (pageInfo.width * scale);
    const sy = displayH / (pageInfo.height * scale);

    const nodeKeys = Object.keys(nodeMap || {});
    const colorIdx = nodeKeys.indexOf(targetNodeId);
    const color = NODE_COLORS[(colorIdx >= 0 ? colorIdx : 0) % NODE_COLORS.length];
    const rgb = hexToRgb(color.text) || { r: 79, g: 70, b: 229 };

    for (const block of pageInfo.blocks) {
        if (block.node_id !== targetNodeId) continue;
        const [x0, y0, x1, y1] = block.bbox;
        ctx.fillStyle = `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, 0.18)`;
        ctx.fillRect(
            x0 * scale * sx,
            y0 * scale * sy,
            (x1 - x0) * scale * sx,
            (y1 - y0) * scale * sy
        );
    }
}

function hexToRgb(hex) {
    if (!hex || hex[0] !== '#') return null;
    const v = parseInt(hex.slice(1), 16);
    return { r: (v >> 16) & 255, g: (v >> 8) & 255, b: v & 255 };
}

function redrawAllHighlights(modal, hlData, nodeMap, targetNodeId) {
    if (!modal || !hlData) return;
    const containers = modal.querySelectorAll('.page-image-container');
    containers.forEach(c => {
        const idx = parseInt(c.dataset.index);
        drawHighlightsOnPage(c, idx + 1, hlData, nodeMap, targetNodeId);
    });
}

function clearAllHighlights() {
    const modal = document.getElementById('pagePreviewModal');
    if (modal) modal.querySelectorAll('.highlight-canvas').forEach(c => c.remove());
    activeHighlightNodeId = null;
    updateHighlightToggleBtn();
    updateActiveNodeTags();
}

function activateNodeHighlight(nodeId) {
    if (activeHighlightNodeId === nodeId) {
        clearAllHighlights();
        return;
    }
    activeHighlightNodeId = nodeId;
    const modal = document.getElementById('pagePreviewModal');
    if (!modal || !currentDocId) return;
    const nMap = nodeMapCache[currentDocId] || {};
    const hlData = highlightsCache[currentDocId];
    redrawAllHighlights(modal, hlData, nMap, nodeId);
    updateHighlightToggleBtn();
    updateActiveNodeTags();
}

function toggleHighlights() {
    if (activeHighlightNodeId) {
        clearAllHighlights();
    }
}

function updateHighlightToggleBtn() {
    const btn = document.getElementById('highlightToggleBtn');
    if (btn) {
        btn.classList.toggle('active', !!activeHighlightNodeId);
        btn.title = activeHighlightNodeId ? '清除高亮 (' + activeHighlightNodeId + ')' : '点击节点标签以高亮';
    }
}

function updateActiveNodeTags() {
    const modal = document.getElementById('pagePreviewModal');
    if (!modal) return;
    modal.querySelectorAll('.page-node-tag').forEach(tag => {
        const nid = tag.getAttribute('data-node-id');
        tag.classList.toggle('highlight-active', nid === activeHighlightNodeId);
    });
}

// ====================== Socket.IO ======================
function initSocket() {
    socket = io();
    socket.on('connect', () => console.log('Connected'));
    socket.on('status', d => updateStatus(d.status));
    socket.on('thinking', d => showThinking(d.content));
    socket.on('thinking_chunk', d => appendToThinking(d.content));
    socket.on('nodes', d => showNodes(d.nodes));
    socket.on('chunk', d => appendToResponse(d.content));
    socket.on('response', d => setResponse(d.content));
    socket.on('done', () => finishResponse());
    socket.on('stopped', () => finishResponse(true));
    socket.on('error', d => showError(d.message));
    socket.on('history', d => displayHistory(d.history));
    socket.on('history_cleared', d => { if (currentDocId === d.doc_id) clearChatDisplay(); });

    // Agent events
    socket.on('agent_step', d => renderAgentStep(d));
    socket.on('agent_decompose', d => renderDecompose(d));
    socket.on('agent_reflect', d => renderReflect(d));
}

// ====================== Agent UI ======================

function getOrCreateTimeline() {
    let tl = document.getElementById('agentTimeline');
    if (!tl) {
        const mc = document.getElementById('chatMessages');
        const ti = document.getElementById('typingIndicator');
        tl = document.createElement('div');
        tl.className = 'agent-timeline';
        tl.id = 'agentTimeline';
        tl.innerHTML = '<div class="agent-timeline-header"><i class="bi bi-robot"></i> Agent 推理过程</div><div id="agentSteps"></div>';
        if (ti) ti.before(tl); else mc.appendChild(tl);
    }
    return tl;
}

function renderAgentStep(d) {
    getOrCreateTimeline();
    const sc = document.getElementById('agentSteps');
    if (!sc) return;
    const tool = d.tool === 'final_answer' ? '准备回答' : (d.tool || '');
    const div = document.createElement('div');
    div.className = 'agent-step';
    div.innerHTML = `
        <div class="step-header">
            <span class="step-number">Step ${d.step || ''}</span>
            <span class="step-tool">${esc(tool)}</span>
        </div>
        ${d.thought ? `<div class="step-thought">${esc(d.thought)}</div>` : ''}
        ${d.observation ? `<div class="step-observation">${esc(d.observation)}</div>` : ''}
    `;
    sc.appendChild(div);
    scrollToBottom();
}

function renderDecompose(d) {
    if (!d.needs_decomposition) return;
    const mc = document.getElementById('chatMessages');
    const ti = document.getElementById('typingIndicator');
    const box = document.createElement('div');
    box.className = 'decompose-box';
    const qs = (d.sub_questions || []).map((q, i) => `<div class="sub-question">${i+1}. ${esc(q)}</div>`).join('');
    box.innerHTML = `<strong><i class="bi bi-diagram-3"></i> 问题分解 (${esc(d.synthesis_strategy || 'direct')})</strong>${qs}`;
    if (ti) ti.before(box); else mc.appendChild(box);
    scrollToBottom();
}

function renderReflect(d) {
    const mc = document.getElementById('chatMessages');
    const box = document.createElement('div');
    box.className = 'reflect-box';
    const s = d.score || 0;
    const cls = s < 6 ? 'poor' : s < 8 ? 'medium' : 'good';
    const action = d.action === 'accept' ? '回答质量满足要求' : '正在补充检索...';
    const icon = d.action === 'accept' ? 'bi-check-circle-fill' : 'bi-arrow-repeat';
    const issues = (d.issues || []).map(i => `<li>${esc(i)}</li>`).join('');
    box.innerHTML = `<strong><i class="bi bi-shield-check"></i> 自我检查</strong>
        <span class="reflect-score ${cls}">${s}/10</span>
        <span><i class="bi ${icon}"></i> ${action}</span>
        ${issues ? `<ul style="margin-top:6px;padding-left:18px;font-size:12px;color:#64748b">${issues}</ul>` : ''}`;
    mc.appendChild(box);
    scrollToBottom();
}

// ====================== History with Agent Step Reconstruction ======================

function displayHistory(history) {
    if (!history || history.length === 0) return;
    hideEmptyState();
    const mc = document.getElementById('chatMessages');

    history.forEach(msg => {
        // Render thinking: try to reconstruct agent timeline
        if (msg.thinking) {
            const steps = parseAgentSteps(msg.thinking);
            if (steps.length > 0) {
                renderHistoryTimeline(mc, steps);
            } else {
                const tb = document.createElement('div');
                tb.className = 'thinking-box';
                tb.innerHTML = `<strong>推理过程</strong><span class="thinking-content">${esc(msg.thinking)}</span>`;
                mc.appendChild(tb);
            }
        }
        if (msg.nodes?.length > 0) {
            const nb = document.createElement('div');
            nb.className = 'nodes-box';
            nb.innerHTML = `<strong>检索节点:</strong> ${msg.nodes.map(n => `<span class="node-tag" onclick="showNodePreview('${n}')">${n}</span>`).join(' ')}`;
            mc.appendChild(nb);
        }
        const div = document.createElement('div');
        div.className = `message message-${msg.role}`;
        const rendered = msg.role === 'assistant' ? renderMarkdown(msg.content) : esc(msg.content);
        div.innerHTML = `<div class="message-content">${rendered}</div>`;
        mc.appendChild(div);
        if (msg.role === 'assistant') {
            renderMathInContainer(div.querySelector('.message-content'));
        }
    });
    scrollToBottom();
}

function parseAgentSteps(thinking) {
    // Format: "Step N [tool_name]: thought text"
    const regex = /^Step\s+(\d+)\s+\[([^\]]+)\]:\s*(.+)$/gm;
    const steps = [];
    let m;
    while ((m = regex.exec(thinking)) !== null) {
        steps.push({ step: parseInt(m[1]), tool: m[2], thought: m[3] });
    }
    return steps;
}

function renderHistoryTimeline(container, steps) {
    const tl = document.createElement('div');
    tl.className = 'agent-timeline';
    tl.innerHTML = '<div class="agent-timeline-header"><i class="bi bi-robot"></i> Agent 推理过程</div>';
    const sc = document.createElement('div');
    steps.forEach(s => {
        const tool = s.tool === 'final_answer' ? '准备回答' : s.tool;
        const div = document.createElement('div');
        div.className = 'agent-step';
        div.innerHTML = `
            <div class="step-header">
                <span class="step-number">Step ${s.step}</span>
                <span class="step-tool">${esc(tool)}</span>
            </div>
            <div class="step-thought">${esc(s.thought)}</div>
        `;
        sc.appendChild(div);
    });
    tl.appendChild(sc);
    container.appendChild(tl);
}

// ====================== Documents ======================

async function loadDocuments() {
    try {
        const r = await fetch('/api/documents');
        const d = await r.json();
        renderDocuments(d.documents);
    } catch (e) { console.error('Load docs error:', e); }
}

function renderDocuments(docs) {
    const c = document.getElementById('documentList');
    if (!docs.length) {
        c.innerHTML = '<div style="text-align:center;padding:20px;color:rgba(255,255,255,0.5)"><i class="bi bi-file-earmark" style="font-size:32px"></i><p style="margin-top:10px">暂无文档</p></div>';
        return;
    }
    c.innerHTML = docs.map(d => `
        <div class="doc-item ${d.doc_id===currentDocId?'active':''} ${d.status==='error'?'error':''}" data-doc-id="${d.doc_id}">
            <div class="doc-name">${d.filename}</div>
            <div class="doc-status">
                <span class="status-badge status-${d.status}"></span>
                ${statusText(d.status)}
                ${d.status==='error'&&d.error_message?`<span title="${d.error_message}"><i class="bi bi-info-circle"></i></span>`:''}
            </div>
            <div class="doc-actions">
                ${d.status==='error'?`<button class="doc-action-btn retry" onclick="event.stopPropagation();retryUpload('${d.doc_id}','${d.filename}')"><i class="bi bi-arrow-clockwise"></i> 重新上传</button>`:''}
                <button class="doc-action-btn delete" onclick="event.stopPropagation();deleteDocument('${d.doc_id}','${d.filename}')"><i class="bi bi-trash"></i> 删除</button>
            </div>
        </div>`).join('');
    c.querySelectorAll('.doc-item').forEach(el => el.addEventListener('click', () => selectDocument(el.dataset.docId)));
}

function statusText(s) {
    return {pending:'等待处理',indexing:'正在索引...',indexed:'索引完成',ready:'就绪',error:'错误'}[s]||s;
}

async function selectDocument(docId) {
    currentDocId = docId;
    document.querySelectorAll('.doc-item').forEach(el => el.classList.toggle('active', el.dataset.docId === docId));
    clearChatDisplay();
    hideEmptyState();

    // Load analysis into persistent bar
    const analysis = await loadAnalysis(docId, 1);
    if (analysis) showAnalysisBar(analysis);
    else hideAnalysisBar();

    socket.emit('get_history', { doc_id: docId });
}

async function uploadDocument(file) {
    if (!file || !file.name.toLowerCase().endsWith('.pdf')) { alert('请选择 PDF 文件'); return; }
    const fd = new FormData(); fd.append('file', file);
    try {
        const r = await fetch('/api/documents/upload', { method:'POST', body:fd });
        const d = await r.json();
        if (d.success) {
            currentDocId = d.document.doc_id;
            loadDocuments(); hideEmptyState(); clearChatDisplay(); hideAnalysisBar();
            pollDocumentStatus(d.document.doc_id);
        } else { alert('上传失败: ' + d.error); }
    } catch (e) { console.error('Upload error:', e); alert('上传失败'); }
}

async function deleteDocument(docId, filename) {
    if (!confirm(`确定要删除文档 "${filename}" 吗？`)) return;
    try {
        const r = await fetch(`/api/documents/${docId}`, {method:'DELETE'});
        const d = await r.json();
        if (d.success) {
            if (currentDocId===docId) { currentDocId=null; clearChatDisplay(); hideAnalysisBar(); }
            loadDocuments(); showNotification('文档已删除');
        } else { alert('删除失败: '+d.error); }
    } catch (e) { alert('删除失败'); }
}

function retryUpload(docId) { deleteDocForRetry(docId); }
async function deleteDocForRetry(docId) {
    try {
        const r = await fetch(`/api/documents/${docId}`, {method:'DELETE'});
        const d = await r.json();
        if (d.success) { if (currentDocId===docId){currentDocId=null;clearChatDisplay();} loadDocuments(); document.getElementById('fileInput')?.click(); }
    } catch (e) { alert('删除失败'); }
}

async function pollDocumentStatus(docId) {
    const poll = async () => {
        try {
            const r = await fetch(`/api/documents/${docId}/status`);
            const d = await r.json();
            loadDocuments();
            if (d.status === 'ready') {
                addSystemMessage('文档索引完成！正在生成智能分析...');
                // Auto-load analysis (with retries since it runs after 'ready')
                const analysis = await loadAnalysis(docId, 5);
                if (analysis) {
                    showAnalysisBar(analysis);
                    addSystemMessage('文档智能分析已生成，可以开始对话了！');
                } else {
                    addSystemMessage('文档已就绪，可以开始对话了。');
                }
                return;
            }
            if (d.status === 'error') { addSystemMessage('文档索引失败: ' + d.error_message); return; }
            setTimeout(poll, 2000);
        } catch (e) { console.error('Poll error:', e); }
    };
    setTimeout(poll, 1000);
}

// ====================== Config ======================

async function loadConfig() {
    try {
        const r = await fetch('/api/config/models'); const d = await r.json();
        document.getElementById('textModelName').value = d.models?.text?.name || '';
        document.getElementById('textApiKey').value = d.models?.text?.api_key || '';
        document.getElementById('textBaseUrl').value = d.models?.text?.base_url || '';
        document.getElementById('visionModelName').value = d.models?.vision?.name || '';
        document.getElementById('visionApiKey').value = d.models?.vision?.api_key || '';
        document.getElementById('visionBaseUrl').value = d.models?.vision?.base_url || '';
        currentModelType = d.default_type || 'text';
        updateModelToggle();
    } catch (e) { console.error('Config error:', e); }
}

function switchModel(t) { currentModelType = t; updateModelToggle(); }
function updateModelToggle() {
    document.getElementById('textModelBtn')?.classList.toggle('active', currentModelType==='text');
    document.getElementById('visionModelBtn')?.classList.toggle('active', currentModelType!=='text');
}

// ====================== Chat ======================

function sendMessage() {
    const input = document.getElementById('chatInput');
    const msg = input.value.trim();
    if (!msg || isStreaming) return;
    if (!currentDocId) { alert('请先选择或上传文档'); return; }

    addUserMessage(msg);
    input.value = '';
    showTypingIndicator();
    isStreaming = true;
    updateSendButton();

    const payload = { doc_id: currentDocId, query: msg, model_type: currentModelType, use_memory: useMemory };

    if (useAgent) {
        socket.emit('agent_chat', payload);
    } else {
        socket.emit('chat', payload);
    }
}

function stopGenerating() {
    if (!isStreaming) return;
    socket.emit('stop_generating');
}

function handleKeyDown(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (!isStreaming) sendMessage(); } }

// ====================== Status / Streaming ======================

function updateStatus(status) {
    const ti = document.getElementById('typingIndicator');
    const st = ti?.querySelector('.status-text');
    if (status === 'retry_answering') {
        _streamingRawText = '';
        const rc = document.getElementById('responseContent');
        if (rc) rc.innerHTML = '';
    }
    if (st) st.textContent = {preparing:'正在准备文档数据...',prepared:'准备完成',searching:'正在检索相关内容...',answering:'正在生成回答...',retrying:'Agent 正在补充检索...',retry_answering:'正在重新生成回答...'}[status] || '';
}

function showThinking(content) {
    const ti = document.getElementById('typingIndicator');
    if (ti) {
        const b = document.createElement('div'); b.className='thinking-box'; b.id='thinkingBox';
        b.innerHTML = `<strong>推理过程</strong><span class="thinking-content">${content}</span>`;
        ti.before(b);
    }
}

function appendToThinking(content) {
    let b = document.getElementById('thinkingBox');
    if (!b) {
        const ti = document.getElementById('typingIndicator');
        const mc = document.getElementById('chatMessages');
        b = document.createElement('div'); b.className='thinking-box'; b.id='thinkingBox';
        b.innerHTML = '<strong>推理过程</strong><span class="thinking-content"></span>';
        if (ti) ti.before(b); else mc.appendChild(b);
    }
    const tc = b.querySelector('.thinking-content');
    if (tc) { tc.textContent += content; scrollToBottom(); }
}

function showNodes(nodes) {
    const anchor = document.getElementById('thinkingBox') || document.getElementById('agentTimeline');
    if (anchor) {
        const h = `<div class="nodes-box"><strong>检索节点:</strong> ${nodes.map(n=>`<span class="node-tag" onclick="showNodePreview('${n}')">${n}</span>`).join(' ')}</div>`;
        anchor.insertAdjacentHTML('afterend', h);
    }
}

function appendToResponse(content) {
    let rc = document.getElementById('responseContent');
    if (!rc) {
        _streamingRawText = '';
        document.getElementById('typingIndicator')?.remove();
        const mc = document.getElementById('chatMessages');
        const box = document.createElement('div'); box.className='message message-assistant'; box.id='responseBox';
        box.innerHTML = '<div class="message-content" id="responseContent"></div>';
        mc.appendChild(box);
        rc = document.getElementById('responseContent');
    }
    _streamingRawText += content;
    if (rc) {
        rc.innerHTML = renderMarkdown(_streamingRawText);
        renderMathInContainer(rc);
        scrollToBottom();
    }
}

function setResponse(content) {
    document.getElementById('typingIndicator')?.remove();
    const mc = document.getElementById('chatMessages');
    const box = document.createElement('div'); box.className='message message-assistant';
    box.innerHTML = `<div class="message-content">${renderMarkdown(content)}</div>`;
    mc.appendChild(box);
    renderMathInContainer(box.querySelector('.message-content'));
    scrollToBottom();
}

function finishResponse(wasStopped = false) {
    isStreaming = false; updateSendButton();
    document.getElementById('typingIndicator')?.remove();
    const rc = document.getElementById('responseContent');
    if (rc && _streamingRawText) {
        const finalText = wasStopped ? _streamingRawText + '\n\n---\n*（已停止生成）*' : _streamingRawText;
        rc.innerHTML = renderMarkdown(finalText);
        renderMathInContainer(rc);
    }
    _streamingRawText = '';
    ['responseBox','responseContent','thinkingBox','agentTimeline','agentSteps'].forEach(id => {
        document.getElementById(id)?.removeAttribute('id');
    });
}

function showError(msg) {
    isStreaming = false; updateSendButton();
    document.getElementById('typingIndicator')?.remove();
    addSystemMessage('错误: ' + msg);
}

// ====================== Messages ======================

function addUserMessage(content) {
    hideEmptyState();
    const mc = document.getElementById('chatMessages');
    const d = document.createElement('div'); d.className='message message-user';
    d.innerHTML = `<div class="message-content">${esc(content)}</div>`;
    mc.appendChild(d); scrollToBottom();
}

function addSystemMessage(content) {
    const mc = document.getElementById('chatMessages');
    const d = document.createElement('div'); d.className='message message-assistant';
    d.innerHTML = `<div class="message-content" style="background:#fef3c7;color:#92400e"><i class="bi bi-info-circle"></i> ${content}</div>`;
    mc.appendChild(d); scrollToBottom();
}

function showTypingIndicator() {
    const mc = document.getElementById('chatMessages');
    const ti = document.createElement('div'); ti.className='typing-indicator'; ti.id='typingIndicator';
    ti.innerHTML = '<span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span><span class="status-text" style="margin-left:10px;font-size:14px;color:#64748b"></span>';
    mc.appendChild(ti); scrollToBottom();
}

function updateSendButton() {
    const b = document.getElementById('sendBtn');
    if (!b) return;
    if (isStreaming) {
        b.innerHTML = '<i class="bi bi-stop-fill"></i>';
        b.classList.add('stop-mode');
        b.disabled = false;
        b.onclick = stopGenerating;
    } else {
        b.innerHTML = '<i class="bi bi-send"></i>';
        b.classList.remove('stop-mode');
        b.disabled = false;
        b.onclick = sendMessage;
    }
}

function clearChatDisplay() {
    document.getElementById('chatMessages').innerHTML = `
        <div class="empty-state" id="emptyState">
            <div class="empty-hero-icon"><i class="bi bi-robot"></i></div>
            <h4>PageIndex Agent</h4>
            <p>上传 PDF 文档，智能体会自动分析文档结构并提供深度问答服务</p>
            <div class="empty-features">
                <div class="empty-feature"><i class="bi bi-diagram-3"></i><span>多步推理</span></div>
                <div class="empty-feature"><i class="bi bi-tools"></i><span>多工具协作</span></div>
                <div class="empty-feature"><i class="bi bi-shield-check"></i><span>自我反思</span></div>
                <div class="empty-feature"><i class="bi bi-lightbulb"></i><span>主动分析</span></div>
            </div>
        </div>`;
}

function hideEmptyState() { document.getElementById('emptyState')?.remove(); }

// ====================== Settings ======================

function openSettingsModal() {
    const el = document.getElementById('settingsModal');
    if (el) new bootstrap.Modal(el).show();
}

window.saveSettings = async function () {
    const tc = {name:document.getElementById('textModelName').value, api_key:document.getElementById('textApiKey').value, base_url:document.getElementById('textBaseUrl').value, type:'text'};
    const vc = {name:document.getElementById('visionModelName').value, api_key:document.getElementById('visionApiKey').value, base_url:document.getElementById('visionBaseUrl').value, type:'vision'};
    try {
        await fetch('/api/config/models/text', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(tc)});
        await fetch('/api/config/models/vision', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(vc)});
        bootstrap.Modal.getInstance(document.getElementById('settingsModal'))?.hide();
        showNotification('配置已保存');
    } catch { alert('保存配置失败'); }
};

// ====================== Node Preview ======================

async function showNodePreview(nodeId) {
    if (!currentDocId) return;
    if (!nodeMapCache[currentDocId] || !allPagesCache[currentDocId]) {
        try {
            const r = await fetch(`/api/documents/${currentDocId}/node-info`);
            const d = await r.json();
            if (d.node_map) { nodeMapCache[currentDocId]=d.node_map; allPagesCache[currentDocId]=d.all_pages||[]; } else return;
        } catch { return; }
    }
    const info = nodeMapCache[currentDocId]?.[nodeId];
    if (!info) { showNotification('未找到节点信息'); return; }
    showPagePreviewModal(nodeId, info, allPagesCache[currentDocId]);
}

const NODE_COLORS = [
    {bg:'#eef2ff',text:'#4338ca'},{bg:'#ecfdf5',text:'#065f46'},{bg:'#fef3c7',text:'#92400e'},
    {bg:'#fce7f3',text:'#9d174d'},{bg:'#e0f2fe',text:'#075985'},{bg:'#f3e8ff',text:'#6b21a8'},
    {bg:'#fef2f2',text:'#991b1b'},{bg:'#f0fdf4',text:'#166534'},{bg:'#fff7ed',text:'#9a3412'},
    {bg:'#f5f3ff',text:'#5b21b6'},{bg:'#ecfeff',text:'#155e75'},{bg:'#fdf2f8',text:'#831843'},
];

function buildPageNodeMap(nodeMap) {
    const pageNodes = {};
    if (!nodeMap) return pageNodes;
    const entries = Object.entries(nodeMap);
    entries.forEach(([nid, info], idx) => {
        const s = info.start_index || 1;
        const e = info.end_index || s;
        const color = NODE_COLORS[idx % NODE_COLORS.length];
        for (let p = s; p <= e; p++) {
            if (!pageNodes[p]) pageNodes[p] = [];
            pageNodes[p].push({ id: nid, title: info.title || nid, color });
        }
    });
    return pageNodes;
}

function getNodeColor(nodeId, nodeMap) {
    const keys = Object.keys(nodeMap || {});
    const idx = keys.indexOf(nodeId);
    return NODE_COLORS[(idx >= 0 ? idx : 0) % NODE_COLORS.length];
}

function showPagePreviewModal(nodeId, nodeInfo, allPages) {
    let modal = document.getElementById('pagePreviewModal');
    if (!modal) {
        modal = document.createElement('div'); modal.id='pagePreviewModal'; modal.className='page-preview-modal';
        modal.innerHTML = `<div class="page-preview-content">
            <div class="page-preview-header">
                <h5 class="page-preview-title"></h5>
                <div class="page-preview-header-actions">
                    <button class="highlight-toggle-btn" id="highlightToggleBtn" onclick="toggleHighlights()" title="点击节点标签以高亮"><i class="bi bi-highlighter"></i></button>
                    <button class="page-preview-close" onclick="closePagePreviewModal()"><i class="bi bi-x-lg"></i></button>
                </div>
            </div>
            <div class="node-info-card" id="nodeInfoCard"></div>
            <div class="page-preview-body"><div class="page-preview-images"></div></div>
            <div class="page-preview-footer"><div class="page-preview-nav">
                <button class="page-nav-btn" id="prevPageBtn" onclick="navPage(-1)"><i class="bi bi-chevron-left"></i> 上一页</button>
                <span class="page-indicator" id="pageIndicator"></span>
                <button class="page-nav-btn" id="nextPageBtn" onclick="navPage(1)">下一页 <i class="bi bi-chevron-right"></i></button>
            </div></div></div>`;
        document.body.appendChild(modal);
    }

    activeHighlightNodeId = null;
    updateHighlightToggleBtn();

    const nMap = nodeMapCache[currentDocId] || {};
    const pageNodeMap = buildPageNodeMap(nMap);
    const currentStart = nodeInfo.start_index || 1;
    const currentEnd = nodeInfo.end_index || currentStart;
    const nodeColor = getNodeColor(nodeId, nMap);

    modal.querySelector('.page-preview-title').textContent = `PDF 预览`;

    const infoCard = modal.querySelector('#nodeInfoCard');
    infoCard.innerHTML = `
        <div class="node-info-badge" style="background:${nodeColor.bg};color:${nodeColor.text}">${nodeId}</div>
        <div class="node-info-detail">
            <div class="node-info-title">${esc(nodeInfo.title || '未命名节点')}</div>
            <div class="node-info-meta">
                <span><i class="bi bi-file-earmark"></i> 第 ${currentStart}–${currentEnd} 页</span>
            </div>
            ${nodeInfo.summary ? `<div class="node-info-summary">${esc(nodeInfo.summary)}</div>` : ''}
        </div>`;

    const imgs = modal.querySelector('.page-preview-images');

    if (!allPages?.length) {
        imgs.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-secondary)">无页面图片</div>';
    } else {
        imgs.innerHTML = allPages.map((p, i) => {
            const pageNum = p.page;
            const isCurrent = pageNum >= currentStart && pageNum <= currentEnd;
            const nodes = pageNodeMap[pageNum] || [];
            const tags = nodes.map(n => {
                const isActive = n.id === nodeId;
                const label = n.title.length > 20 ? n.title.slice(0, 18) + '…' : n.title;
                return `<span class="page-node-tag${isActive?' active-node':''}" data-node-id="${n.id}" `
                     + `style="background:${n.color.bg};color:${n.color.text}" `
                     + `onclick="event.stopPropagation();activateNodeHighlight('${n.id}')" `
                     + `title="点击高亮 ${esc(n.id + ': ' + n.title)}">`
                     + `<span class="page-node-tag-id">${n.id}</span> ${esc(label)}</span>`;
            }).join('');
            return `<div class="page-image-container${isCurrent?' current-node-page':''}" data-index="${i}">`
                 + `<img src="${p.url}" alt="Page ${pageNum}" class="page-preview-image" onclick="openFullscreen('${p.url}')">`
                 + (tags ? `<div class="page-node-tags">${tags}</div>` : '')
                 + `<div class="page-number">第 ${pageNum} 页</div></div>`;
        }).join('');
    }

    modal.dataset.pages = JSON.stringify(allPages);
    const si = Math.max(0, Math.min(currentStart - 1, allPages.length - 1));
    modal.dataset.currentIndex = si;
    updatePageNav();
    modal.classList.add('active');
    document.querySelector('.main-content')?.classList.add('preview-open');

    // Fetch highlights & draw once images load
    initHighlightsForModal(modal, nMap);

    setTimeout(() => {
        const target = modal.querySelectorAll('.page-image-container')[si];
        const scrollParent = modal.querySelector('.page-preview-body');
        if (target && scrollParent) {
            scrollParent.scrollTop = target.offsetTop - scrollParent.offsetTop;
        }
    }, 100);
}

async function initHighlightsForModal(modal, nMap) {
    if (!currentDocId) return;
    const hlData = await fetchTextHighlights(currentDocId);
    if (!hlData) return;

    if (_highlightObserver) { _highlightObserver.disconnect(); _highlightObserver = null; }

    // ResizeObserver to redraw on resize (only if a node is active)
    _highlightObserver = new ResizeObserver(() => {
        if (activeHighlightNodeId) {
            redrawAllHighlights(modal, hlData, nMap, activeHighlightNodeId);
        }
    });
    const body = modal.querySelector('.page-preview-body');
    if (body) _highlightObserver.observe(body);
}

function closePagePreviewModal() {
    activeHighlightNodeId = null;
    if (_highlightObserver) { _highlightObserver.disconnect(); _highlightObserver = null; }
    const m = document.getElementById('pagePreviewModal');
    if (m) { m.classList.remove('active'); document.querySelector('.main-content')?.classList.remove('preview-open'); }
}

function navPage(dir) {
    const m = document.getElementById('pagePreviewModal'); if (!m) return;
    const pages = JSON.parse(m.dataset.pages||'[]');
    let i = Math.max(0, Math.min(pages.length-1, (parseInt(m.dataset.currentIndex)||0)+dir));
    m.dataset.currentIndex = i;
    const target = m.querySelectorAll('.page-image-container')[i];
    const scrollParent = m.querySelector('.page-preview-body');
    if (target && scrollParent) {
        scrollParent.scrollTop = target.offsetTop - scrollParent.offsetTop;
    }
    updatePageNav();
}

function updatePageNav() {
    const m = document.getElementById('pagePreviewModal'); if (!m) return;
    const pages = JSON.parse(m.dataset.pages||'[]');
    const i = parseInt(m.dataset.currentIndex)||0;
    const ind = document.getElementById('pageIndicator');
    if (ind) ind.textContent = pages.length ? `${i+1} / ${pages.length}` : '0 / 0';
    const pb = document.getElementById('prevPageBtn'); if (pb) pb.disabled = i===0;
    const nb = document.getElementById('nextPageBtn'); if (nb) nb.disabled = i>=pages.length-1;
}

function openFullscreen(url) {
    const o = document.createElement('div'); o.className='fullscreen-image-overlay'; o.onclick=()=>o.remove();
    o.innerHTML = `<img src="${url}" class="fullscreen-image"><button class="fullscreen-close" onclick="this.parentElement.remove()"><i class="bi bi-x-lg"></i></button>`;
    document.body.appendChild(o);
}

// ====================== Drag & Drop ======================

function setupDragDrop() {
    const u = document.getElementById('uploadArea'); if (!u) return;
    ['dragenter','dragover','dragleave','drop'].forEach(e=>u.addEventListener(e,ev=>{ev.preventDefault();ev.stopPropagation()},false));
    ['dragenter','dragover'].forEach(e=>u.addEventListener(e,()=>{u.style.borderColor='white';u.style.background='rgba(255,255,255,0.1)';}));
    ['dragleave','drop'].forEach(e=>u.addEventListener(e,()=>{u.style.borderColor='rgba(255,255,255,0.3)';u.style.background='transparent';}));
    u.addEventListener('drop', e => uploadDocument(e.dataTransfer.files[0]));
}

// ====================== Utilities ======================

function scrollToBottom() { const c = document.getElementById('chatContainer'); if (c) c.scrollTop = c.scrollHeight; }
function esc(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

function showNotification(msg) {
    const n = document.createElement('div');
    n.style.cssText = 'position:fixed;top:20px;right:20px;background:#22c55e;color:white;padding:12px 24px;border-radius:10px;box-shadow:0 4px 15px rgba(0,0,0,0.2);z-index:9999;animation:slideIn .3s ease';
    n.textContent = msg; document.body.appendChild(n);
    setTimeout(()=>{ n.style.animation='slideOut .3s ease'; setTimeout(()=>n.remove(),300); },2000);
}

const _s = document.createElement('style');
_s.textContent = '@keyframes slideIn{from{transform:translateX(100%);opacity:0}to{transform:translateX(0);opacity:1}}@keyframes slideOut{from{transform:translateX(0);opacity:1}to{transform:translateX(100%);opacity:0}}';
document.head.appendChild(_s);

// ====================== Skills Management ======================

let _skillsCache = [];

function toggleSkillsPanel() {
    document.getElementById('skillsSection')?.classList.toggle('open');
}

async function loadSkills() {
    try {
        const res = await fetch('/api/skills');
        const data = await res.json();
        _skillsCache = data.skills || [];
        renderSkillsList();
    } catch (e) {
        console.warn('Failed to load skills:', e);
    }
}

function renderSkillsList() {
    const list = document.getElementById('skillsList');
    if (!list) return;
    if (_skillsCache.length === 0) {
        list.innerHTML = '<div style="padding:8px 10px;font-size:11px;color:rgba(255,255,255,0.35)">暂无 Skill，点击 + 创建</div>';
        return;
    }
    list.innerHTML = _skillsCache.map(s => `
        <div class="skill-item" data-id="${esc(s.skill_id)}">
            <div class="skill-toggle ${s.enabled ? 'active' : ''}" onclick="event.stopPropagation(); toggleSkillEnabled('${esc(s.skill_id)}', ${!s.enabled})" title="${s.enabled ? '已启用' : '已禁用'}"></div>
            <div class="skill-info" onclick="openSkillEditor('${esc(s.skill_id)}')">
                <div class="skill-name">${esc(s.name)}</div>
                <div class="skill-desc">${esc(s.description || '无描述')}</div>
            </div>
            <button class="skill-edit-btn" onclick="event.stopPropagation(); openSkillEditor('${esc(s.skill_id)}')" title="编辑">
                <i class="bi bi-pencil"></i>
            </button>
        </div>
    `).join('');
}

async function toggleSkillEnabled(skillId, enabled) {
    try {
        await fetch(`/api/skills/${skillId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        const skill = _skillsCache.find(s => s.skill_id === skillId);
        if (skill) skill.enabled = enabled;
        renderSkillsList();
    } catch (e) {
        console.error('Toggle skill failed:', e);
    }
}

function openSkillEditor(skillId) {
    const modal = new bootstrap.Modal(document.getElementById('skillEditorModal'));
    const titleEl = document.getElementById('skillEditorTitle');
    const nameEl = document.getElementById('skillEditorName');
    const descEl = document.getElementById('skillEditorDesc');
    const contentEl = document.getElementById('skillEditorContent');
    const idEl = document.getElementById('skillEditorId');
    const deleteBtn = document.getElementById('skillDeleteBtn');
    const previewPane = document.getElementById('skillPreviewPane');
    if (previewPane) previewPane.style.display = 'none';

    if (skillId) {
        const skill = _skillsCache.find(s => s.skill_id === skillId);
        if (!skill) return;
        titleEl.textContent = '编辑 Skill';
        nameEl.value = skill.name;
        descEl.value = skill.description || '';
        contentEl.value = skill.content || '';
        idEl.value = skill.skill_id;
        deleteBtn.style.display = 'inline-flex';
    } else {
        titleEl.textContent = '新建 Skill';
        nameEl.value = '';
        descEl.value = '';
        contentEl.value = '';
        idEl.value = '';
        deleteBtn.style.display = 'none';
    }
    modal.show();
}

async function saveSkill() {
    const idEl = document.getElementById('skillEditorId');
    const name = document.getElementById('skillEditorName')?.value?.trim();
    const description = document.getElementById('skillEditorDesc')?.value?.trim();
    const content = document.getElementById('skillEditorContent')?.value?.trim();
    const skillId = idEl?.value;

    if (!name) { alert('请输入 Skill 名称'); return; }

    try {
        if (skillId) {
            await fetch(`/api/skills/${skillId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description, content })
            });
        } else {
            await fetch('/api/skills', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description, content })
            });
        }
        bootstrap.Modal.getInstance(document.getElementById('skillEditorModal'))?.hide();
        await loadSkills();
        showNotification(skillId ? 'Skill 已更新' : 'Skill 已创建');
    } catch (e) {
        console.error('Save skill failed:', e);
        alert('保存失败: ' + e.message);
    }
}

async function deleteCurrentSkill() {
    const skillId = document.getElementById('skillEditorId')?.value;
    if (!skillId) return;
    if (!confirm('确定要删除此 Skill 吗？')) return;
    try {
        await fetch(`/api/skills/${skillId}`, { method: 'DELETE' });
        bootstrap.Modal.getInstance(document.getElementById('skillEditorModal'))?.hide();
        await loadSkills();
        showNotification('Skill 已删除');
    } catch (e) {
        console.error('Delete skill failed:', e);
    }
}

async function uploadSkillFile(file) {
    if (!file || !file.name.endsWith('.md')) {
        alert('请选择 .md 文件');
        return;
    }
    const formData = new FormData();
    formData.append('file', file);
    try {
        const res = await fetch('/api/skills/upload', { method: 'POST', body: formData });
        const data = await res.json();
        if (data.success) {
            await loadSkills();
            showNotification('Skill 上传成功');
        } else {
            alert('上传失败: ' + (data.error || '未知错误'));
        }
    } catch (e) {
        console.error('Upload skill failed:', e);
        alert('上传失败: ' + e.message);
    }
}

function toggleSkillPreview() {
    const pane = document.getElementById('skillPreviewPane');
    const editor = document.getElementById('skillEditorContent');
    if (!pane || !editor) return;
    if (pane.style.display === 'none') {
        pane.style.display = 'block';
        pane.innerHTML = typeof marked !== 'undefined' ? marked.parse(editor.value || '') : esc(editor.value || '');
        document.getElementById('skillPreviewToggle').classList.add('active');
    } else {
        pane.style.display = 'none';
        document.getElementById('skillPreviewToggle').classList.remove('active');
    }
}
