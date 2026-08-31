"""四套后端协议的统一抽象。

上游 SSE 方言互不兼容：Anthropic 用 `content_block_delta`，OpenAI Chat 用
`choices[].delta`，OpenAI Responses 用 `response.output_text.delta`，Gemini 用
`candidates[].content.parts`。本模块把它们统一成一条内部事件流，路由层与前端
只需处理一种格式。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any, ClassVar

import httpx

from .content import Attachment
from .url import ResolvedEndpoint, resolve_endpoint

# --------------------------------------------------------------------------
# 内部统一事件
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    """正文增量。"""

    text: str
    type: str = "text"


@dataclass(frozen=True)
class ThinkingDelta:
    """思维链 / reasoning 增量。"""

    text: str
    type: str = "thinking"


@dataclass(frozen=True)
class UsageEvent:
    """token 用量，上游给了才发。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    type: str = "usage"


@dataclass(frozen=True)
class ErrorEvent:
    """上游或本地错误，不抛异常而是入流，让前端能渲染出来。"""

    message: str
    status: int | None = None
    type: str = "error"


@dataclass(frozen=True)
class WarningEvent:
    """非致命提示（如 Base URL 版本段与官方默认不一致），不中断对话。"""

    message: str
    type: str = "warning"


@dataclass(frozen=True)
class DoneEvent:
    """流正常结束。"""

    finish_reason: str | None = None
    type: str = "done"


StreamEvent = (
    TextDelta | ThinkingDelta | UsageEvent | WarningEvent | ErrorEvent | DoneEvent
)


# 回退重试时告诉用户发生了什么，免得同样的档位在不同模型上表现不一致却无从得知
LEGACY_FALLBACK_NOTE = (
    "该模型不认识当前的推理档位参数，已改用旧写法（按 token 预算）重发"
)

# 上游 400 正文里出现这些词，就认为它是在拒收新档位参数而非拒收整个请求。
# 刻意不含笼统的 "thinking" / "reasoning"：那些词在无关报错里也常出现，
# 会招来一次白跑的重试。
_REASONING_PARAM_HINTS = (
    "effort",
    "thinking_level",
    "thinkinglevel",
    "adaptive",
    "xhigh",
)


def _rejects_reasoning_params(detail: str) -> bool:
    """判断上游的 400 是不是冲着推理档位参数来的。"""
    lowered = detail.lower()
    return any(hint in lowered for hint in _REASONING_PARAM_HINTS)



# --------------------------------------------------------------------------
# 请求模型
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    """一条对话消息。role 为 ``user`` 或 ``assistant``。

    ``attachments`` 只在 user 消息上出现——四家都不接受 assistant 带附件，
    路由层在构造请求时就把它们剔掉了。
    """

    role: str
    content: str
    attachments: tuple[Attachment, ...] = ()


@dataclass(frozen=True)
class ChatRequest:
    """与协议无关的对话请求，由各适配器翻译成上游 payload。

    ``reasoning`` 是 ``app.reasoning`` 里的档位 key（``none``…``max``），
    各适配器按自己协议的表达方式落实。
    """

    model: str
    messages: tuple[ChatMessage, ...]
    system: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning: str = "medium"


@dataclass(frozen=True)
class ProviderConfig:
    """一个后端渠道的配置。``api_key`` 为明文，切勿原样回传前端。"""

    base_url: str
    api_key: str
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeResult:
    """连通性测试结果，可直接序列化给前端（不含任何密钥）。"""

    ok: bool
    endpoint: str
    error: str | None = None
    status: int | None = None
    warnings: tuple[str, ...] = field(default=())


# --------------------------------------------------------------------------
# SSE 解析
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SSEMessage:
    """一条 SSE 消息：``event:`` 名（可能没有）与拼好的 ``data:`` 正文。"""

    event: str | None
    data: str

    def json(self) -> Any:
        """解析 data 为 JSON；非 JSON 返回 None，交由适配器忽略。"""
        try:
            return json.loads(self.data)
        except (ValueError, TypeError):
            return None


async def iter_sse(response: httpx.Response) -> AsyncIterator[SSEMessage]:
    """把 HTTP 响应按 SSE 规范切成消息。

    多行 ``data:`` 会按换行拼接；空行表示一条消息结束；``:`` 开头的注释行
    （心跳）直接丢弃。
    """
    event: str | None = None
    data: list[str] = []

    async for raw in response.aiter_lines():
        line = raw.rstrip("\r")
        if not line:
            if data:
                yield SSEMessage(event, "\n".join(data))
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            event = value
        elif name == "data":
            data.append(value)

    if data:
        yield SSEMessage(event, "\n".join(data))


# --------------------------------------------------------------------------
# 适配器基类
# --------------------------------------------------------------------------


class Provider(ABC):
    """一套后端协议的适配器。

    子类只需描述「端点长什么样、请求头怎么填、payload 怎么拼、SSE 怎么读」，
    连接与流式循环由本类统一处理。
    """

    name: ClassVar[str]
    label: ClassVar[str]
    version_segment: ClassVar[str]
    api_path: ClassVar[str]
    endpoint_markers: ClassVar[tuple[str, ...]] = ()

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    # -- 子类定制点 --------------------------------------------------------

    def resolve_api_path(self, request: ChatRequest) -> str:
        """协议路径。Gemini 需要把 model 拼进路径，故留出这个钩子。"""
        return self.api_path

    def endpoint(self, request: ChatRequest) -> ResolvedEndpoint:
        return resolve_endpoint(
            self.config.base_url,
            api_path=self.resolve_api_path(request),
            version_segment=self.version_segment,
            endpoint_markers=self.endpoint_markers,
        )

    @abstractmethod
    def headers(self) -> dict[str, str]:
        """鉴权与协议头。"""

    @abstractmethod
    def build_payload(self, request: ChatRequest) -> dict[str, Any]:
        """上游请求体，需自行开启流式。"""

    def build_legacy_payload(self, request: ChatRequest) -> dict[str, Any] | None:
        """按旧写法表达推理强度的 payload，供老模型回退用。

        ``build_payload`` 发的是各协议当下的档位参数（Anthropic 的 ``effort``、
        Gemini 的 ``thinkingLevel`` 等），较老的模型不认识，收到会直接 400。
        这时 ``stream()`` 会拿这里的旧写法（按 token 预算 / 四级 effort）重发一次。

        返回 None 表示没有可回退的变体。
        """
        return None

    def payload_warnings(self, request: ChatRequest) -> tuple[str, ...]:
        """构造 payload 时值得提醒用户的非致命问题。

        目前用于推理档位在本协议下没有对等值、只能取最近一档的情形（Anthropic
        没有 minimal、OpenAI 没有 max、Gemini 只有三级），免得用户以为选了
        「最高」却没生效。
        """
        return ()

    @abstractmethod
    def parse_message(self, message: SSEMessage) -> Iterable[StreamEvent]:
        """把一条上游 SSE 消息翻译成零个或多个统一事件。"""

    # -- 统一流式循环 ------------------------------------------------------

    async def stream(
        self,
        request: ChatRequest,
        *,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[StreamEvent]:
        """向上游发起流式请求，产出统一事件。

        错误不抛出而是以 ``ErrorEvent`` 入流，前端因此能把上游报错渲染进对话。

        老模型不认新档位参数时会被 400 拒掉，这时改用 ``build_legacy_payload``
        的旧写法重发一次。重试只发生在还没吐出任何正文之前，因此不会出现半段
        答案重来。
        """
        try:
            resolved = self.endpoint(request)
        except ValueError as exc:  # InvalidBaseURLError
            yield ErrorEvent(message=str(exc))
            return

        for warning in resolved.warnings:
            yield WarningEvent(message=warning)
        for warning in self.payload_warnings(request):
            yield WarningEvent(message=warning)

        attempts = [self.build_payload(request)]
        legacy = self.build_legacy_payload(request)
        if legacy is not None and legacy != attempts[0]:
            attempts.append(legacy)

        # done 必须是流的最后一个事件：OpenAI 的 usage 分片在 finish_reason
        # 之后才到，直接透传会让前端在收到 done 后丢掉用量。
        pending_done: DoneEvent | None = None

        for index, payload in enumerate(attempts):
            is_last = index == len(attempts) - 1
            if index:
                yield WarningEvent(message=LEGACY_FALLBACK_NOTE)
            try:
                async with client.stream(
                    "POST",
                    resolved.url,
                    headers=self.headers(),
                    json=payload,
                ) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", "replace")
                        detail = _extract_error(body) or body[:500] or "上游返回空响应"
                        if not is_last and _rejects_reasoning_params(detail):
                            continue  # 换旧写法再试一次
                        yield ErrorEvent(message=detail, status=response.status_code)
                        return
                    async for message in iter_sse(response):
                        for event in self.parse_message(message):
                            if isinstance(event, DoneEvent):
                                pending_done = event
                                continue
                            yield event
            except httpx.HTTPError as exc:
                yield ErrorEvent(message=f"连接上游失败：{exc}")
                return
            break  # 这一轮没被拒，流已读完

        yield pending_done or DoneEvent()

    # -- 连通性测试 --------------------------------------------------------

    async def probe(
        self,
        model: str,
        *,
        client: httpx.AsyncClient,
    ) -> ProbeResult:
        """发一条极短的消息，拿到首个事件就断流。

        复用 ``stream()``，因此 Key、端点、模型名三者会被一次性验证，各适配器
        无需再写一套非流式的探测逻辑。
        """
        request = ChatRequest(
            model=model,
            messages=(ChatMessage(role="user", content="ping"),),
            max_tokens=16,
            # 连通性测试不带推理参数：这里只验 Key/端点/模型名，附加参数
            # 反而可能被不支持推理的模型拒掉，误报成「不通」
            reasoning="none",
        )
        try:
            endpoint = self.endpoint(request).url
        except ValueError as exc:  # InvalidBaseURLError
            return ProbeResult(ok=False, endpoint="", error=str(exc))

        warnings: list[str] = []
        async for event in self.stream(request, client=client):
            if isinstance(event, WarningEvent):
                warnings.append(event.message)
                continue
            if isinstance(event, ErrorEvent):
                return ProbeResult(
                    ok=False,
                    endpoint=endpoint,
                    error=event.message,
                    status=event.status,
                    warnings=tuple(warnings),
                )
            # 收到任何正文/用量/结束事件即说明链路通畅
            return ProbeResult(ok=True, endpoint=endpoint, warnings=tuple(warnings))

        return ProbeResult(
            ok=False,
            endpoint=endpoint,
            error="上游未返回任何事件",
            warnings=tuple(warnings),
        )


# --------------------------------------------------------------------------
# 错误正文解析
# --------------------------------------------------------------------------


def _extract_error(body: str) -> str | None:
    """从上游错误响应里抠出人类可读的消息。

    四家的错误体形状各异：Anthropic/OpenAI 是 ``{"error": {"message": ...}}``，
    Gemini 有时包一层数组，中转站则可能直接返回 ``{"message": ...}`` 或
    FastAPI 风格的 ``{"detail": ...}``。解析不出来就返回 None，由调用方回退到原文。
    """
    try:
        payload: Any = json.loads(body)
    except (ValueError, TypeError):
        return None

    if isinstance(payload, list):
        payload = payload[0] if payload else None
    if isinstance(payload, str):
        return payload.strip() or None
    if not isinstance(payload, dict):
        return None

    node: Any = payload.get("error", payload)
    if isinstance(node, str):
        return node.strip() or None
    if isinstance(node, dict):
        for key in ("message", "detail", "msg", "reason"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None





