"""进程存活和服务就绪检查。配置数据库时就绪还校验数据库可查询。"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from core.database import DatabaseUnavailableError
from core.errors import ApiError

router = APIRouter(prefix="/health", tags=["health"])


class LivenessResponse(BaseModel):
    status: Literal["ok"]
    service: str
    checked_at: datetime


class ReadinessResponse(BaseModel):
    status: Literal["ready"]
    service: str
    checked_at: datetime
    checks: dict[str, Literal["ok"]]


@router.get(
    "/live",
    response_model=LivenessResponse,
    status_code=status.HTTP_200_OK,
    summary="Check whether the API process is alive",
)
async def liveness(request: Request) -> LivenessResponse:
    settings = request.app.state.settings
    return LivenessResponse(
        status="ok",
        service=settings.app_name,
        checked_at=datetime.now(timezone.utc),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    summary="Check whether the API is ready to receive traffic",
)
async def readiness(request: Request) -> ReadinessResponse:
    settings = request.app.state.settings
    checks: dict[str, Literal["ok"]] = {"application": "ok"}

    database = getattr(request.app.state, "database", None)
    if database is not None:
        try:
            await database.ping()
        except DatabaseUnavailableError:
            # 公开错误只说明依赖种类，不暴露数据库地址、驱动消息或 SQL。
            raise ApiError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="DEPENDENCY_UNAVAILABLE",
                message="A required service dependency is unavailable.",
                details={"dependency": "database"},
            ) from None
        checks["database"] = "ok"

    if getattr(request.app.state, "llm_client", None) is not None:
        checks["llm"] = "ok"

    return ReadinessResponse(
        status="ready",
        service=settings.app_name,
        checked_at=datetime.now(timezone.utc),
        checks=checks,
    )
