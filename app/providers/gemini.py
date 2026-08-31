"""Gemini 适配器。

Gemini 的差异最大：模型名写在**路径**里（``models/{model}:streamGenerateContent``），
必须带 ``?alt=sse`` 才是标准 SSE，鉴权头是 ``x-goog-api-key``，assistant 角色叫
``model``，字段名一律小驼峰。事件没有 ``event:`` 名，每条 data 都是一个完整的
``GenerateContentResponse`` 片段。

推理强度走 ``thinkingConfig.thinkingLevel``（``low|medium|high``）；它与
``thinkingBudget`` 互斥，同时给会被上游拒，所以两套写法各发一个。不认
``thinkingLevel`` 的老模型（2.5 系列）由 ``build_legacy_payload`` 退回
``thinkingBudget``。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
from .url import ResolvedEndpoint

# 不加这个参数上游会返回一整个 JSON 数组而非 SSE
SSE_PARAM = ("alt", "sse")


class GeminiProvider(Provider):
    name: ClassVar[str] = "gemini"
    label: ClassVar[str] = "Gemini"
    version_segment: ClassVar[str] = "v1beta"
    # 真实路径按请求动态拼装，见 resolve_api_path
    api_path: ClassVar[str] = "models"
    endpoint_markers: ClassVar[tuple[str, ...]] = (
        ":generateContent",
        ":streamGenerateContent",
    )

    def resolve_api_path(self, request: ChatRequest) -> str:
        return f"models/{request.model}:streamGenerateContent"

    def endpoint(self, request: ChatRequest) -> ResolvedEndpoint:
        """在归一化结果上补 ``alt=sse``，保留用户 Base URL 里原有的 query。"""
        resolved = super().endpoint(request)
        return ResolvedEndpoint(_with_sse(resolved.url), resolved.warnings)

    def headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "x-goog-api-key": self.config.api_key,
            **self.config.extra_headers,
        }

    def _base_payload(self, request: ChatRequest) -> dict[str, Any]:
        """两套写法共用的部分：contents、systemInstruction、生成参数。"""
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": _parts(m),
                }
                for m in request.messages
            ]
        }
        if request.system:
            payload["systemInstruction"] = {"parts": [{"text": request.system}]}

        config: dict[str, Any] = {}
        if request.temperature is not None:
            config["temperature"] = request.temperature
        if request.max_tokens is not None:
            config["maxOutputTokens"] = request.max_tokens
        payload["generationConfig"] = config
        return payload

    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        level = reasoning_levels.resolve(request.reasoning)
        payload = self._base_payload(request)
        thinking: dict[str, Any] = {"includeThoughts": level.budget > 0}
        if level.gemini.value is None:
            # thinkingLevel 没有「关闭」这一档，只能靠预算 0 表达
            thinking["thinkingBudget"] = 0
        else:
            thinking["thinkingLevel"] = level.gemini.value
        payload["generationConfig"]["thinkingConfig"] = thinking
        return payload

    def build_legacy_payload(self, request: ChatRequest) -> dict[str, Any]:
        """2.5 系列的旧写法：按 token 预算给 ``thinkingBudget``。"""
        level = reasoning_levels.resolve(request.reasoning)
        payload = self._base_payload(request)
        # budget 0 即明确关闭思考，因此这里不能用 `if level.budget` 跳过
        payload["generationConfig"]["thinkingConfig"] = {
            "thinkingBudget": level.budget,
            "includeThoughts": level.budget > 0,
        }
        return payload

    def payload_warnings(self, request: ChatRequest) -> tuple[str, ...]:
        note = reasoning_levels.resolve(request.reasoning).gemini.note
        return (note,) if note else ()

    def parse_message(self, message: SSEMessage) -> Iterable[StreamEvent]:
        data = message.json()
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return ()

        error = data.get("error")
        if isinstance(error, dict):
            return (
                ErrorEvent(
                    message=str(error.get("message") or "上游返回未知错误"),
                    status=error.get("code") if isinstance(error.get("code"), int) else None,
                ),
            )

        events: list[StreamEvent] = []

        blocked = (data.get("promptFeedback") or {}).get("blockReason")
        if blocked:
            return (ErrorEvent(message=f"请求被上游安全策略拦截：{blocked}"),)

        candidates = data.get("candidates")
        candidate = (
            candidates[0]
            if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict)
            else {}
        )
        events.extend(_parse_parts((candidate.get("content") or {}).get("parts")))

        usage = data.get("usageMetadata")
        if isinstance(usage, dict):
            events.extend(_parse_usage(usage))

        reason = candidate.get("finishReason")
        if isinstance(reason, str) and reason:
            events.append(DoneEvent(finish_reason=reason))

        return events


def _parts(message: ChatMessage) -> list[dict[str, Any]]:
    """一条消息的 parts。没有附件时就是单个文本 part，与改动前一致。

    图片与 PDF 走 ``inlineData``，其余解码成文本 part。
    """
    if not message.attachments:
        return [{"text": message.content}]

    parts: list[dict[str, Any]] = []
    for attachment in message.attachments:
        if attachment_content.is_image(attachment) or attachment_content.is_pdf(attachment):
            parts.append(
                {
                    "inlineData": {
                        "mimeType": attachment.mime or "application/octet-stream",
                        "data": attachment.data,
                    }
                }
            )
        elif attachment_content.is_text(attachment):
            parts.append({"text": attachment_content.as_text_block(attachment)})
        else:
            parts.append({"text": attachment_content.unsupported_note(attachment)})

    if message.content:
        parts.append({"text": message.content})
    return parts


def _parse_parts(parts: Any) -> tuple[StreamEvent, ...]:
    """``parts[].text``；带 ``thought: true`` 的是思维链而非正文。"""
    if not isinstance(parts, list):
        return ()
    events: list[StreamEvent] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if not isinstance(text, str) or not text:
            continue
        events.append(
            ThinkingDelta(text=text) if part.get("thought") else TextDelta(text=text)
        )
    return tuple(events)


def _parse_usage(usage: dict[str, Any]) -> tuple[StreamEvent, ...]:
    input_tokens = usage.get("promptTokenCount")
    output_tokens = usage.get("candidatesTokenCount")
    if input_tokens is None and output_tokens is None:
        return ()
    return (UsageEvent(input_tokens=input_tokens, output_tokens=output_tokens),)


def _with_sse(url: str) -> str:
    """幂等地加上 ``alt=sse``，用户自己写了就不重复添加。"""
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    if not any(key.lower() == SSE_PARAM[0] for key, _ in query):
        query.append(SSE_PARAM)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
