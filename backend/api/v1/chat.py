"""AI 客服/售前助手的聊天接口、流式输出与人工转接接口。"""

import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.errors import ApiError
from core.llm import ChatProviderError, LLMToolCall, strip_text_tool_calls
from domain.chat import (
    MAX_MESSAGE_CHARS,
    RETRIEVE_KB_TOOL,
    WEB_SEARCH_TOOL,
    AssistantProfile,
    build_assistant_profile,
    build_system_prompt,
    format_rag_context,
    format_rag_tool_results,
    format_web_context,
    format_web_results,
)
from domain.session import (
    MAX_CONTACT_VALUE_CHARS,
    MAX_TICKET_SUBJECT_CHARS,
    default_title,
)

router = APIRouter(prefix="/chat", tags=["chat"])


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
    # auto=智能（模型按需联网）；web=始终联网；knowledge=仅知识库
    mode: Literal["auto", "web", "knowledge"] = "auto"

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("content must contain non-whitespace characters")
        return normalized


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
    )
    # 追加知识库参考资料；chunks 为 None 时现场检索（无预检索的场景兜底）。
    if chunks is None:
        rag = _rag_engine(request)
        chunks = rag.retrieve(content) if rag is not None else []
    prompt += format_rag_context(chunks)
    return prompt


async def _resolve_session(request: Request, session_id: UUID | None, content: str):
    """解析会话；没有提供 ID 时创建新会话（标题取自首条消息）。"""

    repo = _repository(request)
    if session_id is None:
        return await repo.create_session(title=default_title(content))
    session = await repo.get_session(session_id=session_id)
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
    rag_chunks = (
        rag.retrieve(content) if (rag is not None and pre_inject_rag) else []
    )
    system_prompt = _build_system_prompt(request, content, chunks=rag_chunks)

    turns: list[dict] = [{"role": "system", "content": system_prompt}]
    # 保留最近 N 轮（2N 条消息），避免请求体无界增长。
    for message in history[-settings.llm_max_context_turns * 2 :]:
        turns.append({"role": message.role, "content": message.content})
    turns.append({"role": "user", "content": content})
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
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """非流式对话：持久化消息并返回助手完整回复。"""

    llm = request.app.state.llm_client
    request_id = getattr(request.state, "request_id", "unknown")
    session = await _resolve_session(request, body.session_id, body.content)
    await _persist(request, session_id=session.id, role="user", content=body.content)

    turns, rag_chunks = await _build_turns(request, session.id, body.content)
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
                    {"source": c.source, "heading": c.heading, "score": round(c.score, 3)}
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
async def chat_stream(request: Request, body: ChatRequest) -> StreamingResponse:
    """SSE 流式对话：逐段推送助手回复增量，结束时落库。"""

    llm = request.app.state.llm_client
    request_id = getattr(request.state, "request_id", "unknown")

    async def event_source():
        try:
            session = await _resolve_session(request, body.session_id, body.content)
            await _persist(
                request, session_id=session.id, role="user", content=body.content
            )

            mode = body.mode or "auto"
            web_search = getattr(request.app.state, "web_search", None)
            rag = _rag_engine(request)

            # 预注入知识库的条件：演示模式（无工具循环）以及仅知识库模式。
            # 真实自动/始终联网模式不预注入：auto 由模型调用 retrieve_knowledge_base
            # 按需检索，web 为纯联网（只注入搜索结果）。
            pre_inject_rag = (not llm.configured) or mode == "knowledge"
            turns, rag_chunks = await _build_turns(
                request,
                session.id,
                body.content,
                pre_inject_rag=pre_inject_rag,
            )

            # 模式提示词：明确约束模型行为。
            mode_note = {
                "knowledge": (
                    "\n\n【当前模式：仅知识库】请只依据内部知识库回答；"
                    "若知识库没有相关信息，如实说明不知道，不要编造或猜测实时信息。"
                ),
                "web": (
                    "\n\n【当前模式：始终联网】请优先使用提供的网络搜索结果回答最新/通用问题，"
                    "并在回答中尽量标注来源。"
                ),
                "auto": "",
            }.get(mode, "")
            if mode_note:
                turns[0] = {**turns[0], "content": turns[0]["content"] + mode_note}

            # 开头先推送元信息（会话 ID、模型、接入状态、RAG 来源）。
            yield _sse(
                {
                    "type": "meta",
                    "session_id": str(session.id),
                    "model": llm.model,
                    "provider": llm.provider,
                    "mock": not llm.configured,
                    "mode": mode,
                    "rag": [
                        {
                            "source": chunk.source,
                            "heading": chunk.heading,
                            "score": round(chunk.score, 3),
                        }
                        for chunk in rag_chunks
                    ],
                }
            )

            tools = None
            tool_used = False
            web_count = 0
            rag_count = len(rag_chunks)
            # 本轮实际采用的来源（供落库持久化，刷新会话后仍可展示）。
            used_rag_meta: list[dict] = [
                {
                    "source": chunk.source,
                    "heading": chunk.heading,
                    "score": round(chunk.score, 3),
                }
                for chunk in rag_chunks
            ]
            used_web_meta: list[dict] = []
            web_query: str | None = None
            buffer: list[str] = []

            # 模式分支：
            # - knowledge：不联网、不传工具（知识库已预注入）
            # - web：不注入知识库，无条件联网检索并注入搜索结果，再作答（不传工具）
            # - auto：分别按可用能力组装知识库检索 + 联网工具，模型按需决策。
            #   关闭联网不应让模型失去 RAG 工具，反之亦然。
            if mode == "auto":
                tools = []
                if rag is not None:
                    tools.append(RETRIEVE_KB_TOOL)
                if web_search is not None:
                    tools.append(WEB_SEARCH_TOOL)
                tools = tools or None
            elif mode == "web" and web_search is not None:
                results = await web_search.search(body.content)
                web_count = len(results)
                used_web_meta = [
                    {"title": r.title, "url": r.url, "snippet": r.snippet}
                    for r in results
                ]
                web_query = body.content
                if results:
                    turns[0] = {
                        **turns[0],
                        "content": turns[0]["content"] + format_web_context(results),
                    }
                yield _sse(
                    {
                        "type": "search",
                        "query": body.content,
                        "results": [
                            {"title": r.title, "url": r.url, "snippet": r.snippet}
                            for r in results
                        ],
                    }
                )

            # 工具循环：auto 模式下模型可请求检索知识库或联网，执行后基于结果作答。
            # DeepSeek 推理模型支持连续多轮工具调用：保留工具定义、循环多轮，
            # 直到模型输出自然语言为止；用轮次上限兜底防死循环。
            MAX_TOOL_ROUNDS = 4
            for _round in range(MAX_TOOL_ROUNDS):
                round_text: list[str] = []
                tool_calls: list[LLMToolCall] = []
                async for event in llm.stream_round(
                    messages=turns,
                    request_id=request_id,
                    tools=tools,
                    rag_chunks=rag_chunks,
                ):
                    if event.kind == "delta":
                        # 先暂存本轮文本：确认本轮没有工具调用后才推给前端，
                        # 避免工具调用轮把内部 DSML 泄漏到回答。
                        round_text.append(event.text)
                    elif event.kind == "tool_call" and event.tool_call is not None:
                        tool_calls.append(event.tool_call)

                if not tool_calls:
                    # 自然语言轮：本轮的文本才是最终回答，此时才流式推送。
                    for chunk in round_text:
                        buffer.append(chunk)
                        yield _sse({"type": "delta", "text": chunk})
                    break

                # 推理型模型的思考内容必须随工具调用回传，否则上游返回 400。
                reasoning_content = tool_calls[0].reasoning_content
                assistant_tool_calls: list[dict] = []
                tool_results: list[dict] = []

                for tool_call in tool_calls:
                    try:
                        arguments = json.loads(tool_call.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    query = str(arguments.get("query", "")).strip()

                    if tool_call.name == "web_search":
                        tool_used = True
                        results = (
                            await web_search.search(query)
                            if web_search is not None and query
                            else []
                        )
                        current_web_meta = [
                            {"title": r.title, "url": r.url, "snippet": r.snippet}
                            for r in results
                        ]
                        used_web_meta = _merge_source_rows(
                            used_web_meta,
                            current_web_meta,
                            keys=("url",),
                        )
                        web_count = len(used_web_meta)
                        web_query = query
                        yield _sse(
                            {
                                "type": "search",
                                "query": query,
                                # 推送累计来源，后一次空结果不会清掉前一次有效来源。
                                "results": used_web_meta,
                            }
                        )
                        tool_content = format_web_results(results)
                    elif tool_call.name == "retrieve_knowledge_base":
                        # 知识库按需检索：只有模型判定为产品问题时才执行并展示来源。
                        chunks = (
                            rag.retrieve(query)
                            if rag is not None and query
                            else []
                        )
                        current_rag_meta = [
                            {
                                "source": c.source,
                                "heading": c.heading,
                                "score": round(c.score, 3),
                            }
                            for c in chunks
                        ]
                        used_rag_meta = _merge_source_rows(
                            used_rag_meta,
                            current_rag_meta,
                            keys=("source", "heading"),
                        )
                        rag_count = len(used_rag_meta)
                        yield _sse({"type": "rag_used", "rag": used_rag_meta})
                        tool_content = format_rag_tool_results(chunks)
                    else:
                        # 未知工具：不执行，也不回传。
                        continue

                    assistant_tool_calls.append(
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": tool_call.arguments,
                            },
                        }
                    )
                    tool_results.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": tool_content,
                        }
                    )

                if not assistant_tool_calls:
                    # 只出现未知工具：把本轮回退为自然语言轮。
                    for chunk in round_text:
                        buffer.append(chunk)
                        yield _sse({"type": "delta", "text": chunk})
                    break

                # 追加助手工具调用（含思考内容）与全部工具结果，继续下一轮。
                # 工具调用轮的文本进入 assistant 消息上下文，不进入最终回答。
                assistant_msg: dict = {
                    "role": "assistant",
                    "content": strip_text_tool_calls("".join(round_text)),
                    "tool_calls": assistant_tool_calls,
                }
                if reasoning_content:
                    assistant_msg["reasoning_content"] = reasoning_content
                turns = [*turns, assistant_msg, *tool_results]
                # 关键：不设 tools=None，保留工具定义让模型可连续多轮调用；
                # 由 MAX_TOOL_ROUNDS 上限兜底，杜绝死循环。
            else:
                # 不再通过 tools=None 强迫模型收敛：这会让模型把内部 DSML 当文本
                # 输出。达到上限后返回受控错误，保证内部协议不会展示或落库。
                yield _sse(
                    {
                        "type": "error",
                        "code": "TOOL_ROUND_LIMIT",
                        "message": "检索次数过多，请缩小问题范围后重试。",
                    }
                )
                return

            reply = strip_text_tool_calls("".join(buffer))
            # 组装本轮实际采用的来源，随助手消息落库以便刷新后恢复展示。
            sources: dict | None = None
            if used_rag_meta or used_web_meta:
                sources = {}
                if used_rag_meta:
                    sources["rag"] = used_rag_meta
                if used_web_meta:
                    sources["web"] = used_web_meta
                    if web_query:
                        sources["web_query"] = web_query
            await _persist(
                request,
                session_id=session.id,
                role="assistant",
                content=reply,
                provider=llm.provider,
                model=llm.model,
                mock=not llm.configured,
                sources=sources,
            )
            yield _sse(
                {
                    "type": "done",
                    "session_id": str(session.id),
                    "model": llm.model,
                    "provider": llm.provider,
                    "mock": not llm.configured,
                    "mode": mode,
                    "web": tool_used or mode == "web",
                    "web_count": web_count,
                    "rag_count": rag_count,
                }
            )
        except ChatProviderError as exc:
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
async def handoff(request: Request, body: HandoffRequest) -> HandoffResponse:
    """创建人工转接工单；未提供会话时自动开一个。"""

    repo = _repository(request)
    if body.session_id is None:
        session = await repo.create_session(title="人工转接咨询")
        session_id = session.id
    else:
        session = await repo.get_session(session_id=body.session_id)
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
        rag_docs=rag.document_count if rag else 0,
        rag_chunks=rag.chunk_count if rag else 0,
        quick_questions=[
            {"label": q.label, "question": q.question}
            for q in profile.quick_questions
        ],
    )
