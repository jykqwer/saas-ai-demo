# 小枢 · SaaS AI 客服/售前助手

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
- 📚 **RAG 知识库检索**：基于 `backend/knowledge_base/*.md` 文档，BM25 本地检索注入回答上下文（零外部 Embedding 依赖），并展示命中的知识库来源
- 🌐 **联网查询**：知识库之外的问题，模型通过 `web_search` 工具自动联网检索并基于结果作答（DuckDuckGo/Wikipedia，无需额外 Key），展示可点击的来源链接
- 🎛 **查询模式**：头部开关「智能（按需联网）/ 始终联网 / 仅知识库」
- 👋 **人工转接**：一键转人工，填写联系方式生成工单
- 🧪 演示模式：无 Key 也能完整体验，界面标注「演示模式」
- 🤖 真实大模型：配置 `LLM_API_KEY` 后接入，上下文裁剪、超时与错误处理
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
| `SAAS_PRODUCT_NAME` | `云枢 CloudHub` | 产品名（进入系统提示词） |
| `SAAS_COMPANY_NAME` | `云枢科技` | 公司名 |
| `DATABASE_URL` | 空（内存仓库） | 会话持久化连接串，必须 `postgresql+psycopg://` |
| `DATABASE_HEALTH_TIMEOUT_SECONDS` | `2.0` | readiness 数据库探测最长等待 |
| `RAG_ENABLED` | `true` | 是否启用知识库检索增强 |
| `RAG_TOP_K` | `3` | 每次检索注入的分块数 |
| `RAG_MIN_SCORE` | `1.0` | 全局检索的最低相关度阈值，过滤明显无关片段；显式按文档过滤时不生效 |
| `RAG_KNOWLEDGE_BASE_DIR` | `knowledge_base` | 知识库 Markdown 目录 |
| `WEB_SEARCH_ENABLED` | `true` | 是否允许模型联网查询 |
| `WEB_SEARCH_PROVIDER` | `duckduckgo` | 搜索提供方：`duckduckgo` / `wikipedia` / `auto` |
| `WEB_SEARCH_MAX_RESULTS` | `5` | 每次联网返回的结果数 |
| `CORS_ALLOW_ORIGINS` | 空 | 逗号分隔的可信浏览器 Origin，禁止通配 |
| `LOG_LEVEL` | `INFO` | 结构化日志级别 |

**RAG 说明**：知识库位于 `backend/knowledge_base/*.md`（产品、价格、部署、安全、API、FAQ）。
启动时加载并按标题分块、建立 BM25 索引；每次提问检索最相关分块注入系统提示词，
让大模型基于真实资料回答（演示模式同样走检索）。切换服务商或换模型只需改 `.env`。

**数据库说明**：未配置 `DATABASE_URL` 时使用内存仓库（本地联调、测试）；
配置后使用 PostgreSQL 持久化（compose 与 k3s 均已内置）。启动后不会自动建表，
需先执行迁移：

```bash
docker compose exec backend alembic -c alembic.ini upgrade head
```

切换到 OpenAI：`LLM_BASE_URL=https://api.openai.com/v1`、`LLM_MODEL=gpt-4o-mini`。

## 生产部署（k3s）

见 [pods/README.md](./pods/README.md)：创建 Secret（LLM + 数据库凭据）→ 构建并推送镜像 →
按「先 PostgreSQL、再迁移 Job、后应用」的顺序部署。

## API 一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/v1/chat` | 非流式对话（`content` + 可选 `session_id`） |
| POST | `/api/v1/chat/stream` | SSE 流式对话（`meta` / `delta` / `done` / `error` 事件） |
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
| GET | `/api/v1/health/live` `ready` | 存活与就绪检查 |

**知识库管理**：前端侧边栏「📚 知识库管理」可在线查看、导入（选文件或粘贴 Markdown）、
删除文档，导入/删除后检索索引即时重建、立即生效。文件名仅允许安全的 `*.md`
（拒绝路径穿越），单文档上限 300KB。
