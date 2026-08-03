"""ChatRepository 的两个实现：

- SqlAlchemyChatRepository：PostgreSQL 持久化（生产）
- EphemeralChatRepository：内存实现（本地联调、测试；重启即失）
"""

import asyncio
from collections import defaultdict
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.session import (
    MAX_SESSION_MESSAGES,
    ChatSession,
    HandoffTicket,
    StoredMessage,
    utcnow,
)
from infrastructure.database import (
    ChatMessageRow,
    ChatSessionRow,
    HandoffTicketRow,
)


def _to_session(row: ChatSessionRow, message_count: int) -> ChatSession:
    return ChatSession(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        message_count=message_count,
    )


def _to_message(row: ChatMessageRow) -> StoredMessage:
    return StoredMessage(
        id=row.id,
        session_id=row.session_id,
        role=row.role,  # type: ignore[arg-type]
        content=row.content,
        provider=row.provider,
        model=row.model,
        mock=row.mock,
        created_at=row.created_at,
    )


class SqlAlchemyChatRepository:
    """基于 PostgreSQL 的会话仓库。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_session(self, *, title: str) -> ChatSession:
        async with self._session_factory() as session:
            row = ChatSessionRow(title=title)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_session(row, 0)

    async def get_session(self, *, session_id: UUID) -> ChatSession | None:
        async with self._session_factory() as session:
            row = await session.get(ChatSessionRow, session_id)
            if row is None:
                return None
            count = (
                await session.execute(
                    select(func.count(ChatMessageRow.id)).where(
                        ChatMessageRow.session_id == session_id
                    )
                )
            ).scalar_one()
            return _to_session(row, int(count))

    async def list_sessions(self, *, limit: int = 50) -> list[ChatSession]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(ChatSessionRow).order_by(
                        ChatSessionRow.updated_at.desc()
                    ).limit(limit)
                )
            ).scalars().all()
            counts = dict(
                (
                    await session.execute(
                        select(
                            ChatMessageRow.session_id,
                            func.count(ChatMessageRow.id),
                        ).group_by(ChatMessageRow.session_id)
                    )
                ).all()
            )
            return [
                _to_session(row, int(counts.get(row.id, 0))) for row in rows
            ]

    async def delete_session(self, *, session_id: UUID) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(ChatSessionRow).where(ChatSessionRow.id == session_id)
            )
            await session.commit()
            return result.rowcount > 0

    async def append_message(
        self,
        *,
        session_id: UUID,
        role: Literal["user", "assistant"],
        content: str,
        provider: str | None = None,
        model: str | None = None,
        mock: bool = False,
    ) -> StoredMessage:
        async with self._session_factory() as session:
            row = ChatMessageRow(
                session_id=session_id,
                role=role,
                content=content,
                provider=provider,
                model=model,
                mock=mock,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_message(row)

    async def list_messages(
        self, *, session_id: UUID, limit: int = MAX_SESSION_MESSAGES
    ) -> list[StoredMessage]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(ChatMessageRow)
                    .where(ChatMessageRow.session_id == session_id)
                    .order_by(ChatMessageRow.created_at.asc())
                    .limit(limit)
                )
            ).scalars().all()
            return [_to_message(row) for row in rows]

    async def touch_session(self, *, session_id: UUID) -> None:
        async with self._session_factory() as session:
            row = await session.get(ChatSessionRow, session_id)
            if row is not None:
                row.updated_at = utcnow()
                await session.commit()

    async def create_ticket(
        self,
        *,
        session_id: UUID,
        contact_name: str,
        contact_type: Literal["email", "wechat", "phone"],
        contact_value: str,
        subject: str,
    ) -> HandoffTicket:
        async with self._session_factory() as session:
            row = HandoffTicketRow(
                session_id=session_id,
                contact_name=contact_name,
                contact_type=contact_type,
                contact_value=contact_value,
                subject=subject,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return HandoffTicket(
                id=row.id,
                session_id=row.session_id,
                contact_name=row.contact_name,
                contact_type=row.contact_type,  # type: ignore[arg-type]
                contact_value=row.contact_value,
                subject=row.subject,
                status=row.status,  # type: ignore[arg-type]
                created_at=row.created_at,
            )

    async def list_tickets(self, *, session_id: UUID) -> list[HandoffTicket]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(HandoffTicketRow)
                    .where(HandoffTicketRow.session_id == session_id)
                    .order_by(HandoffTicketRow.created_at.desc())
                )
            ).scalars().all()
            return [
                HandoffTicket(
                    id=row.id,
                    session_id=row.session_id,
                    contact_name=row.contact_name,
                    contact_type=row.contact_type,  # type: ignore[arg-type]
                    contact_value=row.contact_value,
                    subject=row.subject,
                    status=row.status,  # type: ignore[arg-type]
                    created_at=row.created_at,
                )
                for row in rows
            ]


class EphemeralChatRepository:
    """内存会话仓库；进程内保持数据，重启后清空。

    用于未配置 DATABASE_URL 的本地联调与测试，接口与 SQLAlchemy 实现一致。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[UUID, dict[str, object]] = {}
        self._messages: dict[UUID, list[StoredMessage]] = defaultdict(list)
        self._tickets: dict[UUID, list[HandoffTicket]] = defaultdict(list)

    async def create_session(self, *, title: str) -> ChatSession:
        now = utcnow()
        session_id = uuid4()
        async with self._lock:
            self._sessions[session_id] = {
                "id": session_id,
                "title": title,
                "created_at": now,
                "updated_at": now,
            }
        return ChatSession(
            id=session_id,
            title=title,
            created_at=now,
            updated_at=now,
            message_count=0,
        )

    async def get_session(self, *, session_id: UUID) -> ChatSession | None:
        data = self._sessions.get(session_id)
        if data is None:
            return None
        return ChatSession(
            id=data["id"],
            title=data["title"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            message_count=len(self._messages[session_id]),
        )

    async def list_sessions(self, *, limit: int = 50) -> list[ChatSession]:
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: s["updated_at"],
            reverse=True,
        )[:limit]
        return [
            ChatSession(
                id=s["id"],
                title=s["title"],
                created_at=s["created_at"],
                updated_at=s["updated_at"],
                message_count=len(self._messages[s["id"]]),
            )
            for s in sessions
        ]

    async def delete_session(self, *, session_id: UUID) -> bool:
        async with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
            self._messages.pop(session_id, None)
            self._tickets.pop(session_id, None)
        return existed

    async def append_message(
        self,
        *,
        session_id: UUID,
        role: Literal["user", "assistant"],
        content: str,
        provider: str | None = None,
        model: str | None = None,
        mock: bool = False,
    ) -> StoredMessage:
        message = StoredMessage(
            id=uuid4(),
            session_id=session_id,
            role=role,
            content=content,
            provider=provider,
            model=model,
            mock=mock,
            created_at=utcnow(),
        )
        async with self._lock:
            self._messages[session_id].append(message)
        return message

    async def list_messages(
        self, *, session_id: UUID, limit: int = MAX_SESSION_MESSAGES
    ) -> list[StoredMessage]:
        return list(self._messages[session_id])[-limit:]

    async def touch_session(self, *, session_id: UUID) -> None:
        data = self._sessions.get(session_id)
        if data is not None:
            data["updated_at"] = utcnow()

    async def create_ticket(
        self,
        *,
        session_id: UUID,
        contact_name: str,
        contact_type: Literal["email", "wechat", "phone"],
        contact_value: str,
        subject: str,
    ) -> HandoffTicket:
        ticket = HandoffTicket(
            id=uuid4(),
            session_id=session_id,
            contact_name=contact_name,
            contact_type=contact_type,
            contact_value=contact_value,
            subject=subject,
            created_at=utcnow(),
        )
        async with self._lock:
            self._tickets[session_id].append(ticket)
        return ticket

    async def list_tickets(self, *, session_id: UUID) -> list[HandoffTicket]:
        return list(self._tickets[session_id])
