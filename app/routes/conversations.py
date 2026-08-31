"""会话与消息的 CRUD 端点。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import attachments, store

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


@router.get("")
async def get_conversations() -> list[dict[str, Any]]:
    """会话列表，最近活跃的在前。"""
    return store.list_conversations()


class CreateConversationRequest(BaseModel):
    """新建会话请求体。标题留空则自动编号为「对话N」。"""

    title: str | None = None


@router.post("")
async def create_conversation(request: CreateConversationRequest) -> dict[str, Any]:
    """新建会话。"""
    return store.create_conversation(request.title)


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict[str, Any]:
    """取会话详情与全部消息。"""
    conversation = store.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {**conversation, "messages": store.list_messages(conversation_id)}


class RenameConversationRequest(BaseModel):
    """重命名请求体。"""

    title: str


@router.patch("/{conversation_id}")
async def rename_conversation(
    conversation_id: str, request: RenameConversationRequest
) -> dict[str, Any]:
    """改会话标题。"""
    conversation = store.rename_conversation(conversation_id, request.title)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str) -> dict[str, str]:
    """删会话及其消息，磁盘上的附件一并清掉。"""
    # 先把附件 id 取出来：会话一删，消息行就随外键级联消失，之后再问就问不到了
    attachment_ids = store.conversation_attachment_ids(conversation_id)
    if not store.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    attachments.remove(attachment_ids)
    return {"message": "会话已删除"}


class AddMessageRequest(BaseModel):
    """追加消息请求体。``attachments`` 是上传端点返回的元数据数组。"""

    role: str
    content: str
    thinking: str | None = None
    attachments: list[dict[str, Any]] = []
    origin: dict[str, Any] | None = None


@router.post("/{conversation_id}/messages")
async def add_message(
    conversation_id: str, request: AddMessageRequest
) -> dict[str, Any]:
    """往会话末尾追加一条消息。"""
    if request.role not in ("user", "assistant"):
        raise HTTPException(status_code=400, detail="role 只能是 user 或 assistant")
    message = store.add_message(
        conversation_id,
        request.role,
        request.content,
        request.thinking,
        _clean_attachments(request.attachments),
        request.origin,
    )
    if message is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return message


@router.delete("/{conversation_id}/messages/{message_id}")
async def delete_messages_from(
    conversation_id: str, message_id: int
) -> dict[str, Any]:
    """删掉这条消息及其之后的所有消息。

    编辑提问与重新生成回答都用它：前端先把这条之后的历史砍掉，再重新走一轮对话。
    """
    result = store.delete_messages_from(conversation_id, message_id)
    if result is None:
        raise HTTPException(status_code=404, detail="消息不存在")
    deleted, attachment_ids = result
    attachments.remove(attachment_ids)
    return {"deleted": deleted}


def _clean_attachments(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只留下真实存在于磁盘上的附件元数据。

    元数据由前端回传，可能带上已被清理或压根不存在的 id；存进消息行只会让历史
    里出现永远加载不出来的空附件，不如当场丢掉。
    """
    cleaned: list[dict[str, Any]] = []
    for item in items:
        meta = attachments.load_meta(item.get("id"))
        if meta is not None:
            cleaned.append(meta)
    return cleaned
