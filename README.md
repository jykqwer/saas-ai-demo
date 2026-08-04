# saas-ai-demo · 小枢 AI 客服/售前助手

面向 SaaS 企业的 **AI 对话助手**：访客可直接向助手咨询产品、价格、试用、私有化部署、
数据安全与售后问题。未配置大模型 Key 时自动进入**演示模式**（内置产品知识库关键词回复），
配置 Key 后无缝切换到真实大模型（DeepSeek / OpenAI 等任意 OpenAI 兼容接口）。

技术栈参照 [zhaocai 项目](../zhaocai)：FastAPI 分层后端 + React 19/Vite 前端，
本地 Docker Compose（含 PostgreSQL）、生产 k3s 清单。

## 功能

- 💬 聊天界面：用户/助手气泡、加粗渲染、输入引导、自动滚动
- ⚡ **流式输出（SSE）**：助手回复逐字呈现，体验接近真实大模型
- 🧭 引导与快捷问题：欢迎语、5 个售前快捷问题、快捷入口
- 🗂 **会话持久化（PostgreSQL）**：会话与消息落库，刷新/重启不丢失，侧边栏会话历史可回看
- 📚 **混合 RAG**：章节感知分块 + BM25 + TF-IDF 向量召回 + RRF/规则重排，并提供 Recall@K、MRR、拒答准确率评测
- 🌐 **联网查询**：知识库之外的问题，模型通过 `web_search` 工具自动联网检索并基于结果作答（DuckDuckGo/Wikipedia，无需额外 Key），展示可点击的来源链接
- 🎛 **查询模式**：头部开关「智能（按需联网）/ 始终联网 / 仅知识库」
- 👋 **人工转接**：一键转人工，填写联系方式生成工单
- 🧪 演示模式：无 Key 也能完整体验，界面标注「演示模式」
- 🤖 真实大模型：配置 `LLM_API_KEY` 后接入，上下文裁剪、超时与错误处理
- 🧩 **工具平台**：统一注册工具 Schema、执行器和模式权限，LLM 动态决定是否调用 RAG/联网工具
- 🧭 **持久化 Agent 编排**：Run/Step/Event 状态机记录每轮模型与工具调用，可通过 API 查看完整 Trace
- 🖼 **Qwen 图文理解**：配置 `QWEN_API_KEY` 后，包含图片的消息自动路由到 `qwen3.7-plus`
- 🏷 状态徽标：实时显示「已接入大模型 / 演示模式」与模型名
- 🚢 部署：Docker Compose（本地）+ k3s 清单（生产）

## 目录结构

```text
backend/             FastAPI 后端
  core/              配置、LLM 客户端、数据库网关、日志、请求 ID、错误处理
  api/v1/            /api/v1 路由（health、chat/stream/handoff、sessions）
  domain/            聊天领域模型、系统提示词、会话/工单领域与仓库协议
  infrastructure/    SQLAlchemy 数据库与仓库实现（含内存实现）
  migrations/        Alembic 迁移
  tests/             pytest 测试
frontend/            React 19 + Vite 前端
  src/features/chat/ 聊天界面、SSE 客户端、会话列表、转人工弹窗
compose.yaml         Docker Compose（后端 + 前端 + PostgreSQL）
pods/                k3s 部署清单（backend/frontend/postgres + Secret 示例）
.env.example         大模型 Key 与环境变量示例
```

## 快速启动（Docker Compose）

```bash
# 可选：接入真实大模型（不配置则进入演示模式）
cp .env.example .env
# 编辑 .env，填入你的 LLM_API_KEY（DeepSeek/OpenAI 兼容接口）

docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:5173 |
| 后端 | http://localhost:8000 |
| OpenAPI | http://localhost:8000/docs |

## 本地开发

后端：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

后端测试：

```bash
cd backend
python -m pytest tests
```

前端：

```bash
cd frontend
npm install
npm run dev      # 开发服务器，/api 代理到 localhost:8000
npm run lint
npm run build
```

## 大模型接入

后端通过环境变量注入配置，所有配置都有安全默认值：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `LLM_API_KEY` | 空（演示模式） | 大模型 API Key，**不要写入代码或提交到 Git** |
| `LLM_BASE_URL` | `https://api.deepseek.com` | 任意 OpenAI 兼容接口地址 |
| `LLM_MODEL` | `deepseek-chat` | 模型名 |
| `LLM_TIMEOUT_SECONDS` | `60` | 上游调用超时 |
| `LLM_MAX_CONTEXT_TURNS` | `12` | 发送给模型的最近对话轮数上限 |
| `LLM_MAX_RETRIES` | `2` | 遇到 408、425、429、5xx 或网络错误时的最大重试次数 |
| `LLM_RETRY_BASE_DELAY_SECONDS` | `0.5` | 指数退避的初始等待秒数 |
| `QWEN_API_KEY` | 空 | 阿里云百炼 Key；配置后启用图片理解 |
| `QWEN_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 百炼 OpenAI 兼容接口 |
| `QWEN_VISION_MODEL` | `qwen3.7-plus` | 图片消息使用的视觉模型 |
| `SAAS_PRODUCT_NAME` | `云枢 CloudHub` | 产品名（进入系统提示词） |
| `SAAS_COMPANY_NAME` | `云枢科技` | 公司名 |
| `DATABASE_URL` | 空（内存仓库） | 会话持久化连接串，必须 `postgresql+psycopg://` |
| `DATABASE_HEALTH_TIMEOUT_SECONDS` | `2.0` | readiness 数据库探测最长等待 |
| `AUTH_ENABLED` | `true` | 是否启用登录、审批和用户隔离 |
| `USER_DAILY_QUESTION_LIMIT` | `10` | 普通用户每天可提交的问答次数 |
| `QUOTA_TIMEZONE` | `Asia/Shanghai` | 每日额度重置所使用的 IANA 时区 |
| `AUTH_SESSION_TTL_HOURS` | `24` | 登录会话有效小时数 |
| `BOOTSTRAP_SUPERUSER_USERNAME` | 空 | 启动时引导创建的 superuser 用户名 |
| `BOOTSTRAP_SUPERUSER_PASSWORD` | 空 | 引导 superuser 密码，至少 8 位，仅通过 Secret 注入 |
| `RAG_ENABLED` | `true` | 是否启用知识库检索增强 |
| `RAG_TOP_K` | `3` | 每次检索注入的分块数 |
| `RAG_MIN_SCORE` | `1.0` | 全局检索的最低相关度阈值，过滤明显无关片段；显式按文档过滤时不生效 |
| `RAG_KNOWLEDGE_BASE_DIR` | `knowledge_base` | 知识库 Markdown 目录 |
| `RAG_CANDIDATE_K` | `24` | 两路召回进入融合重排的候选数 |
| `RAG_VECTOR_WEIGHT` | `0.4` | RRF 中向量召回通道的权重 |
| `WEB_SEARCH_ENABLED` | `true` | 是否允许模型联网查询 |
| `WEB_SEARCH_PROVIDER` | `tavily` | 搜索提供方：`tavily` / `wikipedia` / `auto` |
| `TAVILY_API_KEY` | 空 | Tavily Search API Key；provider 为 `tavily` 时必需 |
| `WEB_SEARCH_MAX_RESULTS` | `5` | 每次联网返回的结果数 |
| `CORS_ALLOW_ORIGINS` | 空 | 逗号分隔的可信浏览器 Origin，禁止通配 |
| `LOG_LEVEL` | `INFO` | 结构化日志级别 |

**RAG 说明**：知识库位于 `backend/knowledge_base/*.md`（产品、价格、部署、安全、API、FAQ）。
启动时加载并按章节路径分块，同时建立字段加权 BM25 与归一化 TF-IDF 稀疏向量索引；查询先做双路召回，
再通过 RRF、查询覆盖率、标题命中和向量相似度重排；每次提问检索最相关分块注入系统提示词，
让大模型基于真实资料回答（演示模式同样走检索）。切换服务商或换模型只需改 `.env`。

运行可重复的 RAG 质量评测：

```bash
python scripts/evaluate-rag.py
```

评测集为 `backend/rag_evalset.jsonl`，输出 Recall@K、MRR、拒答准确率和检索延迟。

**数据库说明**：未配置 `DATABASE_URL` 时使用内存仓库（本地联调、测试）；
配置后使用 PostgreSQL 持久化（compose 与 k3s 均已内置）。Compose 会在后端启动前
自动执行迁移，也可手工执行：

```bash
docker compose exec backend alembic -c alembic.ini upgrade head
```

## 演示用户服务

- 未登录用户不能发送问题或访问会话。
- 新注册账号状态为 `pending`，需 superuser 在侧边栏「用户审批」中通过后才能登录。
- `user` 每个自然日最多问答 10 次，配额按 `QUOTA_TIMEZONE` 重置；并发请求通过数据库原子计数。
- `superuser` 问答不限次数，并可审批用户及维护知识库。
- Compose 本地演示默认账号为 `demo-admin` / `DemoAdmin123!`；生产环境必须覆盖默认密码。

切换到 OpenAI：`LLM_BASE_URL=https://api.openai.com/v1`、`LLM_MODEL=gpt-4o-mini`。

## 生产部署（k3s）

见 [pods/README.md](./pods/README.md)：创建 Secret（LLM + 数据库凭据）→ 构建并推送镜像 →
按「先 PostgreSQL、再迁移 Job、后应用」的顺序部署。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | 注册普通用户（初始为待审批） |
| POST | `/api/v1/auth/login` | 登录并获取 Bearer Token |
| GET | `/api/v1/auth/me` | 当前用户、角色与今日配额 |
| POST | `/api/v1/auth/logout` | 注销当前 Token |
| GET | `/api/v1/admin/users` | 用户列表（仅 superuser） |
| POST | `/api/v1/admin/users/{id}/approve` | 通过注册申请（仅 superuser） |
| POST | `/api/v1/admin/users/{id}/reject` | 拒绝注册申请（仅 superuser） |
| POST | `/api/v1/chat` | 非流式对话（`content` + 可选 `session_id` / `images`） |
| POST | `/api/v1/chat/stream` | SSE 流式对话（支持 `images`，返回 `meta` / `delta` / `done` / `error`） |
| POST | `/api/v1/chat/handoff` | 创建人工转接工单 |
| GET | `/api/v1/chat/config` | 引导配置（问候语、快捷问题、接入状态） |
| GET | `/api/v1/sessions` | 会话列表 |
| GET | `/api/v1/sessions/{id}` | 会话历史消息 |
| DELETE | `/api/v1/sessions/{id}` | 删除会话 |
| GET | `/api/v1/knowledge/docs` | 知识库文档列表 |
| GET | `/api/v1/knowledge/docs/{name}` | 查看知识库文档原文 |
| POST | `/api/v1/knowledge/docs` | 导入/覆盖文档（`filename` + `content`） |
| DELETE | `/api/v1/knowledge/docs/{name}` | 删除文档 |
| POST | `/api/v1/knowledge/retrieve` | 检索测试：返回命中的分块（来源/标题/相关度），不调用大模型 |
| GET | `/api/v1/runs/{id}` | 查看持久化 Agent Run、模型/工具 Steps 与事件轨迹 |
| GET | `/api/v1/health/live` `ready` | 存活与就绪检查 |

**知识库管理**：前端侧边栏「📚 知识库管理」可在线查看、导入（选文件或粘贴 Markdown）、
删除文档，导入/删除后检索索引即时重建、立即生效。文件名仅允许安全的 `*.md`
（拒绝路径穿越），单文档上限 300KB。
