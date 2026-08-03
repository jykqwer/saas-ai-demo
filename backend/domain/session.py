"""会话、消息与人工转接工单的领域模型及仓库协议。

仓库接口抽象了持久化细节：配置了 PostgreSQL 时使用 SQLAlchemy 实现，
未配置时使用内存实现（便于本地联调与测试）。
"""

from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

MAX_SESSION_TITLE_CHARS = 120
MAX_TICKET_SUBJECT_CHARS = 200
MAX_CONTACT_VALUE_CHARS = 120
MAX_SESSION_MESSAGES = 200


def utcnow() -> datetime:
    """生成统一的 UTC 时间戳。"""

    return datetime.now(timezone.utc)


class ChatSession(BaseModel):
    """一次对话会话。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class StoredMessage(BaseModel):
    """已持久化的一条消息。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    session_id: UUID
    role: Literal["user", "assistant"]
    content: str
    provider: str | None = None
    model: str | None = None
    mock: bool = False
    # 助手消息采用的来源（{"rag": [...], "web": [...], "web_query": "..."}）
    sources: dict | None = None
    created_at: datetime


class HandoffTicket(BaseModel):
    """人工转接工单。"""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    session_id: UUID
    contact_name: str
    contact_type: Literal["email", "wechat", "phone"]
    contact_value: str
    subject: str = ""
    status: Literal["open", "resolved"] = "open"
    created_at: datetime


def default_title(content: str) -> str:
    """从首条用户消息生成会话标题。"""

    text = content.strip().replace("\n", " ")
    if len(text) > MAX_SESSION_TITLE_CHARS:
        text = text[:MAX_SESSION_TITLE_CHARS].rstrip() + "…"
    return text or "新对话"


class ChatRepository(Protocol):
    """会话仓库协议；两个实现都必须满足这些方法。"""

    async def create_session(
        self, *, title: str, owner_user_id: UUID
    ) -> ChatSession: ...

    async def get_session(
        self, *, session_id: UUID, owner_user_id: UUID
    ) -> ChatSession | None: ...

    async def list_sessions(
        self, *, owner_user_id: UUID, limit: int = 50
    ) -> list[ChatSession]: ...

    async def delete_session(
        self, *, session_id: UUID, owner_user_id: UUID
    ) -> bool: ...

    async def append_message(
        self,
        *,
        session_id: UUID,
        role: Literal["user", "assistant"],
        content: str,
        provider: str | None = None,
        model: str | None = None,
        mock: bool = False,
        sources: dict | None = None,
    ) -> StoredMessage: ...

    async def list_messages(
        self, *, session_id: UUID, limit: int = MAX_SESSION_MESSAGES
    ) -> list[StoredMessage]: ...

    async def touch_session(self, *, session_id: UUID) -> None: ...

    async def create_ticket(
        self,
        *,
        session_id: UUID,
        contact_name: str,
        contact_type: Literal["email", "wechat", "phone"],
        contact_value: str,
        subject: str,
    ) -> HandoffTicket: ...

    async def list_tickets(self, *, session_id: UUID) -> list[HandoffTicket]: ...
