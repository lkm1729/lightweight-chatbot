"""对话流式端点。"""

from __future__ import annotations

import base64
import json
from dataclasses import asdict
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import attachments as attachment_store
from app import search as search_module
from app.config import load_config, resolve_api_key
from app.providers import UnknownProviderError, get_provider
from app.providers.base import Attachment, ChatMessage, ChatRequest, ProviderConfig

router = APIRouter(prefix="/api/chat", tags=["chat"])


class Message(BaseModel):
    """前端消息格式。

    ``attachments`` 只带元数据（上传端点返回的那份），字节由后端按 id 从
    ``data/attachments/`` 读出来——让前端把几 MB 的 base64 每轮都重发一遍毫无必要。
    """

    role: str
    content: str
    attachments: list[dict[str, Any]] = []


class ChatStreamRequest(BaseModel):
    """流式对话请求体。"""

    provider: str
    base_url: str
    api_key: str = ""
    model: str
    messages: list[Message]
    vendor_id: str | None = None
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning: str = "medium"
    extra_headers: dict[str, str] = {}
    search: bool = False


@router.post("/stream")
async def chat_stream(request: ChatStreamRequest):
    """流式对话，返回 SSE。开启 search=True 时先联网搜索，再把结果作为上下文注入。"""
    try:
        provider_cls = get_provider(request.provider)
    except UnknownProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = ProviderConfig(
        base_url=request.base_url,
        # 前端可能只持有掩码值，回落到对应供应商的本地明文
        api_key=resolve_api_key(request.provider, request.api_key, request.vendor_id),
        extra_headers=request.extra_headers,
    )
    provider = provider_cls(config)

    messages = list(_to_chat_message(m) for m in request.messages)

    # 搜索在后端做，密钥不出服务端。失败不中断对话，发 warning 后照常回答
    search_results = None
    if request.search and messages:
        try:
            cfg = load_config()
            search_cfg_raw = cfg.get("search", {})
            if search_cfg_raw.get("type") and search_cfg_raw.get("api_key"):
                search_cfg = search_module.SearchConfig(
                    type=search_cfg_raw["type"],
                    name=search_cfg_raw.get("name", ""),
                    base_url=search_cfg_raw["base_url"],
                    api_key=search_cfg_raw["api_key"],
                    max_results=search_cfg_raw.get("max_results", 5),
                )
                # 拿最后一条用户消息当查询词
                query = messages[-1].content if messages[-1].role == "user" else ""
                if query:
                    results = await search_module.search(search_cfg, query)
                    search_results = {"query": query, "results": [r.to_dict() for r in results]}
                    # 把搜索结果编号列表作为上下文注入，让模型在回答时引用 [1], [2] 等
                    sources_text = "\n".join(
                        f"[{i+1}] {r.title}\n{r.content}\nURL: {r.url}"
                        for i, r in enumerate(results)
                    )
                    context = f"参考以下搜索结果回答问题（引用时使用 [1], [2] 等编号）：\n\n{sources_text}\n\n"
                    messages[-1] = ChatMessage(
                        role="user",
                        content=f"{context}{messages[-1].content}",
                        attachments=messages[-1].attachments,
                    )
        except Exception as e:
            # 搜索失败只记警告，不阻断对话
            search_results = {"error": str(e)}

    chat_request = ChatRequest(
        model=request.model,
        messages=tuple(messages),
        system=request.system,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        reasoning=request.reasoning,
    )

    return StreamingResponse(
        _event_stream(provider, chat_request, search_results),
        media_type="text/event-stream",
        headers={
            # 关掉缓冲，否则代理会攒够一批才吐给前端
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _to_chat_message(message: Message) -> ChatMessage:
    """把前端消息转成协议无关的形式，顺手把附件字节读进来。

    只有 user 消息带附件：四家协议都不接受 assistant 消息里塞图片或文件。
    """
    if message.role != "user" or not message.attachments:
        return ChatMessage(role=message.role, content=message.content)
    loaded = (_load_attachment(item) for item in message.attachments)
    return ChatMessage(
        role=message.role,
        content=message.content,
        attachments=tuple(item for item in loaded if item is not None),
    )


def _load_attachment(meta: dict[str, Any]) -> Attachment | None:
    """按 id 取出附件字节，编码成适配器要的 base64 形式。

    读不到就跳过（文件被清理、id 不合法），不因为一个附件让整轮对话发不出去。
    """
    attachment_id = meta.get("id")
    stored = attachment_store.load_meta(attachment_id)
    raw = attachment_store.load_bytes(attachment_id)
    if stored is None or raw is None:
        return None
    return Attachment(
        id=str(attachment_id),
        name=str(stored.get("name") or attachment_id),
        mime=str(stored.get("mime") or "application/octet-stream"),
        data=base64.b64encode(raw).decode("ascii"),
    )


async def _event_stream(provider: Any, chat_request: ChatRequest, search_results: dict[str, Any] | None):
    """把统一事件序列化成 SSE。

    事件 dataclass 都带一个默认的 ``type`` 字段，``asdict`` 出来即可直接给前端。
    搜索结果（若有）排在正文之前发出，前端据此渲染参考来源。
    """
    # 先发搜索事件（成功或失败）
    if search_results:
        if "error" in search_results:
            # 搜索失败，发 warning
            event_data = {"type": "warning", "message": f"搜索失败：{search_results['error']}"}
            yield f"data: {json.dumps(event_data)}\n\n"
        else:
            # 搜索成功，发 search 事件
            event_data = {"type": "search", **search_results}
            yield f"data: {json.dumps(event_data)}\n\n"

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
        async for event in provider.stream(chat_request, client=client):
            payload = asdict(event)
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
