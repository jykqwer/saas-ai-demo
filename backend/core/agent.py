"""由 LLM 动态决策工具的持久化 Agent 编排循环。"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from core.llm import ChatProviderError, LLMToolCall, strip_text_tool_calls
from core.tools import ToolPolicyError, ToolRegistry
from domain.agent import AgentRepository


class AgentExecutionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _merge_rows(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    *,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
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


class AgentOrchestrator:
    """模型负责选择工具，编排器负责策略、执行、预算和持久化。"""

    def __init__(
        self,
        *,
        repository: AgentRepository,
        tools: ToolRegistry,
        max_tool_rounds: int = 4,
    ) -> None:
        self.repository = repository
        self.tools = tools
        self.max_tool_rounds = max_tool_rounds

    async def run_stream(
        self,
        *,
        run_id: UUID,
        llm,
        messages: list[dict[str, Any]],
        request_id: str,
        mode: str,
        rag_chunks: list | None = None,
        tools_enabled: bool = True,
        initial_rag: list[dict[str, Any]] | None = None,
        initial_web: list[dict[str, Any]] | None = None,
        initial_web_query: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        await self.repository.mark_running(run_id=run_id)
        await self.repository.append_event(
            run_id=run_id,
            event_type="run.started",
            payload={"mode": mode, "provider": llm.provider, "model": llm.model},
        )

        tool_definitions = self.tools.definitions(mode) if tools_enabled else []
        turns = list(messages)
        final_buffer: list[str] = []
        used_rag: list[dict[str, Any]] = list(initial_rag or [])
        used_web: list[dict[str, Any]] = list(initial_web or [])
        web_query: str | None = initial_web_query

        try:
            for round_index in range(self.max_tool_rounds):
                started = time.perf_counter()
                model_step = await self.repository.start_step(
                    run_id=run_id,
                    kind="model_call",
                    name=llm.model,
                    input_data={
                        "round": round_index + 1,
                        "message_count": len(turns),
                        "tools": [
                            item.get("function", {}).get("name")
                            for item in tool_definitions
                        ],
                    },
                )
                await self.repository.append_event(
                    run_id=run_id,
                    event_type="model.started",
                    payload={"step_id": str(model_step.id), "round": round_index + 1},
                )

                round_text: list[str] = []
                calls: list[LLMToolCall] = []
                buffer_start = len(final_buffer)
                try:
                    async for event in llm.stream_round(
                        messages=turns,
                        request_id=request_id,
                        tools=tool_definitions or None,
                        rag_chunks=rag_chunks,
                    ):
                        if event.kind == "delta":
                            round_text.append(event.text)
                            final_buffer.append(event.text)
                            yield {"type": "delta", "text": event.text}
                        elif event.kind == "tool_call" and event.tool_call is not None:
                            calls.append(event.tool_call)
                except ChatProviderError as exc:
                    latency_ms = int((time.perf_counter() - started) * 1000)
                    await self.repository.fail_step(
                        step_id=model_step.id,
                        code=exc.code,
                        message=exc.message,
                        latency_ms=latency_ms,
                    )
                    raise

                latency_ms = int((time.perf_counter() - started) * 1000)
                await self.repository.complete_step(
                    step_id=model_step.id,
                    output_data={
                        "text": strip_text_tool_calls("".join(round_text)),
                        "tool_calls": [
                            {
                                "id": call.id,
                                "name": call.name,
                                "arguments": call.arguments,
                            }
                            for call in calls
                        ],
                    },
                    latency_ms=latency_ms,
                )

                if not calls:
                    reply = strip_text_tool_calls("".join(final_buffer))
                    sources: dict[str, Any] = {}
                    if used_rag:
                        sources["rag"] = used_rag
                    if used_web:
                        sources["web"] = used_web
                        if web_query:
                            sources["web_query"] = web_query
                    await self.repository.complete_run(
                        run_id=run_id,
                        final_output=reply,
                        provider=llm.provider,
                        model=llm.model,
                    )
                    await self.repository.append_event(
                        run_id=run_id,
                        event_type="run.completed",
                        payload={
                            "model": llm.model,
                            "provider": llm.provider,
                            "rag_count": len(used_rag),
                            "web_count": len(used_web),
                        },
                    )
                    yield {
                        "type": "agent_complete",
                        "reply": reply,
                        "sources": sources or None,
                        "rag_count": len(used_rag),
                        "web_count": len(used_web),
                        "web": bool(used_web),
                    }
                    return

                if len(final_buffer) > buffer_start:
                    del final_buffer[buffer_start:]
                    yield {"type": "reset"}

                reasoning = calls[0].reasoning_content
                assistant_calls: list[dict[str, Any]] = []
                tool_messages: list[dict[str, Any]] = []
                for call in calls:
                    try:
                        arguments = json.loads(call.arguments or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    tool_started = time.perf_counter()
                    tool_step = await self.repository.start_step(
                        run_id=run_id,
                        kind="tool_call",
                        name=call.name,
                        input_data=arguments,
                    )
                    await self.repository.append_event(
                        run_id=run_id,
                        event_type="tool.started",
                        payload={"step_id": str(tool_step.id), "name": call.name},
                    )
                    try:
                        result = await self.tools.execute(
                            name=call.name, arguments=arguments, mode=mode
                        )
                    except ToolPolicyError as exc:
                        tool_latency = int((time.perf_counter() - tool_started) * 1000)
                        await self.repository.fail_step(
                            step_id=tool_step.id,
                            code=exc.code,
                            message=exc.message,
                            latency_ms=tool_latency,
                        )
                        raise AgentExecutionError(exc.code, exc.message) from exc
                    except Exception as exc:
                        tool_latency = int((time.perf_counter() - tool_started) * 1000)
                        await self.repository.fail_step(
                            step_id=tool_step.id,
                            code="TOOL_EXECUTION_FAILED",
                            message="The tool failed during execution.",
                            latency_ms=tool_latency,
                        )
                        raise AgentExecutionError(
                            "TOOL_EXECUTION_FAILED",
                            "The selected tool is temporarily unavailable.",
                        ) from exc

                    tool_latency = int((time.perf_counter() - tool_started) * 1000)
                    await self.repository.complete_step(
                        step_id=tool_step.id,
                        output_data=result.metadata,
                        latency_ms=tool_latency,
                    )
                    await self.repository.append_event(
                        run_id=run_id,
                        event_type="tool.completed",
                        payload={
                            "step_id": str(tool_step.id),
                            "name": call.name,
                            "latency_ms": tool_latency,
                        },
                    )
                    if result.client_event:
                        yield result.client_event

                    rag_rows = result.metadata.get("rag", [])
                    if rag_rows:
                        used_rag = _merge_rows(
                            used_rag, rag_rows, keys=("source", "heading")
                        )
                    web_rows = result.metadata.get("web", [])
                    if web_rows:
                        used_web = _merge_rows(used_web, web_rows, keys=("url",))
                        web_query = result.metadata.get("web_query") or web_query

                    assistant_calls.append(
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": call.arguments,
                            },
                        }
                    )
                    tool_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": result.content,
                        }
                    )

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": strip_text_tool_calls("".join(round_text)),
                    "tool_calls": assistant_calls,
                }
                if reasoning:
                    assistant_message["reasoning_content"] = reasoning
                turns = [*turns, assistant_message, *tool_messages]

            raise AgentExecutionError(
                "TOOL_ROUND_LIMIT", "检索次数过多，请缩小问题范围后重试。"
            )
        except asyncio.CancelledError:
            await self.repository.cancel_run(run_id=run_id)
            raise
        except ChatProviderError as exc:
            await self.repository.fail_run(
                run_id=run_id, code=exc.code, message=exc.message
            )
            raise
        except AgentExecutionError as exc:
            await self.repository.fail_run(
                run_id=run_id, code=exc.code, message=exc.message
            )
            raise
        except Exception:
            await self.repository.fail_run(
                run_id=run_id,
                code="INTERNAL_ERROR",
                message="An unexpected agent error occurred.",
            )
            raise
