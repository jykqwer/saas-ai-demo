"""网络搜索客户端测试：用 MockTransport 模拟各提供方返回，验证解析与回退。"""

import json

import httpx

from infrastructure.web_search import WebSearchClient


def _run(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)


DDG_HTML = """
<html><body>
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage&rut=abc">
  Example <b>Page</b>
</a>
<a class="result__snippet" href="#">这是 <b>片段</b> 描述</a>
<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ffoo.com%2Fx&rut=def">
  Foo Bar
</a>
<a class="result__snippet" href="#">Foo snippet text</a>
</body></html>
"""


def test_duckduckgo_parsing() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=DDG_HTML))
    client = WebSearchClient(provider="duckduckgo", transport=transport)
    try:
        results = _run(client.search("测试"))
    finally:
        _run(client.close())

    assert len(results) == 2
    assert results[0].url == "https://example.com/page"
    assert results[0].title == "Example Page"
    assert "片段" in results[0].snippet
    assert results[1].url == "https://foo.com/x"


def test_duckduckgo_empty_returns_none() -> None:
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, text="<html>no results</html>")
    )
    client = WebSearchClient(provider="duckduckgo", transport=transport)
    try:
        assert _run(client.search("不存在")) == []
    finally:
        _run(client.close())


def test_wikipedia_parsing() -> None:
    payload = {
        "query": {
            "search": [
                {
                    "title": "人工智能",
                    "snippet": "<span class='searchmatch'>人工智能</span>是研究",
                },
                {"title": "大模型", "snippet": "<span class='searchmatch'>大模型</span>介绍"},
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "zh.wikipedia.org" in request.url.host
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = WebSearchClient(provider="wikipedia", transport=transport)
    try:
        results = _run(client.search("人工智能"))
    finally:
        _run(client.close())

    assert len(results) == 2
    assert results[0].title == "人工智能"
    assert "人工智能" in results[0].snippet
    assert results[0].url.startswith("https://zh.wikipedia.org/wiki/")


def test_fallback_to_next_provider() -> None:
    """duckduckgo 失败/空时自动回退到 wikipedia。"""

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if "duckduckgo" in request.url.host:
            return httpx.Response(200, text="<html>nothing</html>")
        return httpx.Response(
            200,
            json={
                "query": {
                    "search": [{"title": "兜底结果", "snippet": "来自维基"}]
                }
            },
        )

    transport = httpx.MockTransport(handler)
    client = WebSearchClient(provider="auto", transport=transport)
    try:
        results = _run(client.search("测试"))
    finally:
        _run(client.close())

    assert results
    assert results[0].title == "兜底结果"
    assert "duckduckgo" in calls[0]
    assert "wikipedia" in calls[1]


def test_http_error_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "duckduckgo" in request.url.host:
            return httpx.Response(500)
        return httpx.Response(200, json={"query": {"search": [{"title": "T", "snippet": "S"}]}})

    transport = httpx.MockTransport(handler)
    client = WebSearchClient(provider="auto", transport=transport)
    try:
        results = _run(client.search("测试"))
    finally:
        _run(client.close())

    assert results and results[0].title == "T"


def test_format_web_results_json() -> None:
    from domain.chat import format_web_results
    from infrastructure.web_search import SearchResult

    text = format_web_results(
        [SearchResult(title="标题", url="https://x.com", snippet="摘要")]
    )
    data = json.loads(text)
    assert data[0]["title"] == "标题"
    assert data[0]["url"] == "https://x.com"
