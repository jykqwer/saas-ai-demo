"""把框架和业务异常转换成格式固定、不会泄露敏感信息的 API 错误。"""

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.logging import get_logger


class ErrorResponse(BaseModel):
    """所有公开 API 错误共享的响应模型。"""

    code: str
    message: str
    request_id: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiError(Exception):
    """可以直接返回给客户端的应用错误；message 和 details 必须不含敏感信息。"""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    body = ErrorResponse(
        code=code,
        message=message,
        request_id=request_id,
        details=details or {},
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={"X-Request-ID": request_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    """集中注册错误处理器，保证所有错误响应使用同一格式。"""

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError):
        get_logger().warning(
            "api_error",
            extra={
                "request_id": _request_id(request),
                "method": request.method,
                "route": request.url.path,
                "error_type": exc.__class__.__name__,
                "error_code": exc.code,
            },
        )
        return _response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        # 只返回错误字段路径，不回显原始输入（可能包含敏感内容）。
        errors = [
            {
                "loc": list(error.get("loc", [])),
                "msg": error.get("msg", "invalid"),
            }
            for error in exc.errors()
        ]
        return _response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            details={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException):
        return _response(
            request,
            status_code=exc.status_code,
            code="HTTP_ERROR",
            message=str(exc.detail),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        get_logger().exception(
            "unhandled_error",
            extra={
                "request_id": _request_id(request),
                "method": request.method,
                "route": request.url.path,
                "error_type": exc.__class__.__name__,
            },
        )
        return _response(
            request,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="INTERNAL_ERROR",
            message="An unexpected error occurred.",
        )
