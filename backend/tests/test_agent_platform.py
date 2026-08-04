"""工具注册中心、持久化 Agent 轨迹与模型网关测试。"""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from api.v1.chat import ChatRequest
from core.agent import AgentOrchestrator
from core.config import Settings
from core.llm import ChatProviderError, LLMClient, LLMToolCall, StreamEvent
from core.model_gateway import ModelGateway
from core.tools import RegisteredTool, ToolPolicyError, ToolRegistry, ToolResult
from infrastructure.agent_repository import EphemeralAgentRepository
from main import create_app


def _schema(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "test tool",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }


def test_tool_registry_enforces_mode_policy() -> None:
    registry = ToolRegistry()
    registry.register(
        RegisteredTool(
            name="search",
            schema=_schema("search"),
            handler=lambda args: ToolResult(content=args["query"], metadata={}),
            allowed_modes=frozenset({"auto"}),
        )
    )

    async def run():
        result = await registry.execute(
            name="search", arguments={"query": "hello"}, mode="auto"
        )
        with pytest.raises(ToolPolicyError):
            await registry.execute(
                name="search", arguments={"query": "hello"}, mode="knowledge"
            )
        return result

    assert asyncio.run(run()).content == "hello"


def test_agent_persists_dynamic_model_and_tool_steps() -> None:
    class FakeLLM:
        provider = "fake"
        model = "fake-model"
        configured = True

        def __init__(self) -> None:
            self.calls = 0

        async def stream_round(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent(
                    kind="tool_call",
                    tool_call=LLMToolCall(
                        id="call_1",
                        name="search",
                        arguments=json.dumps({"query": "价格"}),
                    ),
                )
            else:
                yield StreamEvent(kind="delta", text="最终答案")

    async def run():
        repository = EphemeralAgentRepository()
        registry = ToolRegistry()
        registry.register(
            RegisteredTool(
                name="search",
                schema=_schema("search"),
                handler=lambda args: ToolResult(
                    content='{"price": 99}', metadata={"query": args["query"]}
                ),
                allowed_modes=frozenset({"auto"}),
            )
        )
        owner_id = uuid4()
        run = await repository.create_run(
            session_id=uuid4(),
            owner_user_id=owner_id,
            mode="auto",
            input_text="多少钱",
        )
        orchestrator = AgentOrchestrator(repository=repository, tools=registry)
        events = [
            event
            async for event in orchestrator.run_stream(
                run_id=run.id,
                llm=FakeLLM(),
                messages=[{"role": "user", "content": "多少钱"}],
                request_id="req_test",
                mode="auto",
            )
        ]
        trace = await repository.get_trace(run_id=run.id, owner_user_id=owner_id)
        return events, trace

    events, trace = asyncio.run(run())
    assert events[-1]["type"] == "agent_complete"
    assert events[-1]["reply"] == "最终答案"
    assert trace is not None
    assert trace.run.status == "completed"
    assert [step.kind for step in trace.steps] == [
        "model_call",
        "tool_call",
        "model_call",
    ]
    assert [event.event_type for event in trace.events] == [
        "run.started",
        "model.started",
        "tool.started",
        "tool.completed",
        "model.started",
        "run.completed",
    ]


def _client(model: str) -> LLMClient:
    return LLMClient(
        api_key="sk-test",
        base_url="https://example.com/v1",
        model=model,
        timeout_seconds=10,
        max_context_turns=5,
        mock_reply=None,
    )


def test_model_gateway_routes_images_to_qwen() -> None:
    primary = _client("text-model")
    vision = _client("qwen3.7-plus")
    gateway = ModelGateway(primary=primary, vision=vision)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AA=="},
                },
                {"type": "text", "text": "描述图片"},
            ],
        }
    ]
    assert gateway.select(messages) is vision
    assert gateway.select([{"role": "user", "content": "hello"}]) is primary
    asyncio.run(gateway.close())


def test_model_gateway_sends_openai_vision_payload_to_qwen() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "一张测试图片"}}]},
        )

    primary = _client("text-model")
    vision = _client("qwen3.7-plus")
    unused_vision_http = vision._http
    vision._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = ModelGateway(primary=primary, vision=vision)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,AA=="},
                },
                {"type": "text", "text": "描述图片"},
            ],
        }
    ]

    async def run():
        await unused_vision_http.aclose()
        result = await gateway.chat(messages=messages, request_id="req_vision")
        await gateway.close()
        return result

    result = asyncio.run(run())
    assert result.text == "一张测试图片"
    assert captured["model"] == "qwen3.7-plus"
    assert captured["messages"] == messages


def test_model_gateway_rejects_images_without_vision_route() -> None:
    gateway = ModelGateway(primary=_client("text-model"))
    with pytest.raises(ChatProviderError) as caught:
        gateway.select(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AA=="},
                        }
                    ],
                }
            ]
        )
    assert caught.value.code == "VISION_MODEL_NOT_CONFIGURED"
    asyncio.run(gateway.close())


def test_chat_request_accepts_safe_image_data_url() -> None:
    request = ChatRequest(content="描述图片", images=["data:image/png;base64,AA=="])
    assert request.images
    with pytest.raises(ValueError):
        ChatRequest(content="描述图片", images=["https://example.com/image.png"])
    with pytest.raises(ValueError):
        ChatRequest(content="描述图片", images=["data:image/png;base64,not-base64"])


def test_stream_api_persists_queryable_agent_trace() -> None:
    async def run():
        app = create_app(
            Settings(
                environment="test",
                log_level="CRITICAL",
                auth_enabled=False,
                web_search_enabled=False,
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/chat/stream", json={"content": "标准版多少钱"}
            )
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ")
            ]
            done = next(event for event in events if event["type"] == "done")
            trace_response = await client.get(f"/api/v1/runs/{done['run_id']}")
        await app.state.llm_client.close()
        return response, trace_response

    response, trace_response = asyncio.run(run())
    assert response.status_code == 200
    assert trace_response.status_code == 200
    body = trace_response.json()
    assert body["run"]["status"] == "completed"
    assert body["steps"][0]["kind"] == "model_call"


def test_chat_api_sends_current_user_turn_once() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    async def run():
        app = create_app(
            Settings(
                environment="test",
                log_level="CRITICAL",
                auth_enabled=False,
                llm_api_key="sk-test",
                web_search_enabled=False,
            )
        )
        original_http = app.state.llm_client.primary._http
        app.state.llm_client.primary._http = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        await original_http.aclose()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post("/api/v1/chat", json={"content": "hello"})
        await app.state.llm_client.close()
        return response

    response = asyncio.run(run())
    assert response.status_code == 200
    assert [message["role"] for message in captured["messages"]] == [
        "system",
        "user",
    ]
