"""记录每个 HTTP 请求的方法、路由、状态码与耗时；不记录请求体。"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.types import Message, Receive, Scope, Send

from core.logging import get_logger


class RequestLoggingMiddleware:
    """为成功的请求记录结构化访问日志。错误响应由错误处理器记录。"""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[Any]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            get_logger().info(
                "http_request",
                extra={
                    "request_id": scope.get("state", {}).get("request_id"),
                    "method": scope.get("method"),
                    "route": scope.get("path"),
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 1),
                },
            )
