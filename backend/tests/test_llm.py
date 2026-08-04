"""大模型客户端解析测试：流式文本化工具调用的识别与清理。"""

import asyncio
import json

import httpx

from core.llm import LLMClient, strip_text_tool_calls


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_client(handler) -> LLMClient:
    client = LLMClient(
        api_key="sk-test",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        timeout_seconds=10,
        max_context_turns=5,
        mock_reply=None,
    )
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def test_stream_round_detects_text_tool_call() -> None:
    """模型把工具调用写成文本时，应转为 tool_call 事件而不是当作回答输出。"""

    # 标记跨多个增量分片下发，验证暂挂逻辑。
    sse = (
        'data: {"choices":[{"delta":{"content":"让我查一下"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"<invoke name=\\"web_search\\">"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"<parameter name=\\"query\\">鲁卫英 介绍</parameter></invoke>"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"以上就是结果"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    client = _make_client(
        lambda req: httpx.Response(
            200, text=sse, headers={"content-type": "text/event-stream"}
        )
    )

    async def run():
        events = []
        async for ev in client.stream_round(
            messages=[{"role": "user", "content": "鲁卫英是谁"}],
            request_id="req_test",
        ):
            events.append(ev)
        await client.close()
        return events

    events = _run(run())

    deltas = [e.text for e in events if e.kind == "delta"]
    # 标记不应出现在回答里；普通文本应正常流式输出。
    assert deltas == ["让我查一下", "以上就是结果"]

    calls = [e.tool_call for e in events if e.kind == "tool_call" and e.tool_call]
    assert len(calls) == 1
    assert calls[0].name == "web_search"
    arguments = json.loads(calls[0].arguments)
    assert arguments["query"] == "鲁卫英 介绍"


def test_stream_round_yields_before_upstream_finishes() -> None:
    """收到完整 SSE 分片后应立即产出 delta，不能等待上游流结束。"""

    async def run():
        release_tail = asyncio.Event()

        class ControlledStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
                await release_tail.wait()
                yield (
                    b'data: {"choices":[{"delta":{"content":"second"}}]}\n\n'
                    b"data: [DONE]\n\n"
                )

        client = _make_client(
            lambda req: httpx.Response(
                200,
                stream=ControlledStream(),
                headers={"content-type": "text/event-stream"},
            )
        )
        stream = client.stream_round(
            messages=[{"role": "user", "content": "test"}],
            request_id="req_incremental",
        )

        first = await asyncio.wait_for(anext(stream), timeout=0.2)
        assert first.kind == "delta"
        assert first.text == "first"

        release_tail.set()
        remaining = [event async for event in stream]
        await client.close()
        return remaining

    remaining = _run(run())
    assert [event.text for event in remaining if event.kind == "delta"] == ["second"]


def test_stream_round_detects_real_deepseek_dsml_tool_call() -> None:
    """真实 DeepSeek DSML 标签应被执行，不能作为可见文本泄漏。"""

    sse = (
        'data: {"choices":[{"delta":{"reasoning_content":"需要进一步检索"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"<｜｜DSML｜｜tool_calls>\\n<｜｜DSML｜｜inv"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"oke name=\\"web_search\\">\\n<｜｜DSML｜｜parameter name=\\"query\\" string=\\"true\\">\\"鲁卫英\\" 人物 介绍</｜｜DSML｜｜parameter>\\n</｜｜DSML｜｜invoke>\\n</｜｜DSML｜｜tool_calls>"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    client = _make_client(
        lambda req: httpx.Response(
            200, text=sse, headers={"content-type": "text/event-stream"}
        )
    )

    async def run():
        events = []
        async for event in client.stream_round(
            messages=[{"role": "user", "content": "鲁卫英是谁"}],
            request_id="req_dsml",
        ):
            events.append(event)
        await client.close()
        return events

    events = _run(run())
    assert [event for event in events if event.kind == "delta"] == []

    calls = [
        event.tool_call
        for event in events
        if event.kind == "tool_call" and event.tool_call is not None
    ]
    assert len(calls) == 1
    assert calls[0].name == "web_search"
    assert calls[0].reasoning_content == "需要进一步检索"
    assert json.loads(calls[0].arguments)["query"] == '"鲁卫英" 人物 介绍'


def test_stream_round_deduplicates_text_and_structured_tool_call() -> None:
    """兼容接口同时返回文本和结构化表示时，同一工具只能执行一次。"""

    sse = (
        'data: {"choices":[{"delta":{"content":"<invoke name=\\"web_search\\"><parameter name=\\"query\\">测试</parameter></invoke>"}}]}\n\n'
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"web_search","arguments":"{\\"query\\":\\"测试\\"}"}}]}}]}\n\n'
        "data: [DONE]\n\n"
    )
    client = _make_client(
        lambda req: httpx.Response(
            200, text=sse, headers={"content-type": "text/event-stream"}
        )
    )

    async def run():
        events = []
        async for event in client.stream_round(
            messages=[{"role": "user", "content": "测试"}],
            request_id="req_dedupe",
        ):
            events.append(event)
        await client.close()
        return events

    events = _run(run())
    calls = [event for event in events if event.kind == "tool_call"]
    assert len(calls) == 1


def test_strip_text_tool_calls() -> None:
    text = (
        "好的，我查一下。"
        '<invoke name="web_search"><parameter name="query">测试</parameter></invoke>'
        "结果如下。"
    )
    cleaned = strip_text_tool_calls(text)
    assert "<invoke" not in cleaned
    assert "好的" in cleaned
    assert "结果如下" in cleaned


def test_strip_real_deepseek_dsml_tool_calls() -> None:
    text = (
        "<｜｜DSML｜｜tool_calls>"
        '<｜｜DSML｜｜invoke name="web_search">'
        '<｜｜DSML｜｜parameter name="query" string="true">测试</｜｜DSML｜｜parameter>'
        "</｜｜DSML｜｜invoke>"
        "</｜｜DSML｜｜tool_calls>"
    )
    assert strip_text_tool_calls(text) == ""
