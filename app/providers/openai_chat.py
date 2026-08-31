"""OpenAI Chat Completions 适配器。

上游事件没有 ``event:`` 名，正文在 ``choices[0].delta.content``，流末尾是
``data: [DONE]`` 哨兵（非 JSON，``SSEMessage.json()`` 会返回 None 而被忽略）。
思维链字段各家不一：官方 o 系列走 ``reasoning``，DeepSeek / OpenRouter 走
``reasoning_content``，两者都认。

推理强度走 ``reasoning_effort``，认 ``none|minimal|low|medium|high|xhigh``；
只认老四级的模型由 ``build_legacy_payload`` 退回 ``minimal|low|medium|high``。
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
    Provider,
    SSEMessage,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    UsageEvent,
)


class OpenAIChatProvider(Provider):
    name: ClassVar[str] = "openai_chat"
    label: ClassVar[str] = "OpenAI Chat Completions"
    version_segment: ClassVar[str] = "v1"
    api_path: ClassVar[str] = "chat/completions"
    endpoint_markers: ClassVar[tuple[str, ...]] = ("/chat/completions",)

    def headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self.config.api_key}",
            **self.config.extra_headers,
        }

    def _base_payload(self, request: ChatRequest) -> dict[str, Any]:
        """两套写法共用的部分：模型、消息、流式选项、temperature、max_tokens。"""
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": _content(m)} for m in request.messages)

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "stream": True,
            # 流式默认不返回 usage，需显式索取
            "stream_options": {"include_usage": True},
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        return payload

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        level = reasoning_levels.resolve(request.reasoning)
        payload = self._base_payload(request)
        if level.openai.value:
            payload["reasoning_effort"] = level.openai.value
        return payload

    def build_legacy_payload(self, request: ChatRequest) -> dict[str, Any]:
        """老模型只认 minimal/low/medium/high，且不接受 ``none``。"""
        level = reasoning_levels.resolve(request.reasoning)
        payload = self._base_payload(request)
        effort = reasoning_levels.legacy_openai_effort(level)
        if effort:
            payload["reasoning_effort"] = effort
        return payload

    def payload_warnings(self, request: ChatRequest) -> tuple[str, ...]:
        note = reasoning_levels.resolve(request.reasoning).openai.note
        return (note,) if note else ()

    def parse_message(self, message: SSEMessage) -> Iterable[StreamEvent]:
        data = message.json()
        if not isinstance(data, dict):
            return ()

        events: list[StreamEvent] = []

        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            choice = choices[0] if isinstance(choices[0], dict) else {}
            events.extend(_parse_delta(choice.get("delta")))
            reason = choice.get("finish_reason")
            if reason:
                events.append(DoneEvent(finish_reason=reason))

        usage = data.get("usage")
        if isinstance(usage, dict):
            events.extend(_parse_usage(usage))

        return events


def _content(message: ChatMessage) -> str | list[dict[str, Any]]:
    """消息正文。没有附件时保持纯字符串，行为与改动前一致。"""
    if not message.attachments:
        return message.content

    parts: list[dict[str, Any]] = []
    for attachment in message.attachments:
        if attachment_content.is_image(attachment):
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": attachment_content.data_url(attachment)},
                }
            )
        elif attachment_content.is_pdf(attachment):
            parts.append(
                {
                    "type": "file",
                    "file": {
                        "filename": attachment.name,
                        "file_data": attachment_content.data_url(attachment),
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
    if not isinstance(delta, dict):
        return ()
    events: list[StreamEvent] = []
    # 思维链先于正文，保持上游顺序
    for key in ("reasoning_content", "reasoning"):
        value = delta.get(key)
        if isinstance(value, str) and value:
            events.append(ThinkingDelta(text=value))
            break
    content = delta.get("content")
    if isinstance(content, str) and content:
        events.append(TextDelta(text=content))
    return tuple(events)


def _parse_usage(usage: dict[str, Any]) -> tuple[StreamEvent, ...]:
    input_tokens = usage.get("prompt_tokens")
    output_tokens = usage.get("completion_tokens")
    if input_tokens is None and output_tokens is None:
        return ()
    return (UsageEvent(input_tokens=input_tokens, output_tokens=output_tokens),)
