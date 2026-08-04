"""AI 客服/售前助手的聊天接口、流式输出与人工转接接口。"""

import base64
import binascii
import json
from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.agent import AgentExecutionError, AgentOrchestrator
from core.auth import AuthenticatedUser, consume_question_quota
from core.errors import ApiError
from core.llm import ChatProviderError
from domain.chat import (
    MAX_MESSAGE_CHARS,
    AssistantProfile,
    build_assistant_profile,
    build_system_prompt,
    format_rag_context,
    format_web_context,
)
from domain.session import (
    MAX_CONTACT_VALUE_CHARS,
    MAX_TICKET_SUBJECT_CHARS,
    default_title,
)

router = APIRouter(prefix="/chat", tags=["chat"])
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def _sse(payload: dict) -> str:
    """把事件对象编码为 SSE data 帧。"""

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _merge_source_rows(
    existing: list[dict], incoming: list[dict], *, keys: tuple[str, ...]
) -> list[dict]:
    """按稳定字段合并来源；重复项更新内容，同时保持首次出现顺序。"""

    merged = list(existing)
    positions = {
        tuple(str(row.get(key, "")) for key in keys): index
        for index, row in enumerate(merged)
    }
    for row in incoming:
        identity = tuple(str(row.get(key, "")) for key in keys)
        index = positions.get(identity)
        if index is None:
            positions[identity] = len(merged)
            merged.append(row)
        else:
            merged[index] = row
    return merged


class ChatRequest(BaseModel):
    """单轮聊天请求：只携带本条用户消息，历史由服务端从仓库加载。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    session_id: UUID | None = None
    images: list[str] = Field(default_factory=list, max_length=4)
    # auto=智能（模型按需联网）；web=始终联网；knowledge=仅知识库
    mode: Literal["auto", "web", "knowledge"] = "auto"

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must contain non-whitespace characters")
        return normalized

    @field_validator("images")
    @classmethod
    def validate_images(cls, images: list[str]) -> list[str]:
        validated: list[str] = []
        for image in images:
            if len(image) > 7_000_000:
                raise ValueError("each image must be smaller than 5 MB")
            if not image.startswith(
                (
                    "data:image/png;base64,",
                    "data:image/jpeg;base64,",
                    "data:image/webp;base64,",
                )
            ):
                raise ValueError("images must be PNG, JPEG, or WebP data URLs")
            try:
                encoded = image.split(",", 1)[1]
                decoded = base64.b64decode(encoded, validate=True)
            except (IndexError, binascii.Error, ValueError) as exc:
                raise ValueError("images must contain valid base64 data") from exc
            if len(decoded) > MAX_IMAGE_BYTES:
                raise ValueError("each image must be smaller than 5 MB")
            validated.append(image)
        return validated


class ChatResponse(BaseModel):
    """非流式响应；携带会话 ID 与展示信息。"""

    reply: str
    session_id: UUID
    model: str
    provider: str
    mock: bool
    latency_ms: int


class HandoffRequest(BaseModel):
    """人工转接工单请求。"""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID | None = None
    contact_name: str = Field(min_length=1, max_length=60)
    contact_type: Literal["email", "wechat", "phone"]
    contact_value: str = Field(min_length=1, max_length=MAX_CONTACT_VALUE_CHARS)
    subject: str = Field(default="", max_length=MAX_TICKET_SUBJECT_CHARS)

    @field_validator("contact_name", "contact_value")
    @classmethod
    def normalize_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("must contain non-whitespace characters")
        return normalized

    @field_validator("subject")
    @classmethod
    def normalize_subject(cls, value: str) -> str:
        return value.strip()


class HandoffResponse(BaseModel):
    """转人工成功响应。"""

    ticket_id: UUID
    session_id: UUID
    message: str


class ChatConfigResponse(BaseModel):
    """前端引导配置：问候语、快捷问题、接入状态。"""

    product_name: str
    company_name: str
    assistant_name: str
    greeting: str
    configured: bool
    provider: str
    model: str
    vision_configured: bool = False
    vision_model: str | None = None
    rag_docs: int = 0
    rag_chunks: int = 0
    quick_questions: list[dict[str, str]]


def _repository(request: Request):
    return request.app.state.chat_repository


def _rag_engine(request: Request):
    return getattr(request.app.state, "rag", None)


def _build_system_prompt(
    request: Request, content: str, chunks: list | None = None
) -> str:
    settings = request.app.state.settings
    prompt = build_system_prompt(
        product_name=settings.saas_product_name,
        company_name=settings.saas_company_name,
        timezone_name=settings.quota_timezone,
        current_time=datetime.now(ZoneInfo(settings.quota_timezone)),
    )
    # 追加知识库参考资料；chunks 为 None 时现场检索（无预检索的场景兜底）。
    if chunks is None:
        rag = _rag_engine(request)
        chunks = rag.retrieve(content) if rag is not None else []
    prompt += format_rag_context(chunks)
    return prompt


async def _resolve_session(
    request: Request,
    session_id: UUID | None,
    content: str,
    owner_user_id: UUID,
):
    """解析会话；没有提供 ID 时创建新会话（标题取自首条消息）。"""

    repo = _repository(request)
    if session_id is None:
        return await repo.create_session(
            title=default_title(content), owner_user_id=owner_user_id
        )
    session = await repo.get_session(session_id=session_id, owner_user_id=owner_user_id)
    if session is None:
        raise ApiError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="SESSION_NOT_FOUND",
            message="The conversation session does not exist.",
        )
    return session


async def _build_turns(
    request: Request,
    session_id: UUID,
    content: str,
    *,
    pre_inject_rag: bool = True,
    images: list[str] | None = None,
) -> tuple[list[dict], list]:
    """加载最近上下文并追加当前用户消息，构造带系统提示词与 RAG 上下文的输入。

    只检索一次知识库：结果同时用于系统提示词注入与返回的 rag_chunks。
    pre_inject_rag=False（真实自动/始终联网模式）时不预注入：auto 由模型调用
    retrieve_knowledge_base 工具按需检索，web 为纯联网，避免无关内容污染上下文。
    """

    settings = request.app.state.settings
    repo = _repository(request)
    history = await repo.list_messages(session_id=session_id)

    rag = _rag_engine(request)
    rag_chunks = rag.retrieve(content) if (rag is not None and pre_inject_rag) else []
    system_prompt = _build_system_prompt(request, content, chunks=rag_chunks)

    turns: list[dict] = [{"role": "system", "content": system_prompt}]
    # 保留最近 N 轮（2N 条消息），避免请求体无界增长。
    for message in history[-settings.llm_max_context_turns * 2 :]:
        turns.append({"role": message.role, "content": message.content})
    user_content: str | list[dict]
    if images:
        user_content = [
            *[{"type": "image_url", "image_url": {"url": image}} for image in images],
            {"type": "text", "text": content},
        ]
    else:
        user_content = content
    turns.append({"role": "user", "content": user_content})
    return turns, rag_chunks


async def _persist(
    request: Request,
    *,
    session_id: UUID,
    role: str,
    content: str,
    provider: str | None = None,
    model: str | None = None,
    mock: bool = False,
    sources: dict | None = None,
) -> None:
    repo = _repository(request)
    await repo.append_message(
        session_id=session_id,
        role=role,  # type: ignore[arg-type]
        content=content,
        provider=provider,
        model=model,
        mock=mock,
        sources=sources,
    )
    await repo.touch_session(session_id=session_id)


@router.post(
    "",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Send a chat turn to the assistant",
)
async def chat(
    request: Request,
    body: ChatRequest,
    user: AuthenticatedUser,
) -> ChatResponse:
    """非流式对话：持久化消息并返回助手完整回复。"""

    llm = request.app.state.llm_client
    request_id = getattr(request.state, "request_id", "unknown")
    await consume_question_quota(request, user)
    session = await _resolve_session(request, body.session_id, body.content, user.id)
    turns, rag_chunks = await _build_turns(
        request, session.id, body.content, images=body.images
    )
    await _persist(request, session_id=session.id, role="user", content=body.content)
    try:
        result = await llm.chat(
            messages=turns, request_id=request_id, rag_chunks=rag_chunks
        )
    except ChatProviderError as exc:
        raise ApiError(
            status_code=status.HTTP_502_BAD_GATEWAY,
            code=exc.code,
            message="The AI assistant is temporarily unavailable.",
        ) from exc

    await _persist(
        request,
        session_id=session.id,
        role="assistant",
        content=result.text,
        provider=result.provider,
        model=result.model,
        mock=result.mock,
        sources=(
            {
                "rag": [
                    {
                        "source": c.source,
                        "heading": c.heading,
                        "score": round(c.score, 3),
                    }
                    for c in rag_chunks
                ]
            }
            if rag_chunks
            else None
        ),
    )
    return ChatResponse(
        reply=result.text,
        session_id=session.id,
        model=result.model,
        provider=result.provider,
        mock=result.mock,
        latency_ms=result.latency_ms,
    )


@router.post(
    "/stream",
    response_model=None,
    status_code=status.HTTP_200_OK,
    summary="Stream a chat turn from the assistant (SSE)",
)
async def chat_stream(
    request: Request,
    body: ChatRequest,
    user: AuthenticatedUser,
) -> StreamingResponse:
    """SSE 流式对话：逐段推送助手回复增量，结束时落库。"""

    gateway = request.app.state.llm_client
    request_id = getattr(request.state, "request_id", "unknown")
    quota = await consume_question_quota(request, user)

    async def event_source():
        try:
            session = await _resolve_session(
                request, body.session_id, body.content, user.id
            )
            mode = body.mode or "auto"
            web_search = getattr(request.app.state, "web_search", None)

            # 预注入知识库的条件：演示模式（无工具循环）以及仅知识库模式。
            # 真实自动/始终联网模式不预注入：auto 由模型调用 retrieve_knowledge_base
            # 按需检索，web 为纯联网（只注入搜索结果）。
            pre_inject_rag = (not gateway.configured) or mode == "knowledge"
            turns, rag_chunks = await _build_turns(
                request,
                session.id,
                body.content,
                pre_inject_rag=pre_inject_rag,
                images=body.images,
            )
            await _persist(
                request, session_id=session.id, role="user", content=body.content
            )
            llm = gateway.select(turns)

            # 模式提示词：明确约束模型行为。
            mode_note = {
                "knowledge": (
                    "\n\n【当前模式：仅知识库】请只依据内部知识库和可信运行时上下文回答；"
                    "若两者没有相关信息，如实说明不知道，不要编造或猜测实时信息。"
                ),
                "web": (
                    "\n\n【当前模式：始终联网】请优先使用提供的网络搜索结果回答最新/通用问题，"
                    "并在回答中尽量标注来源。"
                ),
                "auto": "",
            }.get(mode, "")
            if mode_note:
                turns[0] = {**turns[0], "content": turns[0]["content"] + mode_note}

            agent_run = await request.app.state.agent_repository.create_run(
                session_id=session.id,
                owner_user_id=user.id,
                mode=mode,
                input_text=body.content,
            )

            initial_rag_meta = [
                {
                    "source": chunk.source,
                    "heading": chunk.heading,
                    "score": round(chunk.score, 3),
                    "retrieval": getattr(chunk, "retrieval", None),
                }
                for chunk in rag_chunks
            ]

            # 开头先推送元信息（会话 ID、模型、接入状态、RAG 来源）。
            yield _sse(
                {
                    "type": "meta",
                    "session_id": str(session.id),
                    "model": llm.model,
                    "provider": llm.provider,
                    "mock": not llm.configured,
                    "mode": mode,
                    "run_id": str(agent_run.id),
                    "rag": initial_rag_meta,
                    "quota": quota.model_dump(mode="json"),
                }
            )

            initial_web_meta: list[dict] = []
            initial_web_query: str | None = None
            if mode == "web" and web_search is not None:
                results = await web_search.search(body.content)
                initial_web_meta = [
                    {"title": r.title, "url": r.url, "snippet": r.snippet}
                    for r in results
                ]
                initial_web_query = body.content
                if results:
                    turns[0] = {
                        **turns[0],
                        "content": turns[0]["content"] + format_web_context(results),
                    }
                yield _sse(
                    {
                        "type": "search",
                        "query": body.content,
                        "results": initial_web_meta,
                    }
                )

            orchestrator = AgentOrchestrator(
                repository=request.app.state.agent_repository,
                tools=request.app.state.tool_registry,
            )
            async for event in orchestrator.run_stream(
                run_id=agent_run.id,
                llm=llm,
                messages=turns,
                request_id=request_id,
                mode=mode,
                rag_chunks=rag_chunks,
                tools_enabled=mode == "auto",
                initial_rag=initial_rag_meta,
                initial_web=initial_web_meta,
                initial_web_query=initial_web_query,
            ):
                if event["type"] != "agent_complete":
                    yield _sse(event)
                    continue
                await _persist(
                    request,
                    session_id=session.id,
                    role="assistant",
                    content=event["reply"],
                    provider=llm.provider,
                    model=llm.model,
                    mock=not llm.configured,
                    sources=event["sources"],
                )
                yield _sse(
                    {
                        "type": "done",
                        "session_id": str(session.id),
                        "run_id": str(agent_run.id),
                        "model": llm.model,
                        "provider": llm.provider,
                        "mock": not llm.configured,
                        "mode": mode,
                        "web": event["web"] or mode == "web",
                        "web_count": event["web_count"],
                        "rag_count": event["rag_count"],
                        "quota": quota.model_dump(mode="json"),
                    }
                )
        except ChatProviderError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
        except AgentExecutionError as exc:
            yield _sse({"type": "error", "code": exc.code, "message": exc.message})
        except ApiError as exc:
            yield _sse(
                {
                    "type": "error",
                    "code": exc.code,
                    "message": exc.message,
                    "status": exc.status_code,
                }
            )
        except Exception:  # noqa: BLE001 - SSE 生成器必须兜底任何异常并转为 error 事件
            yield _sse(
                {
                    "type": "error",
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred.",
                }
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/handoff",
    response_model=HandoffResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a human-handoff ticket",
)
async def handoff(
    request: Request,
    body: HandoffRequest,
    user: AuthenticatedUser,
) -> HandoffResponse:
    """创建人工转接工单；未提供会话时自动开一个。"""

    repo = _repository(request)
    if body.session_id is None:
        session = await repo.create_session(title="人工转接咨询", owner_user_id=user.id)
        session_id = session.id
    else:
        session = await repo.get_session(
            session_id=body.session_id, owner_user_id=user.id
        )
        if session is None:
            raise ApiError(
                status_code=status.HTTP_404_NOT_FOUND,
                code="SESSION_NOT_FOUND",
                message="The conversation session does not exist.",
            )
        session_id = session.id

    ticket = await repo.create_ticket(
        session_id=session_id,
        contact_name=body.contact_name,
        contact_type=body.contact_type,
        contact_value=body.contact_value,
        subject=body.subject,
    )
    return HandoffResponse(
        ticket_id=ticket.id,
        session_id=session_id,
        message="已转人工客服，我们将在工作时间内尽快与你联系。",
    )


@router.get(
    "/config",
    response_model=ChatConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the assistant onboarding configuration",
)
async def chat_config(request: Request) -> ChatConfigResponse:
    """返回前端渲染欢迎页与状态徽标所需的信息。"""

    settings = request.app.state.settings
    llm = request.app.state.llm_client
    profile: AssistantProfile = build_assistant_profile(
        product_name=settings.saas_product_name,
        company_name=settings.saas_company_name,
    )
    rag = getattr(request.app.state, "rag", None)

    return ChatConfigResponse(
        product_name=profile.product_name,
        company_name=profile.company_name,
        assistant_name=profile.assistant_name,
        greeting=profile.greeting,
        configured=llm.configured,
        provider=llm.provider,
        model=settings.llm_model,
        vision_configured=llm.vision_configured,
        vision_model=llm.vision_model,
        rag_docs=rag.document_count if rag else 0,
        rag_chunks=rag.chunk_count if rag else 0,
        quick_questions=[
            {"label": q.label, "question": q.question} for q in profile.quick_questions
        ],
    )
