"""FastAPI 认证/授权依赖与每日问答配额检查。"""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import Depends, Header, Request, status

from core.errors import ApiError
from core.security import hash_access_token
from domain.user import QuotaSnapshot, UserAccount

_DISABLED_AUTH_USER = UserAccount(
    id=UUID(int=0),
    username="test-superuser",
    role="superuser",
    status="approved",
    created_at=datetime.fromtimestamp(0, tz=ZoneInfo("UTC")),
    approved_at=datetime.fromtimestamp(0, tz=ZoneInfo("UTC")),
)


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def require_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> UserAccount:
    settings = request.app.state.settings
    if not settings.auth_enabled:
        request.state.current_user = _DISABLED_AUTH_USER
        return _DISABLED_AUTH_USER

    token = _bearer_token(authorization)
    if token is None:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="AUTH_REQUIRED",
            message="请先登录后再使用此功能。",
        )
    user = await request.app.state.auth_repository.authenticate(
        token_hash=hash_access_token(token)
    )
    if user is None:
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_SESSION",
            message="登录已失效，请重新登录。",
        )
    request.state.current_user = user
    request.state.access_token_hash = hash_access_token(token)
    return user


async def require_superuser(
    request: Request,
    authorization: str | None = Header(default=None),
) -> UserAccount:
    user = await require_user(request, authorization)
    if user.role != "superuser":
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="SUPERUSER_REQUIRED",
            message="只有超级管理员可以执行此操作。",
        )
    return user


AuthenticatedUser = Annotated[UserAccount, Depends(require_user)]
Superuser = Annotated[UserAccount, Depends(require_superuser)]


def quota_date(request: Request):
    timezone = ZoneInfo(request.app.state.settings.quota_timezone)
    return datetime.now(timezone).date()


async def get_quota(request: Request, user: UserAccount) -> QuotaSnapshot:
    today = quota_date(request)
    if user.role == "superuser":
        return QuotaSnapshot(
            date=today,
            used=0,
            limit=None,
            remaining=None,
            unlimited=True,
        )
    limit = request.app.state.settings.user_daily_question_limit
    used = await request.app.state.auth_repository.get_usage(
        user_id=user.id, usage_date=today
    )
    return QuotaSnapshot(
        date=today,
        used=used,
        limit=limit,
        remaining=max(limit - used, 0),
    )


async def consume_question_quota(request: Request, user: UserAccount) -> QuotaSnapshot:
    if user.role == "superuser":
        return await get_quota(request, user)
    today = quota_date(request)
    limit = request.app.state.settings.user_daily_question_limit
    used = await request.app.state.auth_repository.consume_question(
        user_id=user.id,
        usage_date=today,
        limit=limit,
    )
    if used is None:
        raise ApiError(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            code="DAILY_QUOTA_EXCEEDED",
            message=f"今日问答次数已用完（每天 {limit} 次），请明天再试。",
            details={"limit": limit, "remaining": 0, "date": str(today)},
        )
    return QuotaSnapshot(
        date=today,
        used=used,
        limit=limit,
        remaining=max(limit - used, 0),
    )
