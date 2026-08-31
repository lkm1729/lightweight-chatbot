"""四套后端协议的适配器。

注册表放在包的 ``__init__`` 而非 ``base``：适配器都要 import ``base``，反向引用
会构成循环导入。路由层只需 ``get_provider(name)``，不必关心具体类。
"""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .base import (
    ChatMessage,
    ChatRequest,
    DoneEvent,
    ErrorEvent,
    Provider,
    ProviderConfig,
    ProbeResult,
    TextDelta,
    ThinkingDelta,
    UsageEvent,
    WarningEvent,
)
from .content import Attachment
from .gemini import GeminiProvider
from .openai_chat import OpenAIChatProvider
from .openai_responses import OpenAIResponsesProvider

PROVIDERS: dict[str, type[Provider]] = {
    cls.name: cls
    for cls in (
        AnthropicProvider,
        OpenAIChatProvider,
        OpenAIResponsesProvider,
        GeminiProvider,
    )
}


class UnknownProviderError(ValueError):
    """请求了不存在的协议名。"""


def get_provider(name: str) -> type[Provider]:
    """按协议名取适配器类。"""
    try:
        return PROVIDERS[name]
    except KeyError:
        raise UnknownProviderError(
            f"未知的协议 {name!r}，可选：{', '.join(PROVIDERS)}"
        ) from None


def list_providers() -> list[dict[str, str]]:
    """供前端下拉框使用的协议清单。"""
    return [{"name": cls.name, "label": cls.label} for cls in PROVIDERS.values()]


__all__ = [
    "PROVIDERS",
    "AnthropicProvider",
    "Attachment",
    "ChatMessage",
    "ChatRequest",
    "DoneEvent",
    "ErrorEvent",
    "GeminiProvider",
    "OpenAIChatProvider",
    "OpenAIResponsesProvider",
    "ProbeResult",
    "Provider",
    "ProviderConfig",
    "TextDelta",
    "ThinkingDelta",
    "UnknownProviderError",
    "UsageEvent",
    "WarningEvent",
    "get_provider",
    "list_providers",
]
