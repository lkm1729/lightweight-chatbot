"""联网搜索适配器。

支持五种协议：Tavily / Brave / Serper / SearXNG / Exa。每种协议描述「怎么拼请求、
怎么读结果」，分派逻辑共用一套——写法与四套模型协议的适配器一致。

安全要点：密钥在后端发出、不出服务端；搜索失败**不中断对话**，发 warning 后
照常让模型回答。
"""

from __future__ import annotations

from typing import Any

import httpx


class SearchResult:
    """统一的搜索结果条目。"""

    def __init__(self, title: str, url: str, content: str):
        self.title = title
        self.url = url
        self.content = content

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url, "content": self.content}


class SearchConfig:
    """搜索配置。从 config.json 的 search 段读入。"""

    def __init__(
        self,
        type: str,
        name: str,
        base_url: str,
        api_key: str,
        max_results: int = 5,
    ):
        self.type = type
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.max_results = max_results


async def search(config: SearchConfig, query: str) -> list[SearchResult]:
    """执行搜索，返回结果列表。失败时抛异常，由调用方降级处理。"""
    if config.type == "tavily":
        return await _search_tavily(config, query)
    elif config.type == "brave":
        return await _search_brave(config, query)
    elif config.type == "serper":
        return await _search_serper(config, query)
    elif config.type == "searxng":
        return await _search_searxng(config, query)
    elif config.type == "exa":
        return await _search_exa(config, query)
    else:
        raise ValueError(f"不支持的搜索协议：{config.type}")


# --------------------------------------------------------------------------
# 各协议的实现
# --------------------------------------------------------------------------


async def _search_tavily(config: SearchConfig, query: str) -> list[SearchResult]:
    """Tavily: POST /search，Authorization: Bearer，results[].{title,url,content}"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{config.base_url}/search",
            json={"query": query, "max_results": config.max_results},
            headers={"Authorization": f"Bearer {config.api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=_pick_content(r),
            )
            for r in results
        ]


async def _search_brave(config: SearchConfig, query: str) -> list[SearchResult]:
    """Brave: GET /res/v1/web/search?q=，X-Subscription-Token，
    web.results[].{title,url,description}"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{config.base_url}/res/v1/web/search",
            params={"q": query, "count": config.max_results},
            headers={"X-Subscription-Token": config.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("web", {}).get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=_pick_content(r),
            )
            for r in results
        ]


async def _search_serper(config: SearchConfig, query: str) -> list[SearchResult]:
    """Serper: POST /search，X-API-KEY，organic[].{title,link,snippet}"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{config.base_url}/search",
            json={"q": query, "num": config.max_results},
            headers={"X-API-KEY": config.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("organic", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("link", "") or r.get("url", ""),
                content=_pick_content(r),
            )
            for r in results
        ]


async def _search_searxng(config: SearchConfig, query: str) -> list[SearchResult]:
    """SearXNG: GET /search?q=&format=json，无鉴权（自建），
    results[].{title,url,content}"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{config.base_url}/search",
            params={"q": query, "format": "json", "number_of_results": config.max_results},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=_pick_content(r),
            )
            for r in results
        ]


async def _search_exa(config: SearchConfig, query: str) -> list[SearchResult]:
    """Exa: POST /search，x-api-key，results[].{title,url,text}"""
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{config.base_url}/search",
            json={"query": query, "numResults": config.max_results},
            headers={"x-api-key": config.api_key},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return [
            SearchResult(
                title=r.get("title", ""),
                url=r.get("url", ""),
                content=_pick_content(r),
            )
            for r in results
        ]


def _pick_content(result: dict[str, Any]) -> str:
    """取字段时按候选名依次尝试，上游小改字段名不至于整个功能失灵。"""
    for key in ("content", "snippet", "description", "text"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def format_search_context(query: str, results: list[SearchResult]) -> str:
    """把搜索结果格式化成注入对话的上下文。编号便于模型引用来源。"""
    if not results:
        return f"[搜索「{query}」无结果]"
    lines = [f"[联网搜索「{query}」的参考资料]"]
    for i, r in enumerate(results, start=1):
        lines.append(f"\n【{i}】{r.title}")
        lines.append(f"来源：{r.url}")
        if r.content:
            lines.append(r.content[:500])  # 截断过长摘要
    return "\n".join(lines)
