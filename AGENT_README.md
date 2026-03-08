# PageIndex Chat UI — Agent 增强版改进文档

## 概述

本次改进将 PageIndex Chat UI 从一个**线性管道式 RAG 问答系统**升级为一个**具备 Agent 能力的智能文档分析系统**。改进涵盖五大方向，核心理念是让系统从"被动回答"进化为"主动思考、多步推理、自我纠错"。

---

## 改进方向总览

| 方向 | 名称 | 描述 | 核心文件 |
|------|------|------|----------|
| 1 | ReAct 循环 | 思考→行动→观察的迭代推理 | `services/agent.py` |
| 2 | 多工具 Agent | 5 种工具自主选择调用 | `services/tools/` |
| 3 | 查询分解 | 复杂问题拆解为子问题 | `services/agent.py` → `decompose_query()` |
| 4 | 自我反思 | 回答后自评，低质量自动重试 | `services/agent.py` → `reflect()` |
| 5 | 主动分析 | 文档上传后自动生成分析报告 | `services/agent.py` → `analyze_document()` |

---

## 方向 1：ReAct 循环（Think → Act → Observe）

### 改进前
```
用户提问 → 一次树搜索 → 直接生成回答
```

### 改进后
```
用户提问 → Agent 思考 → 选择工具 → 观察结果 → 再思考 → ... → 信息充足 → 生成回答
```

### 实现细节

Agent 的核心循环在 `services/agent.py` 的 `run()` 方法中：

1. **Think（思考）**：Agent 收到问题后，结合文档树结构和已收集的信息，推理下一步应该做什么。
2. **Act（行动）**：Agent 选择一个工具执行，例如搜索树结构、阅读某个节点、搜索关键词等。
3. **Observe（观察）**：工具返回结果，Agent 将结果加入已知信息池。
4. **循环**：重复以上步骤，直到 Agent 认为信息充足（选择 `final_answer` 工具），最多循环 5 次。

### 前端展示

聊天界面新增 **Agent 推理时间线**，每一步显示：
- 步骤编号和使用的工具名称
- Agent 的思考内容
- 工具返回的观察结果

---

## 方向 2：多工具 Agent

### 工具列表

| 工具 | 文件 | 功能 |
|------|------|------|
| `tree_search` | `services/tools/tree_search.py` | 在文档树结构中搜索与问题相关的节点 |
| `read_node` | `services/tools/node_reader.py` | 阅读指定节点的完整正文 |
| `keyword_search` | `services/tools/keyword_search.py` | 在所有节点中搜索精确关键词 |
| `view_pages` | `services/tools/page_viewer.py` | 查看节点对应的页面信息和图片可用性 |
| `summarize_nodes` | `services/tools/summarizer.py` | 使用 LLM 为指定节点生成摘要 |

### 工具架构

所有工具继承自 `BaseTool` 基类（`services/tools/base.py`），统一接口：

```python
class BaseTool(ABC):
    name: str
    description: str
    parameters_schema: dict

    async def execute(self, params: dict, context: dict) -> dict:
        """执行工具，返回包含 'summary' 和 'nodes' 的字典"""
        ...
```

工具通过 `ToolRegistry` 注册和管理，Agent 根据 LLM 的决策自动选择和调用工具。

### 扩展方式

要添加新工具，只需：
1. 在 `services/tools/` 下新建文件，继承 `BaseTool`
2. 实现 `execute()` 方法
3. 在 `services/agent.py` 的 `_register_tools()` 中注册

---

## 方向 3：查询分解

### 工作原理

当用户提出复杂问题时（如比较、多方面分析、需要跨章节信息），Agent 会：

1. **分析问题复杂度**：通过 LLM 判断问题是否需要分解
2. **生成子问题**：将原始问题拆分为 2-4 个更具体的子问题
3. **确定综合策略**：选择 `compare`（比较）、`aggregate`（汇总）、`sequence`（顺序）或 `direct`（直接）策略
4. **逐个解答**：对每个子问题分别运行 ReAct 循环
5. **综合回答**：将所有子问题的答案综合成一个完整回答

### 示例

**原始问题**：「论文提出的方法和 baseline 相比，在性能上有什么优势？」

**分解结果**：
- 子问题 1：「论文提出的方法的性能指标是什么？」
- 子问题 2：「baseline 方法的性能指标是什么？」
- 综合策略：`compare`

### 前端展示

当问题被分解时，界面显示**问题分解面板**，列出所有子问题和选择的综合策略。

---

## 方向 4：自我反思

### 工作原理

Agent 生成回答后，会进行一次自我评估：

1. **评分**：对回答质量打 1-10 分
2. **发现问题**：列出回答中可能存在的问题
3. **决策**：
   - 评分 ≥ 6 分 → `accept`（接受），返回当前回答
   - 评分 < 6 分 → `retry`（重试），根据缺失信息补充检索后重新生成回答

### 重试机制

当触发重试时，Agent 会：
1. 根据反思中发现的"缺失信息"构造新的检索查询
2. 运行额外的 ReAct 循环收集补充信息
3. 用更丰富的上下文重新生成回答
4. 最多重试 1 次，避免无限循环

### 前端展示

聊天界面显示**自我检查面板**，包含评分（带颜色标识）和发现的问题。

---

## 方向 5：主动文档分析

### 工作原理

文档索引完成后，Agent 自动分析文档结构，生成：

1. **文档摘要**：2-3 句话概述文档内容
2. **关键发现**：3-5 个文档的主要发现/贡献
3. **主要主题**：文档涉及的核心主题
4. **建议提问**：5 个用户可能想问的问题

### 触发时机

在 `routes/api.py` 的文档上传流程中，当文档状态变为 `ready` 后自动触发分析。分析结果保存为 `results/<doc_id>/analysis.json`。

### 前端展示

- 选择一个已就绪的文档时，聊天区域自动显示**文档智能分析面板**
- 建议提问以可点击按钮形式展示，点击后自动填入输入框并发送

### API

```
GET /api/documents/<doc_id>/analysis
```

返回：
```json
{
  "analysis": {
    "summary": "...",
    "key_findings": ["...", "..."],
    "main_topics": ["...", "..."],
    "suggested_questions": ["...", "..."]
  }
}
```

---

## 新增文件结构

```
services/
├── agent.py                  # Agent 核心 (ReAct + 分解 + 反思 + 分析)
├── tools/
│   ├── __init__.py           # 工具包入口
│   ├── base.py               # 工具基类 & 注册器
│   ├── tree_search.py        # 树搜索工具
│   ├── node_reader.py        # 节点阅读工具
│   ├── keyword_search.py     # 关键词搜索工具
│   ├── page_viewer.py        # 页面查看工具
│   └── summarizer.py         # 摘要生成工具
├── rag_service.py            # 新增 agent_chat_stream / auto_analyze_document
└── indexing_service.py       # 未修改

models/
└── document.py               # 新增 analysis_path 属性 / get_analysis 方法

routes/
├── api.py                    # 新增 GET /api/documents/<doc_id>/analysis
└── socket_handlers.py        # 新增 agent_chat 事件 + agent 标记解析

templates/
└── index.html                # 新增 Agent 模式开关 + 时间线/分解/反思/分析面板样式

static/js/
└── app.js                    # 新增 Agent 事件处理 + UI 渲染逻辑
```

---

## 修改的文件

| 文件 | 修改内容 |
|------|----------|
| `models/document.py` | 新增 `analysis_path` 属性、`get_analysis()` 方法 |
| `services/rag_service.py` | 新增 `agent` 属性、`agent_chat_stream()`、`auto_analyze_document()` |
| `routes/api.py` | 新增分析结果接口、文档准备后自动触发分析 |
| `routes/socket_handlers.py` | 新增 `agent_chat` 事件处理、统一标记解析函数 |
| `templates/index.html` | 新增 Agent 模式开关、时间线/分解/反思/分析面板 CSS |
| `static/js/app.js` | 新增 Agent 事件监听、UI 渲染、分析面板加载 |

---

## 使用说明

### 开启/关闭 Agent 模式

顶部栏新增 **Agent 模式** 开关（默认开启）：
- **开启**：使用 ReAct 循环 + 多工具 + 查询分解 + 自我反思
- **关闭**：使用原有的简单 RAG（单次搜索 + 直接回答）

### Agent 模式的交互流程

1. 上传 PDF 文档，等待索引完成
2. 索引完成后，系统自动生成文档分析报告
3. 选择文档，查看分析报告，可以点击建议提问
4. 输入问题，观察 Agent 的多步推理过程
5. 查看最终回答和自我检查结果

### 性能注意事项

Agent 模式会比简单 RAG 使用**更多的 LLM API 调用**（每个问题约 3-8 次调用），因此：
- API 费用会更高
- 响应时间会更长
- 但回答质量和可解释性显著提升

如果需要快速、低成本的回答，可以关闭 Agent 模式。

---

## 通信协议

### 新增 Socket.IO 事件

| 事件名 | 方向 | 数据 |
|--------|------|------|
| `agent_chat` | 客户端→服务端 | `{doc_id, query, model_type, use_memory}` |
| `agent_step` | 服务端→客户端 | `{step, thought, tool, tool_input, observation}` |
| `agent_decompose` | 服务端→客户端 | `{needs_decomposition, sub_questions, synthesis_strategy}` |
| `agent_reflect` | 服务端→客户端 | `{score, issues, missing_info, action}` |

### 新增流式标记

| 标记 | 含义 |
|------|------|
| `[AGENT_STEP]{...}` | Agent 执行了一个推理步骤 |
| `[AGENT_DECOMPOSE]{...}` | 查询分解结果 |
| `[AGENT_REFLECT]{...}` | 自我反思结果 |
| `[AGENT_RETRY]` | Agent 决定重试 |

---

## 后续扩展建议

1. **多文档跨文档推理**：让 Agent 同时检索多个文档
2. **对话上下文理解**：Agent 基于对话历史自动调整策略
3. **代码执行工具**：支持计算类问题
4. **Web 搜索工具**：补充文档中没有的外部信息
5. **用户反馈闭环**：用户标记回答质量，Agent 学习改进
