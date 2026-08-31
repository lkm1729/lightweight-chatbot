"""四个 provider 适配器与统一事件流的测试。

上游用 respx 拦截，因此不需要真实网络与密钥。断言集中在「上游 SSE 原文 → 统一
事件序列」这层翻译上——四套协议的差异都在这里，也最容易出错。
"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
import respx

from app.providers import (
    AnthropicProvider,
    GeminiProvider,
    OpenAIChatProvider,
    OpenAIResponsesProvider,
    UnknownProviderError,
    get_provider,
    list_providers,
)
from app.providers.anthropic import DEFAULT_MAX_TOKENS
from app.providers.base import (
    ChatMessage,
    ChatRequest,
    DoneEvent,
    ErrorEvent,
    Provider,
    ProviderConfig,
    SSEMessage,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    UsageEvent,
    WarningEvent,
    iter_sse,
)

API_KEY = "sk-test-1234567890abcd"

REQUEST = ChatRequest(
    model="test-model",
    messages=(ChatMessage(role="user", content="你好"),),
    system="你是助手",
    temperature=0.7,
    max_tokens=1024,
    # 基线用例只验通用字段的翻译，推理档位另有专门用例
    reasoning="none",
)
GEMINI_REQUEST = replace(REQUEST, model="gemini-2.5-pro")


def sse(*chunks: str) -> str:
    """把若干条 ``event:``/``data:`` 文本拼成完整 SSE 报文。"""
    return "".join(f"{chunk}\n\n" for chunk in chunks)


def sse_response(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status, text=body, headers={"content-type": "text/event-stream"}
    )


def make(cls: type[Provider], base_url: str) -> Provider:
    return cls(ProviderConfig(base_url=base_url, api_key=API_KEY))


async def collect(
    provider: Provider, request: ChatRequest = REQUEST
) -> list[StreamEvent]:
    async with httpx.AsyncClient() as client:
        return [event async for event in provider.stream(request, client=client)]


async def parse_sse(body: str) -> list[SSEMessage]:
    """``content=`` 构造的响应就能喂给 iter_sse，不必搭传输层。"""
    return [message async for message in iter_sse(httpx.Response(200, text=body))]


# --- iter_sse ---------------------------------------------------------------


async def test_iter_sse_reads_named_and_anonymous_events():
    messages = await parse_sse('event: ping\ndata: {"a": 1}\n\ndata: bare\n\n')
    assert messages == [SSEMessage("ping", '{"a": 1}'), SSEMessage(None, "bare")]
    assert messages[0].json() == {"a": 1}
    # 非 JSON（如 OpenAI 的 [DONE] 哨兵）返回 None 而不是抛异常
    assert messages[1].json() is None


async def test_iter_sse_joins_multiline_data():
    (message,) = await parse_sse("data: 第一行\ndata: 第二行\n\n")
    assert message.data == "第一行\n第二行"


async def test_iter_sse_drops_comments_and_handles_crlf():
    """``:`` 开头是心跳注释，中转站爱发；CRLF 也要能吃。"""
    assert await parse_sse(": heartbeat\r\ndata: hi\r\n\r\n") == [SSEMessage(None, "hi")]


async def test_iter_sse_flushes_unterminated_tail():
    """上游断在末尾没补空行时，缓冲区里的内容不能丢。"""
    assert await parse_sse("data: tail") == [SSEMessage(None, "tail")]


# --- Anthropic Messages -----------------------------------------------------

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

ANTHROPIC_SSE = sse(
    'event: message_start\ndata: {"type": "message_start", "message":'
    ' {"usage": {"input_tokens": 10, "output_tokens": 1}}}',
    'event: content_block_delta\ndata: {"type": "content_block_delta",'
    ' "delta": {"type": "thinking_delta", "thinking": "嗯"}}',
    'event: content_block_delta\ndata: {"type": "content_block_delta",'
    ' "delta": {"type": "text_delta", "text": "你好"}}',
    'event: message_delta\ndata: {"type": "message_delta", "delta":'
    ' {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}',
)


@respx.mock
async def test_anthropic_stream():
    route = respx.post(ANTHROPIC_URL).mock(return_value=sse_response(ANTHROPIC_SSE))
    provider = make(AnthropicProvider, "https://api.anthropic.com")

    assert await collect(provider) == [
        UsageEvent(input_tokens=10, output_tokens=1),
        ThinkingDelta(text="嗯"),
        TextDelta(text="你好"),
        UsageEvent(input_tokens=None, output_tokens=5),
        DoneEvent(finish_reason="end_turn"),
    ]

    request = route.calls.last.request
    assert request.headers["x-api-key"] == API_KEY
    assert request.headers["anthropic-version"] == "2023-06-01"

    payload = json.loads(route.calls.last.request.content)
    assert payload["stream"] is True
    assert payload["max_tokens"] == 1024
    assert payload["system"] == "你是助手"
    assert payload["messages"] == [{"role": "user", "content": "你好"}]


@respx.mock
async def test_anthropic_defaults_max_tokens():
    """Anthropic 的 max_tokens 是必填项，用户没填要兜底。"""
    route = respx.post(ANTHROPIC_URL).mock(return_value=sse_response(ANTHROPIC_SSE))
    provider = make(AnthropicProvider, "https://api.anthropic.com")

    await collect(provider, replace(REQUEST, max_tokens=None))

    payload = json.loads(route.calls.last.request.content)
    assert payload["max_tokens"] == DEFAULT_MAX_TOKENS


@respx.mock
async def test_anthropic_in_stream_error():
    body = sse(
        'event: error\ndata: {"type": "error", "error":'
        ' {"type": "overloaded_error", "message": "上游过载"}}'
    )
    respx.post(ANTHROPIC_URL).mock(return_value=sse_response(body))
    provider = make(AnthropicProvider, "https://api.anthropic.com")

    # 流内错误照样补一个 done，前端才能收尾
    assert await collect(provider) == [
        ErrorEvent(message="上游过载"),
        DoneEvent(),
    ]


# --- OpenAI Chat Completions ------------------------------------------------

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

OPENAI_CHAT_SSE = sse(
    'data: {"choices": [{"delta": {"reasoning_content": "思考"}}]}',
    'data: {"choices": [{"delta": {"content": "你好"}}]}',
    'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}',
    'data: {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}',
    "data: [DONE]",
)


@respx.mock
async def test_openai_chat_stream_puts_usage_before_done():
    """usage 分片在 finish_reason 之后才到，done 必须被推到最后。"""
    route = respx.post(OPENAI_CHAT_URL).mock(
        return_value=sse_response(OPENAI_CHAT_SSE)
    )
    provider = make(OpenAIChatProvider, "https://api.openai.com")

    assert await collect(provider) == [
        ThinkingDelta(text="思考"),
        TextDelta(text="你好"),
        UsageEvent(input_tokens=10, output_tokens=5),
        DoneEvent(finish_reason="stop"),
    ]

    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {API_KEY}"

    payload = json.loads(request.content)
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    # system 提示以 system 消息的形式塞在最前面
    assert payload["messages"] == [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ]
    assert payload["max_tokens"] == 1024
    assert payload["temperature"] == 0.7


@respx.mock
async def test_openai_chat_accepts_reasoning_alias():
    """官方 o 系列用 reasoning，DeepSeek / OpenRouter 用 reasoning_content。"""
    body = sse('data: {"choices": [{"delta": {"reasoning": "推理"}}]}')
    respx.post(OPENAI_CHAT_URL).mock(return_value=sse_response(body))
    provider = make(OpenAIChatProvider, "https://api.openai.com")

    assert await collect(provider) == [ThinkingDelta(text="推理"), DoneEvent()]


# --- OpenAI Responses -------------------------------------------------------

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

OPENAI_RESPONSES_SSE = sse(
    'event: response.reasoning_summary_text.delta\ndata: {"type":'
    ' "response.reasoning_summary_text.delta", "delta": "思考"}',
    'event: response.output_text.delta\ndata: {"type":'
    ' "response.output_text.delta", "delta": "你好"}',
    'event: response.completed\ndata: {"type": "response.completed",'
    ' "response": {"status": "completed", "usage": {"input_tokens": 10,'
    ' "output_tokens": 5}}}',
)


@respx.mock
async def test_openai_responses_stream():
    route = respx.post(OPENAI_RESPONSES_URL).mock(
        return_value=sse_response(OPENAI_RESPONSES_SSE)
    )
    provider = make(OpenAIResponsesProvider, "https://api.openai.com")

    assert await collect(provider) == [
        ThinkingDelta(text="思考"),
        TextDelta(text="你好"),
        UsageEvent(input_tokens=10, output_tokens=5),
        DoneEvent(finish_reason="completed"),
    ]

    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {API_KEY}"

    payload = json.loads(request.content)
    # Responses 的字段名与 Chat Completions 不同
    assert payload["input"] == [{"role": "user", "content": "你好"}]
    assert payload["instructions"] == "你是助手"
    assert payload["max_output_tokens"] == 1024
    assert "messages" not in payload
    assert "max_tokens" not in payload


@respx.mock
async def test_openai_responses_incomplete_reason_wins():
    """截断时 incomplete_details.reason 比 status 更具体。"""
    body = sse(
        'event: response.incomplete\ndata: {"type": "response.incomplete",'
        ' "response": {"status": "incomplete", "incomplete_details":'
        ' {"reason": "max_output_tokens"}}}'
    )
    respx.post(OPENAI_RESPONSES_URL).mock(return_value=sse_response(body))
    provider = make(OpenAIResponsesProvider, "https://api.openai.com")

    assert await collect(provider) == [DoneEvent(finish_reason="max_output_tokens")]


# --- Gemini -----------------------------------------------------------------

GEMINI_BASE = "https://generativelanguage.googleapis.com"
GEMINI_URL = (
    f"{GEMINI_BASE}/v1beta/models/gemini-2.5-pro:streamGenerateContent?alt=sse"
)

GEMINI_SSE = sse(
    'data: {"candidates": [{"content": {"parts": [{"text": "想",'
    ' "thought": true}, {"text": "你好"}]}}],'
    ' "usageMetadata": {"promptTokenCount": 10}}',
    'data: {"candidates": [{"content": {"parts": [{"text": "！"}]},'
    ' "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10,'
    ' "candidatesTokenCount": 5}}',
)


@respx.mock
async def test_gemini_stream():
    route = respx.post(GEMINI_URL).mock(return_value=sse_response(GEMINI_SSE))
    provider = make(GeminiProvider, GEMINI_BASE)

    assert await collect(provider, GEMINI_REQUEST) == [
        ThinkingDelta(text="想"),
        TextDelta(text="你好"),
        UsageEvent(input_tokens=10, output_tokens=None),
        TextDelta(text="！"),
        UsageEvent(input_tokens=10, output_tokens=5),
        DoneEvent(finish_reason="STOP"),
    ]

    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == API_KEY
    assert "authorization" not in request.headers

    payload = json.loads(request.content)
    # 模型名在路径里，不在 body 里
    assert "model" not in payload
    assert payload["contents"] == [{"role": "user", "parts": [{"text": "你好"}]}]
    assert payload["systemInstruction"] == {"parts": [{"text": "你是助手"}]}
    assert payload["generationConfig"] == {
        "temperature": 0.7,
        "maxOutputTokens": 1024,
        # 「无」档要显式把预算写成 0，否则上游会按模型默认值自行思考
        "thinkingConfig": {"thinkingBudget": 0, "includeThoughts": False},
    }


@respx.mock
async def test_gemini_maps_assistant_role_to_model():
    route = respx.post(GEMINI_URL).mock(return_value=sse_response(GEMINI_SSE))
    provider = make(GeminiProvider, GEMINI_BASE)

    await collect(
        provider,
        replace(
            GEMINI_REQUEST,
            messages=(
                ChatMessage(role="user", content="一"),
                ChatMessage(role="assistant", content="二"),
                ChatMessage(role="user", content="三"),
            ),
        ),
    )

    payload = json.loads(route.calls.last.request.content)
    assert [c["role"] for c in payload["contents"]] == ["user", "model", "user"]


@respx.mock
async def test_gemini_does_not_duplicate_alt_param():
    """用户 Base URL 自带 alt 时不能再追加一个。"""
    route = respx.post(GEMINI_URL).mock(return_value=sse_response(GEMINI_SSE))
    provider = make(GeminiProvider, f"{GEMINI_BASE}/v1beta?alt=sse")

    await collect(provider, GEMINI_REQUEST)

    assert route.calls.last.request.url.params.get_list("alt") == ["sse"]


@respx.mock
async def test_gemini_reports_block_reason():
    body = sse('data: {"promptFeedback": {"blockReason": "SAFETY"}}')
    respx.post(GEMINI_URL).mock(return_value=sse_response(body))
    provider = make(GeminiProvider, GEMINI_BASE)

    assert await collect(provider, GEMINI_REQUEST) == [
        ErrorEvent(message="请求被上游安全策略拦截：SAFETY"),
        DoneEvent(),
    ]


# --- stream() 的通用行为 -----------------------------------------------------


@respx.mock
async def test_http_error_yields_error_without_done():
    """HTTP 层就失败时不补 done：请求根本没开始，前端应显示为失败而非完成。"""
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "invalid api key"}}
        )
    )
    provider = make(AnthropicProvider, "https://api.anthropic.com")

    assert await collect(provider) == [
        ErrorEvent(message="invalid api key", status=400)
    ]


@respx.mock
async def test_connection_error_yields_error_event():
    """网络异常不抛出，包成事件让前端渲染进对话。"""
    respx.post(ANTHROPIC_URL).mock(side_effect=httpx.ConnectError("拨号失败"))
    provider = make(AnthropicProvider, "https://api.anthropic.com")

    (event,) = await collect(provider)
    assert isinstance(event, ErrorEvent)
    assert "连接上游失败" in event.message


@respx.mock
async def test_invalid_base_url_never_sends_request():
    """Base URL 非法时应就地报错——路由表为空，一旦发出请求 respx 就会报错。"""
    provider = make(AnthropicProvider, "")

    (event,) = await collect(provider)
    assert isinstance(event, ErrorEvent)
    assert event.status is None


@respx.mock
async def test_missing_terminal_event_still_gets_done():
    """上游没发终止事件（中转站常见）时补一个 done，前端才不会一直转圈。"""
    body = sse(
        'event: content_block_delta\ndata: {"type": "content_block_delta",'
        ' "delta": {"type": "text_delta", "text": "嗨"}}'
    )
    respx.post(ANTHROPIC_URL).mock(return_value=sse_response(body))
    provider = make(AnthropicProvider, "https://api.anthropic.com")

    assert await collect(provider) == [
        TextDelta(text="嗨"),
        DoneEvent(finish_reason=None),
    ]


@respx.mock
async def test_version_mismatch_is_warning_not_error():
    """用户写的版本段与协议默认不符只是提醒，不该当成错误吓人。"""
    respx.post("https://api.anthropic.com/v1beta/messages").mock(
        return_value=sse_response(ANTHROPIC_SSE)
    )
    provider = make(AnthropicProvider, "https://api.anthropic.com/v1beta")

    events = await collect(provider)

    assert isinstance(events[0], WarningEvent)
    assert not any(isinstance(event, ErrorEvent) for event in events)
    assert events[-1] == DoneEvent(finish_reason="end_turn")


# --- probe（连通性测试）------------------------------------------------------


async def probe(provider: Provider, model: str = "test-model"):
    async with httpx.AsyncClient() as client:
        return await provider.probe(model, client=client)


@respx.mock
async def test_probe_ok():
    respx.post(ANTHROPIC_URL).mock(return_value=sse_response(ANTHROPIC_SSE))
    provider = make(AnthropicProvider, "https://api.anthropic.com")

    result = await probe(provider)

    assert result.ok is True
    assert result.endpoint == ANTHROPIC_URL
    assert result.error is None
    assert result.warnings == ()


@respx.mock
async def test_probe_reports_upstream_error():
    respx.post(ANTHROPIC_URL).mock(
        return_value=httpx.Response(401, json={"error": {"message": "密钥无效"}})
    )
    provider = make(AnthropicProvider, "https://api.anthropic.com")

    result = await probe(provider)

    assert result.ok is False
    assert result.error == "密钥无效"
    assert result.status == 401


@respx.mock
async def test_probe_keeps_warnings_separate_from_errors():
    respx.post("https://api.anthropic.com/v1beta/messages").mock(
        return_value=sse_response(ANTHROPIC_SSE)
    )
    provider = make(AnthropicProvider, "https://api.anthropic.com/v1beta")

    result = await probe(provider)

    # 有提醒但链路是通的
    assert result.ok is True
    assert len(result.warnings) == 1


@respx.mock
async def test_probe_invalid_base_url():
    """Base URL 非法时连端点都算不出来，直接返回失败。"""
    provider = make(AnthropicProvider, "not a url")

    result = await probe(provider)

    assert result.ok is False
    assert result.endpoint == ""
    assert result.error


@respx.mock
async def test_probe_uses_probed_model_in_gemini_path():
    """Gemini 的模型名在路径里，探测用的模型必须体现在端点上。"""
    url = f"{GEMINI_BASE}/v1beta/models/probe-model:streamGenerateContent?alt=sse"
    respx.post(url).mock(return_value=sse_response(GEMINI_SSE))
    provider = make(GeminiProvider, GEMINI_BASE)

    result = await probe(provider, "probe-model")

    assert result.ok is True
    assert result.endpoint == url


# --- 注册表 -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "cls"),
    [
        ("anthropic", AnthropicProvider),
        ("openai_chat", OpenAIChatProvider),
        ("openai_responses", OpenAIResponsesProvider),
        ("gemini", GeminiProvider),
    ],
)
def test_get_provider(name: str, cls: type[Provider]):
    assert get_provider(name) is cls


def test_get_provider_rejects_unknown_name():
    with pytest.raises(UnknownProviderError) as excinfo:
        get_provider("claude")
    # 报错里要列出可选项，方便前端/用户排查
    assert "anthropic" in str(excinfo.value)


def test_list_providers_shape_and_order():
    listed = list_providers()
    assert [item["name"] for item in listed] == [
        "anthropic",
        "openai_chat",
        "openai_responses",
        "gemini",
    ]
    assert all(item["label"] for item in listed)
