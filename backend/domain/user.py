"""用户、审批状态与问答配额的领域模型。"""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

UserRole = Literal["superuser", "user"]
UserStatus = Literal["pending", "approved", "rejected"]


class UserAccount(BaseModel):
    """认证与授权所需的最小用户信息。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    username: str
    role: UserRole
    status: UserStatus
    created_at: datetime
    approved_at: datetime | None = None


class QuotaSnapshot(BaseModel):
    """普通用户某个自然日的问答用量；superuser 使用 unlimited=True。"""

    model_config = ConfigDict(extra="forbid")

    date: date
    used: int
    limit: int | None
    remaining: int | None
    unlimited: bool = False
