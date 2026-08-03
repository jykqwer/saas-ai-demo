"""网络搜索客户端：为助手提供实时/通用信息检索。

提供两个搜索提供方：
- tavily：面向 AI 应用的网页搜索 API（默认，需要 API Key）
- wikipedia：中文维基百科 API（auto 模式下的无 Key 兜底）

默认按配置顺序尝试；某个提供方失败或空结果时自动回退。
"""

import html as html_module
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from core.logging import get_logger


@dataclass(frozen=True, slots=True)
class SearchResult:
    """一条搜索结果。"""

    title: str
    url: str
    snippet: str


def _strip_tags(value: str) -> str:
    """去掉 HTML 标签并反转义实体。"""

    text = re.sub(r"<[^>]+>", "", value)
    return html_module.unescape(text).strip()


def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
    seen: set[str] = set()
    out: list[SearchResult] = []
    for item in results:
        if item.url in seen:
            continue
        seen.add(item.url)
        out.append(item)
    return out


class WebSearchClient:
    """基于 httpx 的多提供方网络搜索客户端。"""

    def __init__(
        self,
        *,
        provider: str = "tavily",
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
        max_results: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.max_results = max_results
        self._logger = get_logger()
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            transport=transport,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                )
            },
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def search(self, query: str, limit: int | None = None) -> list[SearchResult]:
        """按配置尝试各提供方，返回去重后的结果。"""

        limit = limit or self.max_results
        providers = (
            [self.provider]
            if self.provider != "auto"
            else ["tavily", "wikipedia"]
        )

        for name in providers:
            try:
                results = await self._run_provider(name, query, limit)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                self._logger.warning(
                    "web_search_provider_error",
                    extra={"provider": name, "error_type": exc.__class__.__name__},
                )
                continue
            if results:
                return _dedupe(results)
        return []

    async def _run_provider(self, name: str, query: str, limit: int) -> list[SearchResult]:
        if name == "tavily":
            return await self._search_tavily(query, limit)
        if name == "wikipedia":
            return await self._search_wikipedia(query, limit)
        raise ValueError(f"unknown web search provider: {name}")

    async def _search_tavily(self, query: str, limit: int) -> list[SearchResult]:
        """调用 Tavily Search API，并映射为聊天层统一的搜索结果。"""

        if not self.api_key:
            raise ValueError("Tavily API key is not configured")

        response = await self._http.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "query": query,
                "search_depth": "basic",
                "max_results": limit,
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        results: list[SearchResult] = []
        for item in data.get("results", []):
            if len(results) >= limit:
                break
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            if not title or not url.startswith(("http://", "https://")):
                continue
            snippet = str(item.get("content", "")).strip()
            results.append(SearchResult(title=title, url=url, snippet=snippet))
        return results

    async def _search_wikipedia(self, query: str, limit: int) -> list[SearchResult]:
        response = await self._http.get(
            "https://zh.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": limit,
                "utf8": 1,
            },
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()

        results: list[SearchResult] = []
        for item in data.get("query", {}).get("search", []):
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            snippet = _strip_tags(str(item.get("snippet", "")))
            page_url = "https://zh.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
            results.append(SearchResult(title=title, url=page_url, snippet=snippet))
        return results
