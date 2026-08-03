"""大模型接入客户端。

支持任意 OpenAI 兼容的 /chat/completions 接口（OpenAI、DeepSeek、Moonshot 等），
通过 base_url 与 api_key 区分服务商。未配置 Key 时启用内置演示模式，便于本地联调。

设计要点：
- 不把 Key 写入日志；错误消息只描述失败类别，不回显上游原始响应体。
- 对上下文长度做上限约束（按对话轮数裁剪），防止请求体无界增长。
- 提供流式接口：真实模式解析上游 SSE，演示模式模拟逐字输出。
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from core.logging import get_logger


class ChatProviderError(Exception):
    """调用上游大模型失败；message 必须不含敏感信息。"""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class LLMResult:
    """一次对话调用的结果；usage 可能为空（上游未返回）。"""

    text: str
    model: str
    provider: str
    mock: bool
    latency_ms: int
    usage: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LLMToolCall:
    """模型请求的一次工具调用。

    reasoning_content：推理型模型（如 deepseek-v4-flash）在同一条响应里产出的
    思考内容，工具调用被回传时必须一并带上，否则上游会 400。
    """

    id: str
    name: str
    arguments: str
    reasoning_content: str = ""


@dataclass(frozen=True, slots=True)
class StreamEvent:
    """流式轮次中的事件：文本增量或一次工具调用。"""

    kind: Literal["delta", "tool_call"]
    text: str = ""
    tool_call: LLMToolCall | None = None


def _extract_provider(base_url: str) -> str:
    """根据 base_url 推断服务商名称，仅用于展示与日志。"""

    lowered = base_url.lower()
    if "deepseek" in lowered:
        return "deepseek"
    if "openai" in lowered:
        return "openai"
    if "moonshot" in lowered:
        return "moonshot"
    if "dashscope" in lowered or "aliyun" in lowered:
        return "aliyun-qwen"
    return "openai-compatible"


def _split_mock_chunks(text: str, max_chunk: int = 24) -> list[str]:
    """把演示模式回复切成接近自然语速的增量块。

    优先按标点切分，避免在词中截断；剩余长串按字符兜底切分。
    """

    chunks: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if len(buffer) >= max_chunk or char in "。！？；，、\n":
            chunks.append(buffer)
            buffer = ""
    if buffer:
        chunks.append(buffer)
    return chunks


class LLMClient:
    """面向聊天场景的轻量大模型客户端。"""

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_context_turns: int,
        mock_reply: Any,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_context_turns = max_context_turns
        self._mock_reply = mock_reply
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Content-Type": "application/json"},
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def provider(self) -> str:
        return _extract_provider(self.base_url)

    async def close(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        *,
        messages: list[dict],
        request_id: str,
        rag_chunks: list | None = None,
    ) -> LLMResult:
        """对裁剪后的上下文做一次非流式对话；演示模式直接返回内置回复。"""

        if not self.configured:
            return self._mock_chat(messages, request_id, rag_chunks)

        # 只发送最近 N 轮（保留 system 消息），控制请求体积。
        context = messages[:1] + messages[-self.max_context_turns :]

        payload = {
            "model": self.model,
            "messages": context,
            "temperature": 0.6,
            # 推理型模型会先消耗思考 token，留足余量避免截断最终回答。
            "max_tokens": 2048,
        }

        started = __import__("time").perf_counter()
        try:
            response = await self._http.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.TimeoutException as exc:
            get_logger().warning(
                "llm_timeout",
                extra={
                    "request_id": request_id,
                    "provider": self.provider,
                    "model": self.model,
                },
            )
            raise ChatProviderError(
                code="LLM_TIMEOUT",
                message="The AI provider did not respond in time.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ChatProviderError(
                code="LLM_NETWORK_ERROR",
                message="Failed to reach the AI provider.",
            ) from exc

        latency_ms = int((__import__("time").perf_counter() - started) * 1000)

        if response.status_code != 200:
            get_logger().warning(
                "llm_http_error",
                extra={
                    "request_id": request_id,
                    "provider": self.provider,
                    "model": self.model,
                    "status_code": response.status_code,
                    "latency_ms": latency_ms,
                },
            )
            raise ChatProviderError(
                code="LLM_UPSTREAM_ERROR",
                message="The AI provider returned an error.",
            )

        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ChatProviderError(
                code="LLM_INVALID_RESPONSE",
                message="The AI provider returned an unexpected payload.",
            ) from exc

        get_logger().info(
            "llm_chat",
            extra={
                "request_id": request_id,
                "provider": self.provider,
                "model": self.model,
                "latency_ms": latency_ms,
            },
        )

        return LLMResult(
            text=text,
            model=self.model,
            provider=self.provider,
            mock=False,
            latency_ms=latency_ms,
            usage=data.get("usage"),
        )

    async def stream_round(
        self,
        *,
        messages: list[dict],
        request_id: str,
        tools: list[dict] | None = None,
        rag_chunks: list | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """单轮流式请求：产出文本增量或一次工具调用事件。

        演示模式只产出增量（不使用工具）。
        """

        if not self.configured:
            last_user = next(
                (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
            )
            text = self._mock_reply(last_user, rag_chunks)
            get_logger().info(
                "llm_mock_stream",
                extra={
                    "request_id": request_id,
                    "provider": "mock",
                    "model": self.model,
                    "mock": True,
                },
            )
            for chunk in _split_mock_chunks(text):
                yield StreamEvent(kind="delta", text=chunk)
                await asyncio.sleep(0.012)
            return

        context = messages[:1] + messages[-self.max_context_turns :]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": context,
            "temperature": 0.6,
            # 推理型模型会先消耗思考 token，留足余量避免截断最终回答。
            "max_tokens": 2048,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        tool_calls: dict[int, dict[str, str]] = {}
        reasoning_parts: list[str] = []
        try:
            async with self._http.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            ) as response:
                if response.status_code != 200:
                    get_logger().warning(
                        "llm_stream_http_error",
                        extra={
                            "request_id": request_id,
                            "provider": self.provider,
                            "model": self.model,
                            "status_code": response.status_code,
                        },
                    )
                    raise ChatProviderError(
                        code="LLM_UPSTREAM_ERROR",
                        message="The AI provider returned an error.",
                    )

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        choice = json.loads(data)["choices"][0]
                    except (KeyError, IndexError, TypeError, ValueError):
                        continue
                    delta = choice.get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield StreamEvent(kind="delta", text=content)
                    # 推理型模型的思考内容：需要随工具调用一并回传。
                    reasoning = delta.get("reasoning_content")
                    if reasoning:
                        reasoning_parts.append(reasoning)
                    for tool_call in delta.get("tool_calls") or []:
                        idx = int(tool_call.get("index", 0))
                        entry = tool_calls.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tool_call.get("id"):
                            entry["id"] = tool_call["id"]
                        function = tool_call.get("function") or {}
                        if function.get("name"):
                            entry["name"] += function["name"]
                        if function.get("arguments"):
                            entry["arguments"] += function["arguments"]
        except httpx.TimeoutException as exc:
            get_logger().warning(
                "llm_stream_timeout",
                extra={
                    "request_id": request_id,
                    "provider": self.provider,
                    "model": self.model,
                },
            )
            raise ChatProviderError(
                code="LLM_TIMEOUT",
                message="The AI provider did not respond in time.",
            ) from exc
        except httpx.HTTPError as exc:
            raise ChatProviderError(
                code="LLM_NETWORK_ERROR",
                message="Failed to reach the AI provider.",
            ) from exc

        get_logger().info(
            "llm_stream",
            extra={
                "request_id": request_id,
                "provider": self.provider,
                "model": self.model,
            },
        )

        if tool_calls:
            reasoning = "".join(reasoning_parts)
            # 同一响应可能包含多个工具调用（如同时检索知识库与联网），逐个下发。
            for idx in sorted(tool_calls):
                entry = tool_calls[idx]
                if not entry["id"] or not entry["name"]:
                    continue
                yield StreamEvent(
                    kind="tool_call",
                    tool_call=LLMToolCall(
                        id=entry["id"],
                        name=entry["name"],
                        arguments=entry["arguments"],
                        reasoning_content=reasoning,
                    ),
                )

    def _mock_chat(
        self,
        messages: list[dict],
        request_id: str,
        rag_chunks: list | None = None,
    ) -> LLMResult:
        """演示模式：优先用知识库检索结果，否则基于关键词回复。"""

        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        get_logger().info(
            "llm_mock",
            extra={
                "request_id": request_id,
                "provider": "mock",
                "model": self.model,
                "mock": True,
            },
        )
        return LLMResult(
            text=self._mock_reply(last_user, rag_chunks),
            model=self.model,
            provider="mock",
            mock=True,
            latency_ms=0,
            usage=None,
        )
