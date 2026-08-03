"""注册、登录、当前用户与 superuser 审批接口。"""

from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.auth import AuthenticatedUser, Superuser, get_quota
from core.errors import ApiError
from core.security import (
    hash_access_token,
    hash_password,
    new_access_token,
    verify_password,
)
from domain.user import QuotaSnapshot, UserAccount, UserStatus

router = APIRouter(tags=["auth"])


class CredentialsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class UserOut(BaseModel):
    id: UUID
    username: str
    role: Literal["superuser", "user"]
    status: UserStatus
    created_at: datetime
    approved_at: datetime | None = None


class RegisterResponse(BaseModel):
    user: UserOut
    message: str


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserOut
    quota: QuotaSnapshot


class MeResponse(BaseModel):
    user: UserOut
    quota: QuotaSnapshot


class MessageResponse(BaseModel):
    message: str


def _user_out(user: UserAccount) -> UserOut:
    return UserOut(**user.model_dump())


@router.post(
    "/auth/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(request: Request, body: CredentialsRequest) -> RegisterResponse:
    user = await request.app.state.auth_repository.create_user(
        username=body.username,
        password_hash=hash_password(body.password),
    )
    if user is None:
        raise ApiError(
            status_code=status.HTTP_409_CONFLICT,
            code="USERNAME_EXISTS",
            message="该用户名已被注册。",
        )
    return RegisterResponse(
        user=_user_out(user),
        message="注册申请已提交，请等待超级管理员审批。",
    )


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: Request, body: CredentialsRequest) -> AuthResponse:
    credentials = await request.app.state.auth_repository.get_credentials(
        username=body.username
    )
    if credentials is None or not verify_password(body.password, credentials[1]):
        raise ApiError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code="INVALID_CREDENTIALS",
            message="用户名或密码错误。",
        )
    user = credentials[0]
    if user.status == "pending":
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="ACCOUNT_PENDING",
            message="账号正在等待超级管理员审批。",
        )
    if user.status == "rejected":
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="ACCOUNT_REJECTED",
            message="注册申请未通过，请联系超级管理员。",
        )

    token = new_access_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        hours=request.app.state.settings.auth_session_ttl_hours
    )
    await request.app.state.auth_repository.create_auth_session(
        user_id=user.id,
        token_hash=hash_access_token(token),
        expires_at=expires_at,
    )
    return AuthResponse(
        access_token=token,
        expires_at=expires_at,
        user=_user_out(user),
        quota=await get_quota(request, user),
    )


@router.get("/auth/me", response_model=MeResponse)
async def me(request: Request, user: AuthenticatedUser) -> MeResponse:
    return MeResponse(user=_user_out(user), quota=await get_quota(request, user))


@router.post("/auth/logout", response_model=MessageResponse)
async def logout(request: Request, user: AuthenticatedUser) -> MessageResponse:
    del user
    token_hash = getattr(request.state, "access_token_hash", None)
    if token_hash:
        await request.app.state.auth_repository.revoke(token_hash=token_hash)
    return MessageResponse(message="已退出登录。")


@router.get("/admin/users", response_model=list[UserOut])
async def list_users(
    request: Request,
    superuser: Superuser,
    status_filter: UserStatus | None = None,
) -> list[UserOut]:
    del superuser
    users = await request.app.state.auth_repository.list_users(status=status_filter)
    return [_user_out(user) for user in users]


async def _set_user_status(
    request: Request,
    user_id: UUID,
    new_status: UserStatus,
    superuser: UserAccount,
) -> UserOut:
    user = await request.app.state.auth_repository.set_status(
        user_id=user_id,
        status=new_status,
        approved_by=superuser.id,
    )
    if user is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="USER_NOT_FOUND",
            message="用户不存在或不能修改该账号。",
        )
    return _user_out(user)


@router.post("/admin/users/{user_id}/approve", response_model=UserOut)
async def approve_user(
    request: Request,
    user_id: UUID,
    superuser: Superuser,
) -> UserOut:
    return await _set_user_status(request, user_id, "approved", superuser)


@router.post("/admin/users/{user_id}/reject", response_model=UserOut)
async def reject_user(
    request: Request,
    user_id: UUID,
    superuser: Superuser,
) -> UserOut:
    return await _set_user_status(request, user_id, "rejected", superuser)
