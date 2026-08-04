"""通用工具注册中心、模式策略与执行适配器。"""

import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from domain.chat import RETRIEVE_KB_TOOL, WEB_SEARCH_TOOL


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具执行结果：模型内容与客户端可见元数据严格分离。"""

    content: str
    metadata: dict[str, Any]
    client_event: dict[str, Any] | None = None


ToolHandler = Callable[[dict[str, Any]], Awaitable[ToolResult] | ToolResult]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    name: str
    schema: dict[str, Any]
    handler: ToolHandler
    allowed_modes: frozenset[str]


class ToolPolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolRegistry:
    """工具能力与实现的唯一注册点，Agent 不再硬编码具体工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def register(self, tool: RegisteredTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        declared_name = tool.schema.get("function", {}).get("name")
        if declared_name != tool.name:
            raise ValueError("tool schema name must match registry name")
        self._tools[tool.name] = tool

    def definitions(self, mode: str) -> list[dict[str, Any]]:
        return [
            tool.schema for tool in self._tools.values() if mode in tool.allowed_modes
        ]

    async def execute(
        self, *, name: str, arguments: dict[str, Any], mode: str
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolPolicyError("TOOL_NOT_FOUND", f"Unknown tool: {name}")
        if mode not in tool.allowed_modes:
            raise ToolPolicyError(
                "TOOL_NOT_ALLOWED", f"Tool {name} is not allowed in {mode} mode"
            )
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolPolicyError("INVALID_TOOL_ARGUMENTS", "query is required")
        result = tool.handler({**arguments, "query": query.strip()})
        if inspect.isawaitable(result):
            return await result
        return result


def build_tool_registry(*, rag, web_search) -> ToolRegistry:
    registry = ToolRegistry()

    if rag is not None:

        async def retrieve_knowledge(arguments: dict[str, Any]) -> ToolResult:
            chunks = rag.retrieve(arguments["query"])
            rows = [
                {
                    "source": chunk.source,
                    "heading": chunk.heading,
                    "score": round(chunk.score, 3),
                    "retrieval": getattr(chunk, "retrieval", None),
                }
                for chunk in chunks
            ]
            model_rows = [
                {
                    "source": chunk.source,
                    "heading": chunk.heading,
                    "content": chunk.content,
                    "score": round(chunk.score, 3),
                }
                for chunk in chunks
            ]
            return ToolResult(
                content=json.dumps(model_rows, ensure_ascii=False),
                metadata={"rag": rows},
                client_event={"type": "rag_used", "rag": rows},
            )

        registry.register(
            RegisteredTool(
                name="retrieve_knowledge_base",
                schema=RETRIEVE_KB_TOOL,
                handler=retrieve_knowledge,
                allowed_modes=frozenset({"auto", "knowledge"}),
            )
        )

    if web_search is not None:

        async def search_web(arguments: dict[str, Any]) -> ToolResult:
            query = arguments["query"]
            results = await web_search.search(query)
            rows = [
                {"title": item.title, "url": item.url, "snippet": item.snippet}
                for item in results
            ]
            return ToolResult(
                content=json.dumps(rows, ensure_ascii=False),
                metadata={"web": rows, "web_query": query},
                client_event={"type": "search", "query": query, "results": rows},
            )

        registry.register(
            RegisteredTool(
                name="web_search",
                schema=WEB_SEARCH_TOOL,
                handler=search_web,
                allowed_modes=frozenset({"auto", "web"}),
            )
        )

    return registry
