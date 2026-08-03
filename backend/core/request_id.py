"""为每个 HTTP 请求建立可安全传播的关联 ID。"""

import re
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _new_request_id() -> str:
    return f"req_{uuid4().hex}"


def _resolve_request_id(candidate: str | None) -> str:
    """只接受长度和字符集受限的调用方 ID，其他值由服务端替换。"""

    if candidate and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return _new_request_id()


ASGIApp = Callable[[Scope, Receive, Send], Awaitable[Any]]


class RequestIdMiddleware:
    """把同一个请求 ID 写入应用上下文和所有 HTTP 响应头。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = _resolve_request_id(headers.get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = MutableHeaders(scope=message)
                response_headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        await self.app(scope, receive, send_wrapper)
