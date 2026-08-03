"""会话管理接口：列表、历史消息、删除。"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from core.auth import AuthenticatedUser
from core.errors import ApiError
from domain.session import ChatSession, StoredMessage

router = APIRouter(prefix="/sessions", tags=["sessions"])


class SessionSummary(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class MessageOut(BaseModel):
    id: UUID
    session_id: UUID
    role: str
    content: str
    provider: str | None = None
    model: str | None = None
    mock: bool = False
    sources: dict | None = None
    created_at: datetime


class SessionMessagesResponse(BaseModel):
    session: SessionSummary
    messages: list[MessageOut]


class DeleteResponse(BaseModel):
    deleted: bool


def _session_out(session: ChatSession) -> SessionSummary:
    return SessionSummary(
        id=session.id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=session.message_count,
    )


def _message_out(message: StoredMessage) -> MessageOut:
    return MessageOut(
        id=message.id,
        session_id=message.session_id,
        role=message.role,
        content=message.content,
        provider=message.provider,
        model=message.model,
        mock=message.mock,
        sources=message.sources,
        created_at=message.created_at,
    )


@router.get(
    "",
    response_model=list[SessionSummary],
    status_code=status.HTTP_200_OK,
    summary="List recent conversation sessions",
)
async def list_sessions(
    request: Request, user: AuthenticatedUser
) -> list[SessionSummary]:
    repo = request.app.state.chat_repository
    sessions = await repo.list_sessions(owner_user_id=user.id)
    return [_session_out(s) for s in sessions]


@router.get(
    "/{session_id}",
    response_model=SessionMessagesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get messages of a session",
)
async def get_session(
    request: Request,
    session_id: UUID,
    user: AuthenticatedUser,
) -> SessionMessagesResponse:
    repo = request.app.state.chat_repository
    session = await repo.get_session(session_id=session_id, owner_user_id=user.id)
    if session is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="SESSION_NOT_FOUND",
            message="The conversation session does not exist.",
        )
    messages = await repo.list_messages(session_id=session_id)
    return SessionMessagesResponse(
        session=_session_out(session),
        messages=[_message_out(m) for m in messages],
    )


@router.delete(
    "/{session_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a session",
)
async def delete_session(
    request: Request,
    session_id: UUID,
    user: AuthenticatedUser,
) -> DeleteResponse:
    repo = request.app.state.chat_repository
    deleted = await repo.delete_session(session_id=session_id, owner_user_id=user.id)
    return DeleteResponse(deleted=deleted)
