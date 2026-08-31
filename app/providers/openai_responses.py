"""OpenAI Responses 适配器。

Responses API 的事件是带类型名的：正文走 ``response.output_text.delta``，
推理摘要走 ``response.reasoning_summary_text.delta``，用量在
``response.completed`` 里一次性给出。请求体与 Chat Completions 不同——
消息字段叫 ``input``，system 提示叫 ``instructions``，文本块叫 ``input_text``。

推理强度走 ``reasoning.effort``，认 ``none|minimal|low|medium|high|xhigh``；
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
    ErrorEvent,
    Provider,
    SSEMessage,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    UsageEvent,
)

TEXT_DELTA_EVENTS = frozenset({"response.output_text.delta"})

THINKING_DELTA_EVENTS = frozenset(
    {
        "response.reasoning_summary_text.delta",
        "response.reasoning_text.delta",
    }
)

TERMINAL_EVENTS = frozenset(
    {"response.completed", "response.incomplete", "response.failed"}
)


class OpenAIResponsesProvider(Provider):
    name: ClassVar[str] = "openai_responses"
    label: ClassVar[str] = "OpenAI Responses"
    version_segment: ClassVar[str] = "v1"
    api_path: ClassVar[str] = "responses"
    endpoint_markers: ClassVar[tuple[str, ...]] = ("/responses",)

    def headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self.config.api_key}",
            **self.config.extra_headers,
        }

    def _base_payload(self, request: ChatRequest) -> dict[str, Any]:
        """两套写法共用的部分：模型、input、instructions、上限。"""
        payload: dict[str, Any] = {
            "model": request.model,
            "input": [
                {"role": m.role, "content": _content(m)} for m in request.messages
            ],
            "stream": True,
        }
        if request.system:
            payload["instructions"] = request.system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_output_tokens"] = request.max_tokens
        return payload

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        level = reasoning_levels.resolve(request.reasoning)
        payload = self._base_payload(request)
        if level.openai.value:
            # summary=auto 才会有 reasoning_summary_text.delta 事件可渲染
            payload["reasoning"] = {"effort": level.openai.value, "summary": "auto"}
        return payload

    def build_legacy_payload(self, request: ChatRequest) -> dict[str, Any]:
        """老模型只认 minimal/low/medium/high，且不接受 ``none``。"""
        level = reasoning_levels.resolve(request.reasoning)
        payload = self._base_payload(request)
        effort = reasoning_levels.legacy_openai_effort(level)
        if effort:
            payload["reasoning"] = {"effort": effort, "summary": "auto"}
        return payload

    def payload_warnings(self, request: ChatRequest) -> tuple[str, ...]:
        note = reasoning_levels.resolve(request.reasoning).openai.note
        return (note,) if note else ()

    def parse_message(self, message: SSEMessage) -> Iterable[StreamEvent]:
        data = message.json()
        if not isinstance(data, dict):
            return ()
        kind = message.event or data.get("type")

        if kind in TEXT_DELTA_EVENTS:
            return _text(data, TextDelta)
        if kind in THINKING_DELTA_EVENTS:
            return _text(data, ThinkingDelta)
        if kind in TERMINAL_EVENTS:
            return _parse_terminal(data)
        if kind == "error":
            return (
                ErrorEvent(
                    message=data.get("message") or "上游返回未知错误",
                    status=data.get("code") if isinstance(data.get("code"), int) else None,
                ),
            )
        return ()


def _content(message: ChatMessage) -> str | list[dict[str, Any]]:
    """消息正文。没有附件时保持纯字符串，行为与改动前一致。"""
    if not message.attachments:
        return message.content

    parts: list[dict[str, Any]] = []
    for attachment in message.attachments:
        if attachment_content.is_image(attachment):
            parts.append(
                {
                    "type": "input_image",
                    "image_url": attachment_content.data_url(attachment),
                }
            )
        elif attachment_content.is_pdf(attachment):
            parts.append(
                {
                    "type": "input_file",
                    "filename": attachment.name,
                    "file_data": attachment_content.data_url(attachment),
                }
            )
        elif attachment_content.is_text(attachment):
            parts.append(
                {
                    "type": "input_text",
                    "text": attachment_content.as_text_block(attachment),
                }
            )
        else:
            parts.append(
                {
                    "type": "input_text",
                    "text": attachment_content.unsupported_note(attachment),
                }
            )

    if message.content:
        parts.append({"type": "input_text", "text": message.content})
    return parts


def _text(data: dict[str, Any], factory: type) -> tuple[StreamEvent, ...]:
    delta = data.get("delta")
    if isinstance(delta, str) and delta:
        return (factory(text=delta),)
    return ()


def _parse_terminal(data: dict[str, Any]) -> tuple[StreamEvent, ...]:
    """``response.completed`` 等终止事件：取出用量与结束原因。"""
    response = data.get("response")
    response = response if isinstance(response, dict) else {}

    events: list[StreamEvent] = []
    usage = response.get("usage")
    if isinstance(usage, dict):
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if input_tokens is not None or output_tokens is not None:
            events.append(
                UsageEvent(input_tokens=input_tokens, output_tokens=output_tokens)
            )

    error = response.get("error")
    if isinstance(error, dict) and error.get("message"):
        events.append(ErrorEvent(message=str(error["message"])))
        return tuple(events)

    reason = response.get("status")
    incomplete = response.get("incomplete_details")
    if isinstance(incomplete, dict) and incomplete.get("reason"):
        reason = str(incomplete["reason"])
    events.append(DoneEvent(finish_reason=reason if isinstance(reason, str) else None))
    return tuple(events)
