"""从环境变量构建不可变应用配置，并在启动阶段拒绝危险配置。

参考 zhaocai 的配置约定：配置不可变、布尔值严格解析、CORS 禁止通配、
显式校验外部依赖配置（本项目的 LLM 端点）。
"""

import os
from dataclasses import dataclass
from functools import lru_cache

DEFAULT_APP_NAME = "SaaS AI Assistant API"
DEFAULT_ENVIRONMENT = "local"
DEFAULT_LOG_LEVEL = "INFO"
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
DEFAULT_CORS_ORIGINS: tuple[str, ...] = ()

# LLM 默认值：未配置 LLM_API_KEY 时进入内置演示模式，无需任何外部依赖即可联调。
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-chat"
DEFAULT_LLM_TIMEOUT_SECONDS = 60.0
DEFAULT_LLM_MAX_CONTEXT_TURNS = 12
DEFAULT_SAAS_PRODUCT_NAME = "云枢 CloudHub"
DEFAULT_SAAS_COMPANY_NAME = "云枢科技"
DEFAULT_DATABASE_HEALTH_TIMEOUT_SECONDS = 2.0


def _parse_origins(raw_origins: str | None) -> tuple[str, ...]:
    """把逗号分隔的 Origin 转为不可变元组，忽略空白项。"""

    if raw_origins is None:
        return DEFAULT_CORS_ORIGINS
    return tuple(origin.strip() for origin in raw_origins.split(",") if origin.strip())


def _parse_bool(name: str, raw_value: str | None, default: bool = False) -> bool:
    """严格解析布尔配置；拼写错误直接报错，避免安全开关被意外改变。"""

    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _parse_float(name: str, raw_value: str | None, default: float) -> float:
    if raw_value is None:
        return default
    try:
        parsed = float(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    return parsed


def _parse_int(name: str, raw_value: str | None, default: int) -> int:
    if raw_value is None:
        return default
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    return parsed


@dataclass(frozen=True, slots=True)
class Settings:
    """进程级配置；不可变设计避免运行期间被请求代码意外修改。"""

    app_name: str = DEFAULT_APP_NAME
    environment: str = DEFAULT_ENVIRONMENT
    log_level: str = DEFAULT_LOG_LEVEL
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS

    # LLM 接入配置。API Key 不在代码中保存，只通过环境变量注入。
    llm_api_key: str | None = None
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_max_context_turns: int = DEFAULT_LLM_MAX_CONTEXT_TURNS

    # SaaS 产品与公司信息，用于构建系统提示词和前端引导。
    saas_product_name: str = DEFAULT_SAAS_PRODUCT_NAME
    saas_company_name: str = DEFAULT_SAAS_COMPANY_NAME

    # 会话持久化。未配置 DATABASE_URL 时使用内存仓库（重启即失），
    # 配置后使用 PostgreSQL 持久化。
    database_url: str | None = None
    database_health_timeout_seconds: float = DEFAULT_DATABASE_HEALTH_TIMEOUT_SECONDS

    # RAG：对知识库 Markdown 文档做本地检索，注入回答上下文。
    rag_enabled: bool = True
    rag_top_k: int = 3
    # BM25 字符大粒匹配噪音较多，阈值只过滤明显无关（0.9 以下）片段。
    rag_min_score: float = 1.0
    rag_knowledge_base_dir: str = "knowledge_base"

    # 网络搜索：让模型在知识库之外也能通过工具调用联网查询。
    web_search_enabled: bool = True
    web_search_provider: str = "tavily"
    tavily_api_key: str | None = None
    web_search_max_results: int = 5
    web_search_timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        normalized_log_level = self.log_level.upper()
        if normalized_log_level not in VALID_LOG_LEVELS:
            raise ValueError(f"Unsupported log level: {self.log_level}")
        object.__setattr__(self, "log_level", normalized_log_level)

        # 禁止通配 Origin，避免任意网站跨域访问 API。
        if "*" in self.cors_origins:
            raise ValueError("CORS wildcard origins are not allowed")

        if self.llm_timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be positive")
        if self.llm_max_context_turns <= 0:
            raise ValueError("LLM_MAX_CONTEXT_TURNS must be positive")
        if self.web_search_provider not in {"tavily", "wikipedia", "auto"}:
            raise ValueError(
                f"Unsupported web search provider: {self.web_search_provider}"
            )

        # 显式指定 Psycopg 3 方言，确保异步运行时和同步迁移使用同一种驱动。
        if self.database_url is not None and not self.database_url.startswith(
            "postgresql+psycopg://"
        ):
            # 错误消息刻意不回显可能包含凭据的连接字符串。
            raise ValueError("DATABASE_URL must use the postgresql+psycopg scheme")
        if self.database_health_timeout_seconds <= 0:
            raise ValueError("DATABASE_HEALTH_TIMEOUT_SECONDS must be positive")

        # 配置了 Key 时必须同时给出合法的 HTTPS 端点。
        if self.llm_api_key:
            if not self.llm_base_url.startswith(("http://", "https://")):
                raise ValueError("LLM_BASE_URL must be an http(s) URL")
        # 未配置 Key 时进入演示模式，base_url 仍须是合法 URL 以便展示接入示例。
        elif not self.llm_base_url.startswith(("http://", "https://")):
            raise ValueError("LLM_BASE_URL must be an http(s) URL")

    @property
    def llm_configured(self) -> bool:
        """是否配置了真实大模型接入；未配置时使用内置演示模式。"""

        return bool(self.llm_api_key)


def _build_settings() -> Settings:
    """从环境变量读取全部配置；缺失项使用默认值。"""

    return Settings(
        app_name=os.getenv("APP_NAME", DEFAULT_APP_NAME),
        environment=os.getenv("APP_ENV", DEFAULT_ENVIRONMENT),
        log_level=os.getenv("LOG_LEVEL", DEFAULT_LOG_LEVEL),
        cors_origins=_parse_origins(os.getenv("CORS_ALLOW_ORIGINS")),
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_base_url=os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL).rstrip("/"),
        llm_model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
        llm_timeout_seconds=_parse_float(
            "LLM_TIMEOUT_SECONDS", os.getenv("LLM_TIMEOUT_SECONDS"), DEFAULT_LLM_TIMEOUT_SECONDS
        ),
        llm_max_context_turns=_parse_int(
            "LLM_MAX_CONTEXT_TURNS",
            os.getenv("LLM_MAX_CONTEXT_TURNS"),
            DEFAULT_LLM_MAX_CONTEXT_TURNS,
        ),
        saas_product_name=os.getenv("SAAS_PRODUCT_NAME", DEFAULT_SAAS_PRODUCT_NAME),
        saas_company_name=os.getenv("SAAS_COMPANY_NAME", DEFAULT_SAAS_COMPANY_NAME),
        database_url=os.getenv("DATABASE_URL") or None,
        database_health_timeout_seconds=_parse_float(
            "DATABASE_HEALTH_TIMEOUT_SECONDS",
            os.getenv("DATABASE_HEALTH_TIMEOUT_SECONDS"),
            DEFAULT_DATABASE_HEALTH_TIMEOUT_SECONDS,
        ),
        rag_enabled=_parse_bool("RAG_ENABLED", os.getenv("RAG_ENABLED"), True),
        rag_top_k=_parse_int("RAG_TOP_K", os.getenv("RAG_TOP_K"), 3),
        rag_min_score=_parse_float(
            "RAG_MIN_SCORE", os.getenv("RAG_MIN_SCORE"), 1.0
        ),
        rag_knowledge_base_dir=os.getenv(
            "RAG_KNOWLEDGE_BASE_DIR", "knowledge_base"
        ),
        web_search_enabled=_parse_bool(
            "WEB_SEARCH_ENABLED", os.getenv("WEB_SEARCH_ENABLED"), True
        ),
        web_search_provider=os.getenv(
            "WEB_SEARCH_PROVIDER", "tavily"
        ),
        tavily_api_key=os.getenv("TAVILY_API_KEY") or None,
        web_search_max_results=_parse_int(
            "WEB_SEARCH_MAX_RESULTS", os.getenv("WEB_SEARCH_MAX_RESULTS"), 5
        ),
        web_search_timeout_seconds=_parse_float(
            "WEB_SEARCH_TIMEOUT_SECONDS",
            os.getenv("WEB_SEARCH_TIMEOUT_SECONDS"),
            10.0,
        ),
    )


@lru_cache
def get_settings() -> Settings:
    """缓存配置；进程内所有请求共享同一份不可变配置。"""

    return _build_settings()
