# SaaS AI Demo 代码阅读与面试准备指南

> 适用方向：后端开发、AI 应用开发、全栈开发。  
> 推荐方法：按业务调用链纵向阅读，不要按目录逐个文件通读。

## 1. 项目定位

这是一个基于 FastAPI、React 和 PostgreSQL 的 SaaS AI 客服系统，主要能力包括：

- SSE 流式对话；
- 会话和消息持久化；
- 混合 RAG 检索；
- Agent 工具调用；
- 联网搜索；
- 文本与视觉模型路由；
- 用户审批、登录和每日配额；
- Agent Run、Step、Event 运行轨迹；
- Docker Compose 和 k3s 部署。

面试前至少要能完整解释下面四条链路：

1. 用户发送消息后，系统如何流式返回答案；
2. 模型如何决定调用知识库或联网搜索；
3. RAG 如何分块、召回、融合、重排和拒答；
4. 登录、会话隔离和每日配额如何保证安全与并发正确性。

## 2. 全局架构

```text
React/Vite 前端
    │
    │ HTTP JSON / SSE
    ▼
FastAPI API 层
    │
    ├── 认证与配额
    ├── 会话管理
    ├── Agent 编排
    │     ├── LLM
    │     ├── RAG 工具
    │     └── Web Search 工具
    └── 知识库管理
          │
          ├── PostgreSQL：用户、会话、消息、工单、Agent Trace
          └── Markdown 文件：知识库原文与内存检索索引
```

## 3. 推荐阅读顺序

### 阶段一：建立整体认识

阅读：

1. [`README.md`](../README.md)
2. [`compose.yaml`](../compose.yaml)
3. [`backend/main.py`](../backend/main.py)
4. [`backend/core/config.py`](../backend/core/config.py)
5. [`backend/api/v1/__init__.py`](../backend/api/v1/__init__.py)

重点关注：

- 系统由哪些服务构成；
- FastAPI 启动时创建了哪些组件；
- 配置如何从环境变量注入；
- 没有数据库、LLM Key 或搜索 Key 时如何降级；
- 为什么测试可以向 `create_app` 传入替代配置；
- 为什么 LLM、仓库和知识库放在 `app.state`。

完成标准：

- [ ] 能用一分钟介绍项目；
- [ ] 能画出前端、后端、数据库、LLM 和知识库的关系；
- [ ] 能解释演示模式和真实模型模式的差异。

### 阶段二：走通 SSE 聊天主链路

按照请求传播方向阅读：

1. [`frontend/src/features/chat/ChatPage.jsx`](../frontend/src/features/chat/ChatPage.jsx)
2. [`frontend/src/features/chat/chatApi.js`](../frontend/src/features/chat/chatApi.js)
3. [`backend/api/v1/chat.py`](../backend/api/v1/chat.py)
4. [`backend/core/agent.py`](../backend/core/agent.py)
5. [`backend/core/llm.py`](../backend/core/llm.py)
6. [`backend/infrastructure/chat_repository.py`](../backend/infrastructure/chat_repository.py)

调用流程：

```text
用户点击发送
→ 前端插入用户消息和助手占位气泡
→ POST /api/v1/chat/stream
→ 校验 Bearer Token
→ 原子扣减每日额度
→ 创建或加载会话
→ 加载历史并构造 Prompt
→ 选择文本模型或视觉模型
→ 创建 Agent Run
→ 推送 meta 事件
→ 模型/工具循环
→ 推送 delta、search、rag_used、reset 等事件
→ 助手最终消息和来源落库
→ 推送 done 事件
→ 前端结束流式状态并刷新会话列表
```

需要理解的 SSE 事件：

| 事件 | 作用 |
| --- | --- |
| `meta` | 返回会话 ID、Run ID、模型、模式、配额和预检索来源 |
| `delta` | 增量追加助手文本 |
| `search` | 返回联网查询及来源 |
| `rag_used` | 返回 Agent 实际采用的知识库来源 |
| `reset` | 撤回工具调用前产生的临时可见文本 |
| `done` | 标记本轮成功结束 |
| `error` | 在流已经建立后传递业务或上游错误 |

自测问题：

- [ ] 为什么这里使用 `fetch + ReadableStream`，而不是原生 `EventSource`？
- [ ] SSE 帧为什么以空行分隔？
- [ ] 为什么需要保留未解析完的尾部缓冲区？
- [ ] 为什么已经产生可见文本后不能自动重试？
- [ ] `reset` 事件解决了什么问题？
- [ ] 为什么 SSE 内部异常通常仍表现为 HTTP 200 加 `error` 事件？

### 阶段三：理解 Agent 与工具平台

阅读：

1. [`backend/core/agent.py`](../backend/core/agent.py)
2. [`backend/core/tools.py`](../backend/core/tools.py)
3. [`backend/domain/chat.py`](../backend/domain/chat.py)
4. [`backend/core/llm.py`](../backend/core/llm.py)
5. [`backend/core/model_gateway.py`](../backend/core/model_gateway.py)
6. [`backend/domain/agent.py`](../backend/domain/agent.py)
7. [`backend/infrastructure/agent_repository.py`](../backend/infrastructure/agent_repository.py)
8. [`backend/tests/test_agent_platform.py`](../backend/tests/test_agent_platform.py)

Agent 循环：

```text
调用模型
  ├── 没有工具调用 → 保存最终结果 → Run completed
  └── 存在工具调用
        → 验证工具名称、参数和模式权限
        → 持久化 Tool Step
        → 执行 RAG 或联网搜索
        → 将结果追加为 tool 消息
        → 再次调用模型
```

重点设计：

- 模型负责选择工具，编排器负责权限、预算、执行和持久化；
- 工具 Schema、执行器和允许模式统一注册；
- 最大工具轮数限制可以防止无限循环；
- Run、Step、Event 让一次回答可以被追踪和排障；
- 文本模型和视觉模型通过 `ModelGateway` 路由；
- 某些兼容模型会把工具调用输出成 XML/DSML 文本，系统需要过滤、解析和去重；
- `reasoning_content` 在部分推理模型的工具调用回传中不能丢失。

查询模式：

| 模式 | 知识库 | 联网 | 决策方式 |
| --- | --- | --- | --- |
| `auto` | 可用 | 可用 | 模型动态选择工具 |
| `knowledge` | 预先检索 | 禁止 | 只根据知识库回答 |
| `web` | 不预注入 | 强制预搜索 | 根据网络结果回答 |

完成标准：

- [ ] 能画出两轮“模型 → 工具 → 模型”的时序图；
- [ ] 能解释为什么工具执行权限不能只靠 Prompt；
- [ ] 能解释 Agent Trace 对排障、审计和评测的价值；
- [ ] 能说明如何增加一个新工具。

### 阶段四：掌握 RAG

先读设计报告，再读实现：

1. [`docs/rag-improvement-report.md`](./rag-improvement-report.md)
2. [`backend/infrastructure/knowledge_base.py`](../backend/infrastructure/knowledge_base.py)
3. [`backend/api/v1/knowledge.py`](../backend/api/v1/knowledge.py)
4. [`scripts/evaluate-rag.py`](../scripts/evaluate-rag.py)
5. [`scripts/benchmark-rag.py`](../scripts/benchmark-rag.py)
6. [`backend/tests/test_rag_retrieval.py`](../backend/tests/test_rag_retrieval.py)

检索流程：

```text
Markdown 文档
→ 按 # / ## / ### 构造完整标题路径
→ 超长章节按段落继续分块
→ 中文二元组、英文单词和复合编号分词
→ 建立字段加权 BM25 倒排索引
→ 建立 Feature Hashing TF-IDF 稀疏向量
→ 两路召回候选
→ RRF 融合
→ 查询覆盖率、标题命中、BM25 和向量相似度重排
→ 相关度及覆盖率过滤
→ 返回 Top-K
```

必须掌握的概念：

- BM25 中 TF、IDF、文档长度归一化的作用；
- 文档名、标题和正文为什么需要不同权重；
- 为什么 BM25 分数和余弦相似度不适合直接相加；
- RRF 为什么对不同召回通道的分数尺度不敏感；
- 查询扩展为什么使用低权重；
- 为什么除了 Recall，还要评估错误召回和拒答准确率；
- 为什么将建索引计算前移适合读多写少的知识库。

需要诚实说明的边界：

- 当前所谓“向量召回”是 TF-IDF 稀疏向量，不是 Embedding 语义检索；
- 规则没有覆盖的同义表达、跨语言问题和隐含意图仍可能召回失败；
- 10 MiB、20,122 个分块来自合成基准，不代表真实生产流量；
- 大规模生产环境可以演进为 BM25、Embedding、RRF、Reranker 的组合。

完成标准：

- [ ] 能白板说明一次查询如何得到 Top-K；
- [ ] 能解释 Recall@K、MRR、拒答准确率和 P50/P95；
- [ ] 能讲清优化前的问题、采取的措施、量化结果和代价；
- [ ] 能提出下一阶段的语义检索架构。

### 阶段五：认证、配额与数据库

阅读：

1. [`backend/domain/user.py`](../backend/domain/user.py)
2. [`backend/domain/session.py`](../backend/domain/session.py)
3. [`backend/core/auth.py`](../backend/core/auth.py)
4. [`backend/core/security.py`](../backend/core/security.py)
5. [`backend/api/v1/auth.py`](../backend/api/v1/auth.py)
6. [`backend/infrastructure/database.py`](../backend/infrastructure/database.py)
7. [`backend/infrastructure/auth_repository.py`](../backend/infrastructure/auth_repository.py)
8. [`backend/infrastructure/chat_repository.py`](../backend/infrastructure/chat_repository.py)
9. [`backend/migrations/versions`](../backend/migrations/versions)
10. [`backend/tests/test_auth.py`](../backend/tests/test_auth.py)

重点设计：

- 新用户注册后必须由 superuser 审批；
- 数据库只保存访问令牌的哈希；
- 会话查询和删除都附带 `owner_user_id`，防止越权访问；
- superuser 不受每日问题配额限制；
- 配额日期按业务时区计算；
- PostgreSQL 使用原子 Upsert 防止并发超扣；
- Domain 使用 `Protocol` 定义仓库契约；
- Infrastructure 提供 PostgreSQL 和内存两套实现；
- Alembic 是生产数据库结构变更入口。

原子配额的核心思想：

```sql
INSERT ...
ON CONFLICT (...) DO UPDATE
SET question_count = question_count + 1
WHERE question_count < limit
RETURNING question_count;
```

它把“检查额度”和“增加计数”合并为一条数据库语句，避免两个并发请求同时通过检查。

自测问题：

- [ ] 为什么不能先 `SELECT`，再在 Python 中判断并 `UPDATE`？
- [ ] 为什么访问 Token 不能明文落库？
- [ ] 用户被拒绝后为什么要撤销登录会话？
- [ ] 内存仓库和数据库仓库分别适合什么场景？
- [ ] 为什么不能用 ORM 模型自动建表替代 Alembic 生产迁移？

### 阶段六：前端交互

阅读：

1. [`frontend/src/App.jsx`](../frontend/src/App.jsx)
2. [`frontend/src/features/auth/AuthPage.jsx`](../frontend/src/features/auth/AuthPage.jsx)
3. [`frontend/src/features/chat/ChatPage.jsx`](../frontend/src/features/chat/ChatPage.jsx)
4. [`frontend/src/features/chat/chatApi.js`](../frontend/src/features/chat/chatApi.js)
5. [`frontend/src/features/chat/ChatMessage.jsx`](../frontend/src/features/chat/ChatMessage.jsx)
6. [`frontend/src/features/chat/KnowledgeModal.jsx`](../frontend/src/features/chat/KnowledgeModal.jsx)

重点关注：

- Token 的保存和请求头注入；
- 首次加载时如何恢复登录状态；
- 为什么使用 `activeSessionRef` 保存当前会话；
- 流式增量如何只更新最后一个助手占位气泡；
- 为什么弹窗通过修改 `key` 强制重新挂载；
- 会话历史如何恢复 RAG 与 Web 来源；
- 图片如何转换为 Data URL 并传给后端。

前端不是面试重点时，不需要逐行阅读 CSS，能解释状态流转和 SSE 解析即可。

### 阶段七：测试、可观测性与部署

阅读：

1. [`backend/tests`](../backend/tests)
2. [`backend/core/request_id.py`](../backend/core/request_id.py)
3. [`backend/core/request_logging.py`](../backend/core/request_logging.py)
4. [`backend/core/errors.py`](../backend/core/errors.py)
5. [`backend/api/v1/health.py`](../backend/api/v1/health.py)
6. [`pods/README.md`](../pods/README.md)
7. [`pods/backend-migration.yaml`](../pods/backend-migration.yaml)

推荐优先阅读的测试：

- `test_stream_mock_reply`：SSE 事件顺序与消息落库；
- `test_stream_round_does_not_retry_after_visible_delta`：流式重试边界；
- `test_agent_persists_dynamic_model_and_tool_steps`：Agent 状态机；
- `test_daily_question_limit_is_atomic`：并发配额；
- `test_irrelevant_question_returns_no_results`：RAG 拒答；
- `test_model_gateway_routes_images_to_qwen`：多模态路由。

部署自测问题：

- [ ] liveness 和 readiness 有什么区别？
- [ ] 为什么部署顺序是 PostgreSQL、迁移 Job、后端、前端？
- [ ] 为什么数据库密码和模型 Key 必须通过 Secret 注入？
- [ ] 为什么后端关闭时需要释放 HTTP 客户端和数据库连接池？
- [ ] 多副本部署时，本地知识库文件会出现什么一致性问题？

## 4. 五天学习计划

### 第一天：系统全貌

- [ ] 阅读 README、Compose、`main.py` 和配置；
- [ ] 启动项目并操作登录、聊天、会话和知识库；
- [ ] 打开 `/docs` 查看 API；
- [ ] 画一张系统组件图；
- [ ] 写出一分钟项目介绍。

### 第二天：聊天主链路

- [ ] 从 `ChatPage.send` 跟到 `/chat/stream`；
- [ ] 列出全部 SSE 事件；
- [ ] 跟踪一次用户消息和助手消息的落库过程；
- [ ] 画聊天时序图；
- [ ] 阅读聊天和 LLM 重试测试。

### 第三天：Agent 与模型

- [ ] 阅读 Agent 循环和工具注册中心；
- [ ] 跟踪一次 RAG 工具调用；
- [ ] 跟踪一次 Web Search 工具调用；
- [ ] 理解 DSML/XML 工具文本过滤；
- [ ] 理解文本模型和视觉模型路由；
- [ ] 设计一个新增工具的方案。

### 第四天：RAG

- [ ] 阅读 RAG 优化报告；
- [ ] 手推一次 BM25、TF-IDF 和 RRF 流程；
- [ ] 运行 RAG 评测；
- [ ] 分析一个正确召回和一个拒答案例；
- [ ] 准备 RAG 优化的 STAR 讲述。

### 第五天：数据、安全与部署

- [ ] 画核心数据库 ER 图；
- [ ] 理解认证、审批和配额；
- [ ] 阅读 Alembic 迁移；
- [ ] 阅读健康检查和 k3s 清单；
- [ ] 整理项目不足和生产化方案；
- [ ] 完成一次模拟项目问答。

## 5. 阅读代码的方法

每阅读一个模块，都回答以下问题：

1. 输入是什么？
2. 输出是什么？
3. 状态保存在哪里？
4. 依赖从哪里获得？
5. 正常路径是什么？
6. 失败路径是什么？
7. 并发情况下是否正确？
8. 为什么这样设计？
9. 有什么代价和改进空间？
10. 哪些测试证明了预期行为？

建议使用“问题驱动”阅读：

```text
先提出问题
→ 用 rg 查找入口
→ 沿函数调用向下阅读
→ 再阅读对应测试
→ 最后用自己的话画图或复述
```

常用命令：

```bash
# 查找文件
rg --files backend frontend/src

# 查找函数或类
rg -n '^(class|def|async def) ' backend

# 查找 API 路由
rg -n '@router\.' backend/api

# 查找某个事件
rg -n '"(meta|delta|reset|done|error)"' backend frontend/src

# 后端测试
cd backend
python3 -m pytest tests -q

# 单独运行重点测试
python3 -m pytest tests/test_llm.py -q
python3 -m pytest tests/test_agent_platform.py -q
python3 -m pytest tests/test_rag_retrieval.py -q

# RAG 评测
cd ..
python3 scripts/evaluate-rag.py

# 前端检查
cd frontend
npm run lint
npm run build
```

## 6. 面试高频问题

### 为什么选择 SSE，而不是 WebSocket？

当前主要需求是服务端向客户端单向推送模型增量。SSE 协议简单、基于 HTTP、容易经过反向代理，也便于按事件类型处理。用户消息仍通过普通 POST 发送，不需要维持双向实时通道。如果后续需要语音双工、实时打断或多人协作，再考虑 WebSocket。

### 如何避免流式重试导致重复回答？

只在尚未向前端发送可见文本时重试。产生任何可见增量后发生断连，就返回错误，不再重新调用模型，否则用户会收到重复的回答前缀。

### Agent 如何防止无限调用工具？

编排器限制最大工具轮数，同时由工具注册中心验证工具名称、参数和模式权限。超过轮数后将 Run 标记为失败，并返回明确错误。

### 为什么使用 RRF？

BM25 分数和向量余弦相似度的尺度不同，直接加权相加需要困难的归一化和调参。RRF 使用排名而不是原始分数融合，对不同召回器的分数范围不敏感。

### 如何解决 RAG 幻觉？

- 使用相关度和查询覆盖率过滤无关分块；
- Prompt 明确资料边界和拒答要求；
- 保存并展示来源；
- 为无答案问题建立评测集；
- 生产化时增加 Reranker、引用正确率和回答忠实度评测。

### 如何保证每日额度不会被并发请求突破？

不在应用层执行“查询后更新”，而是使用 PostgreSQL 条件 Upsert，把判断和递增放进一条原子 SQL。

### 为什么同时提供内存仓库和 PostgreSQL 仓库？

内存实现适合测试和零依赖演示，PostgreSQL 实现负责真实持久化。业务层依赖仓库协议而不是 ORM，使测试替换依赖更容易。

### 如何排查一次 Agent 回答为什么失败？

先通过 Request ID 定位请求日志，再根据响应中的 Run ID 查询 Agent Trace，查看模型 Step、工具 Step、事件顺序、输入摘要、延迟和失败码。

## 7. 可以主动指出的改进空间

面试中不要只讲优点。下面的问题很适合作为生产化讨论：

1. 配额在调用模型前扣减，上游失败时不会自动返还；
2. 用户消息先于模型结果落库，失败后可能形成没有助手回复的会话；
3. 知识库导入会同步重建全部索引，可能阻塞 FastAPI 事件循环；
4. 多 Pod 使用本地知识库和进程内索引时存在副本一致性问题；
5. `LLM_MAX_CONTEXT_TURNS` 实际裁剪的是消息数量，命名和行为存在偏差；
6. 图片参与当前模型请求，但没有作为完整附件持久化；
7. 前端没有 `AbortController`，用户不能主动停止生成；
8. 缺少前端组件测试和 SSE 解析单元测试；
9. Agent 只有轮数预算，缺少 Token、费用和总耗时预算；
10. 知识库索引没有版本化、原子切换和持久化机制。

生产化改造可以包括：

- 为问答请求增加幂等键和明确的消息状态；
- 区分额度预占、确认和补偿；
- 将索引构建移入后台任务或独立检索服务；
- 使用对象存储保存文档，使用版本号发布索引；
- 引入 pgvector 或 Qdrant、Embedding 和 Reranker；
- 增加超时、Token、费用和工具调用预算；
- 增加 OpenTelemetry 指标与 Trace；
- 增加真实客服问题评测集和线上反馈闭环。

## 8. 面试讲述模板

### 60 秒项目介绍

> 这是一个面向 SaaS 售前和客服场景的 AI 对话系统。前端使用 React，后端使用 FastAPI，通过 SSE 提供流式回答；PostgreSQL 保存用户、会话、消息、配额以及 Agent 执行轨迹。AI 层支持 OpenAI 兼容模型，通过 Agent 动态选择内部知识库和联网搜索工具，图片消息则路由到视觉模型。知识库使用章节感知分块、字段加权 BM25、TF-IDF 稀疏向量、RRF 融合和规则重排，并通过 Recall、MRR、拒答准确率和延迟进行评测。项目也处理了流式重试、工具协议泄漏、并发配额和来源持久化等工程问题。

### RAG STAR 模板

- **Situation**：初版 BM25 在小知识库中可用，但标题无法命中、口语问题召回失败，大文档查询还会重复建索引；
- **Task**：在不引入外部 Embedding 服务的前提下提高准确率并降低查询延迟和内存；
- **Action**：建立确定性评测集，增加章节路径、字段加权、低权重查询扩展、复合编号归一化、预构建索引、TF-IDF 稀疏向量、RRF 融合和覆盖率拒答，并补充离线评测；
- **Result**：合成基准中 Top-1 从 5% 提升到 100%，查询 P50 相比基线下降约 90%（约 1 秒 → 混合检索约 104ms），峰值内存下降约 42%；离线评测 Recall@3、MRR、拒答准确率均为 1.0；代价是两阶段加载建索引时间上升。

## 9. 最终检查清单

- [ ] 一分钟内介绍项目背景、技术栈和核心能力；
- [ ] 手画系统架构图；
- [ ] 手画一次 SSE 对话时序图；
- [ ] 手画 Agent 工具调用循环；
- [ ] 手画 RAG 检索流程；
- [ ] 解释数据库核心表及关系；
- [ ] 讲清一个并发问题及解决方案；
- [ ] 讲清一个线上故障场景及排查方法；
- [ ] 使用 STAR 讲述一次 RAG 优化；
- [ ] 主动说明至少三个项目局限；
- [ ] 给出合理的生产化演进方案；
- [ ] 不把合成基准描述成真实生产数据。

