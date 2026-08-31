"""Anthropic Messages 适配器。

上游事件带 ``event:`` 名，正文在 ``content_block_delta.delta.text``，思维链在
同一事件的 ``thinking`` 字段里。``max_tokens`` 是必填项，未指定时兜底。

推理强度走顶层 ``effort``（``low``…``max``）配合 ``thinking: {"type": "adaptive"}``；
不认这套参数的老模型由 ``build_legacy_payload`` 退回 ``thinking.budget_tokens``。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar

from app import reasoning as reasoning_levels

from . import content as attachment_content
from .base import (
    ChatMessage,
    ChatRequest,
    DoneEvent,
    ErrorEvent,
    Provider,
    SSEMessage,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    UsageEvent,
)

# Anthropic 要求显式给出 max_tokens
DEFAULT_MAX_TOKENS = 4096

# 开思考时 max_tokens 必须大于 budget_tokens，留出这么多给正文
ANSWER_HEADROOM = 4096

API_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    name: ClassVar[str] = "anthropic"
    label: ClassVar[str] = "Anthropic Messages"
    version_segment: ClassVar[str] = "v1"
    api_path: ClassVar[str] = "messages"
    endpoint_markers: ClassVar[tuple[str, ...]] = ("/messages",)

    def headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "x-api-key": self.config.api_key,
            "anthropic-version": API_VERSION,
            **self.config.extra_headers,
        }

    def _base_payload(self, request: ChatRequest) -> dict[str, Any]:
        """两套写法共用的部分：模型、消息、system。"""
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": m.role, "content": _content(m)} for m in request.messages
            ],
            "stream": True,
        }
        if request.system:
            payload["system"] = request.system
        return payload

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        level = reasoning_levels.resolve(request.reasoning)
        payload = self._base_payload(request)
        max_tokens = request.max_tokens or DEFAULT_MAX_TOKENS

        effort = level.anthropic.value
        if effort is None:
            # 新模型默认开着自适应思考，不显式关掉它就还会思考
            payload["thinking"] = {"type": "disabled"}
            if request.temperature is not None:
                payload["temperature"] = request.temperature
        else:
            payload["effort"] = effort
            payload["thinking"] = {"type": "adaptive"}
            # 思考 token 也算在 max_tokens 里，高档位下不顶上去正文会被截断
            max_tokens = max(max_tokens, level.budget + ANSWER_HEADROOM)
            # 思考模式下 temperature 只接受 1，索性不发，让上游用默认值

        payload["max_tokens"] = max_tokens
        return payload

    def build_legacy_payload(self, request: ChatRequest) -> dict[str, Any]:
        """扩展思考的旧写法：按 token 预算给 ``thinking.budget_tokens``。"""
        level = reasoning_levels.resolve(request.reasoning)
        payload = self._base_payload(request)
        max_tokens = request.max_tokens or DEFAULT_MAX_TOKENS

        if level.budget > 0:
            payload["thinking"] = {"type": "enabled", "budget_tokens": level.budget}
            # 上游校验 max_tokens > budget_tokens，预算大时得把上限顶上去
            max_tokens = max(max_tokens, level.budget + ANSWER_HEADROOM)
        elif request.temperature is not None:
            payload["temperature"] = request.temperature

        payload["max_tokens"] = max_tokens
        return payload

    def payload_warnings(self, request: ChatRequest) -> tuple[str, ...]:
        note = reasoning_levels.resolve(request.reasoning).anthropic.note
        return (note,) if note else ()

    def parse_message(self, message: SSEMessage) -> Iterable[StreamEvent]:
        data = message.json()
        if not isinstance(data, dict):
            return ()
        kind = message.event or data.get("type")

        if kind == "content_block_delta":
            return _parse_delta(data.get("delta"))
        if kind == "message_start":
            usage = (data.get("message") or {}).get("usage") or {}
            return _parse_usage(usage)
        if kind == "message_delta":
            events: list[StreamEvent] = list(_parse_usage(data.get("usage") or {}))
            reason = (data.get("delta") or {}).get("stop_reason")
            events.append(DoneEvent(finish_reason=reason))
            return events
        if kind == "error":
            error = data.get("error") or {}
            return (ErrorEvent(message=error.get("message") or "上游返回未知错误"),)
        return ()


def _content(message: ChatMessage) -> str | list[dict[str, Any]]:
    """消息正文。没有附件时保持纯字符串，行为与改动前一致。

    附件排在提问前面：图片与 PDF 走原生块，其余解码成文本块。
    """
    if not message.attachments:
        return message.content

    parts: list[dict[str, Any]] = []
    for attachment in message.attachments:
        if attachment_content.is_image(attachment):
            parts.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": attachment.mime,
                        "data": attachment.data,
                    },
                }
            )
        elif attachment_content.is_pdf(attachment):
            parts.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": attachment.data,
                    },
                }
            )
        elif attachment_content.is_text(attachment):
            parts.append(
                {"type": "text", "text": attachment_content.as_text_block(attachment)}
            )
        else:
            parts.append(
                {"type": "text", "text": attachment_content.unsupported_note(attachment)}
            )

    if message.content:
        parts.append({"type": "text", "text": message.content})
    return parts


def _parse_delta(delta: Any) -> tuple[StreamEvent, ...]:
    """``content_block_delta`` 的正文 / 思维链增量。signature_delta 等直接忽略。"""
    if not isinstance(delta, dict):
        return ()
    text = delta.get("text")
    if isinstance(text, str) and text:
        return (TextDelta(text=text),)
    thinking = delta.get("thinking")
    if isinstance(thinking, str) and thinking:
        return (ThinkingDelta(text=thinking),)
    return ()


def _parse_usage(usage: Any) -> tuple[StreamEvent, ...]:
    if not isinstance(usage, dict):
        return ()
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None and output_tokens is None:
        return ()
    return (UsageEvent(input_tokens=input_tokens, output_tokens=output_tokens),)
