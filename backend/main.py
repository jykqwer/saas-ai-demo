"""FastAPI 应用入口：组装配置、LLM 客户端、会话仓库、中间件、错误处理和路由。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1 import router as api_v1_router
from core.config import Settings, get_settings
from core.errors import register_error_handlers
from core.llm import LLMClient
from core.logging import APP_LOGGER_NAME, configure_logging, get_logger
from core.request_id import RequestIdMiddleware
from core.request_logging import RequestLoggingMiddleware
from domain.chat import build_assistant_profile, build_mock_reply
from infrastructure.chat_repository import (
    EphemeralChatRepository,
    SqlAlchemyChatRepository,
)
from infrastructure.database import SqlAlchemyDatabase
from infrastructure.knowledge_base import KnowledgeBase
from infrastructure.web_search import WebSearchClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """进程生命周期钩子：释放 LLM 客户端、数据库连接池与搜索客户端。"""

    yield
    llm_client = getattr(app.state, "llm_client", None)
    if llm_client is not None:
        await llm_client.close()
    database = getattr(app.state, "database", None)
    if database is not None:
        await database.close()
    web_search = getattr(app.state, "web_search", None)
    if web_search is not None:
        await web_search.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建应用；测试可传入替代配置，避免读取真实环境变量或调用外部服务。"""

    resolved_settings = settings or get_settings()
    configure_logging(
        service=APP_LOGGER_NAME,
        environment=resolved_settings.environment,
        level=resolved_settings.log_level,
    )

    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    # LLM 客户端在应用创建时立即初始化：无需等待 lifespan 启动事件，
    # 测试与生产环境都能直接访问；关闭动作由 lifespan 负责。
    assistant_profile = build_assistant_profile(
        product_name=resolved_settings.saas_product_name,
        company_name=resolved_settings.saas_company_name,
    )
    app.state.llm_client = LLMClient(
        api_key=resolved_settings.llm_api_key,
        base_url=resolved_settings.llm_base_url,
        model=resolved_settings.llm_model,
        timeout_seconds=resolved_settings.llm_timeout_seconds,
        max_context_turns=resolved_settings.llm_max_context_turns,
        mock_reply=lambda question, rag_chunks: build_mock_reply(
            question, assistant_profile, rag_chunks
        ),
    )

    # RAG 知识库：启用时加载文档并建立检索索引；失败或禁用时置为 None。
    if resolved_settings.rag_enabled:
        rag = KnowledgeBase(
            resolved_settings.rag_knowledge_base_dir,
            top_k=resolved_settings.rag_top_k,
            min_score=resolved_settings.rag_min_score,
        )
        rag.load()
        app.state.rag = rag if rag.chunk_count > 0 else None
    else:
        app.state.rag = None

    # 网络搜索：启用时允许模型通过 web_search 工具联网查询。
    if resolved_settings.web_search_enabled:
        app.state.web_search = WebSearchClient(
            provider=resolved_settings.web_search_provider,
            timeout_seconds=resolved_settings.web_search_timeout_seconds,
            max_results=resolved_settings.web_search_max_results,
        )
    else:
        app.state.web_search = None

    # 会话仓库：配置 DATABASE_URL 时使用 PostgreSQL 持久化，否则用内存实现。
    if resolved_settings.database_url is not None:
        database = SqlAlchemyDatabase(resolved_settings.database_url)
        app.state.database = database
        app.state.chat_repository = SqlAlchemyChatRepository(
            database.session_factory_any
        )
    else:
        app.state.database = None
        app.state.chat_repository = EphemeralChatRepository()

    if resolved_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(resolved_settings.cors_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # 先加请求 ID，再加访问日志，保证访问日志能读到同一 request_id。
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(api_v1_router)
    register_error_handlers(app)

    get_logger().info(
        "app_started",
        extra={
            "environment": resolved_settings.environment,
            "model": resolved_settings.llm_model,
            "mock": not resolved_settings.llm_configured,
            "persistence": "postgresql" if resolved_settings.database_url else "memory",
        },
    )
    return app


app = create_app()
