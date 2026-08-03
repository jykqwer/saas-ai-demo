"""应用结构化日志配置。

参考 zhaocai 的约定：只输出白名单字段，Token、请求体或 LLM 原始载荷绝不进入日志。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

APP_LOGGER_NAME = "saas-ai"
_STRUCTURED_FIELDS = (
    "request_id",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "error_type",
    "provider",
    "model",
    "mock",
    "latency_ms",
)


class JsonFormatter(logging.Formatter):
    """把应用日志编码为单行 JSON，并忽略非白名单扩展字段。"""

    def __init__(self, *, service: str, environment: str) -> None:
        super().__init__()
        self.service = service
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "service": self.service,
            "environment": self.environment,
        }

        for field_name in _STRUCTURED_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value

        if record.exc_info and "error_type" not in payload:
            payload["error_type"] = record.exc_info[0].__name__

        return json.dumps(payload, ensure_ascii=False)


def configure_logging(*, service: str, environment: str, level: str) -> None:
    """配置应用 logger；控制台输出单行结构化 JSON。"""

    logger = logging.getLogger(APP_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(service=service, environment=environment))
    logger.addHandler(handler)


def get_logger() -> logging.Logger:
    """返回应用 logger；调用方通过 extra 附加白名单字段。"""

    return logging.getLogger(APP_LOGGER_NAME)
