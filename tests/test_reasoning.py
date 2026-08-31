"""推理档位与各协议参数映射的测试。"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import reasoning
from app.main import app
from app.providers import (
    AnthropicProvider,
    ChatMessage,
    ChatRequest,
    GeminiProvider,
    OpenAIChatProvider,
    OpenAIResponsesProvider,
    ProviderConfig,
    WarningEvent,
)
from app.providers.base import LEGACY_FALLBACK_NOTE

pytestmark = pytest.mark.anyio

CONFIG = ProviderConfig(base_url="https://api.example.com", api_key="sk-test")

REQUEST = ChatRequest(
    model="test-model",
    messages=(ChatMessage(role="user", content="你好"),),
    max_tokens=1000,
)

ALL_KEYS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def payload_for(provider_cls, effort: str) -> dict:
    provider = provider_cls(CONFIG)
    return provider.build_payload(replace(REQUEST, reasoning=effort))


def legacy_for(provider_cls, effort: str) -> dict:
    provider = provider_cls(CONFIG)
    return provider.build_legacy_payload(replace(REQUEST, reasoning=effort))


def warnings_for(provider_cls, effort: str) -> tuple[str, ...]:
    provider = provider_cls(CONFIG)
    return provider.payload_warnings(replace(REQUEST, reasoning=effort))


# --- 档位表本身 ---

def test_seven_levels_in_ascending_order():
    """界面依赖这个顺序从弱到强排列；「超级」档已删除。"""
    assert [level.key for level in reasoning.LEVELS] == list(ALL_KEYS)


def test_ultra_is_gone():
    assert "ultra" not in reasoning.LEVELS_BY_KEY


def test_budget_is_monotonic():
    """预算必须单调不减，否则「更高档位」名不副实。"""
    budgets = [level.budget for level in reasoning.LEVELS]
    assert budgets == sorted(budgets)


def test_unknown_key_falls_back_to_default():
    assert reasoning.resolve("wat").key == reasoning.DEFAULT_KEY
    assert reasoning.resolve(None).key == reasoning.DEFAULT_KEY
    # 删掉的档位也应回落，而不是抛异常
    assert reasoning.resolve("ultra").key == reasoning.DEFAULT_KEY


def test_list_levels_exposes_ui_fields():
    """前端抽屉要的字段齐全，且不泄漏内部预算数字。"""
    first = reasoning.list_levels()[0]
    assert set(first) == {"key", "label", "label_en", "note"}


def test_no_boosted_badge_field():
    """↑ 标记已去掉，boosted 字段随之没人使用。"""
    assert not hasattr(reasoning.LEVELS[-1], "boosted")
    assert all("boosted" not in level for level in reasoning.list_levels())


# --- Anthropic：顶层 effort + 自适应思考 ---

def test_anthropic_sends_native_effort():
    """xhigh / max 是 Anthropic 认识的真实值，不该被压成 high。"""
    for key in ("low", "medium", "high", "xhigh", "max"):
        payload = payload_for(AnthropicProvider, key)
        assert payload["effort"] == key
        assert payload["thinking"] == {"type": "adaptive"}


def test_anthropic_none_disables_thinking():
    """新模型默认开着自适应思考，得显式关掉才是「无」。"""
    payload = payload_for(AnthropicProvider, "none")
    assert payload["thinking"] == {"type": "disabled"}
    assert "effort" not in payload
    assert payload["max_tokens"] == 1000


def test_anthropic_minimal_maps_to_low_with_note():
    """Anthropic 没有 minimal 档，取 low 并如实告知。"""
    assert payload_for(AnthropicProvider, "minimal")["effort"] == "low"
    assert warnings_for(AnthropicProvider, "minimal")


def test_anthropic_lifts_max_tokens_for_high_efforts():
    """思考 token 也算在 max_tokens 里，高档位下不顶上去正文会被截断。"""
    payload = payload_for(AnthropicProvider, "max")
    assert payload["max_tokens"] >= reasoning.LEVELS_BY_KEY["max"].budget


def test_anthropic_legacy_uses_budget_tokens():
    """回退路径回到扩展思考的旧写法。"""
    payload = legacy_for(AnthropicProvider, "max")
    assert "effort" not in payload
    assert payload["thinking"] == {
        "type": "enabled",
        "budget_tokens": reasoning.LEVELS_BY_KEY["max"].budget,
    }
    assert payload["max_tokens"] > payload["thinking"]["budget_tokens"]


def test_anthropic_drops_temperature_when_thinking():
    """思考模式只接受 temperature=1，带上原值会被上游拒。"""
    provider = AnthropicProvider(CONFIG)
    request = replace(REQUEST, temperature=0.7, reasoning="high")
    assert "temperature" not in provider.build_payload(request)
    # 关掉思考后 temperature 应照常透传
    request = replace(REQUEST, temperature=0.7, reasoning="none")
    assert provider.build_payload(request)["temperature"] == 0.7


# --- OpenAI 两套：reasoning_effort ---

@pytest.mark.parametrize(
    "provider_cls, read",
    [
        (OpenAIChatProvider, lambda p: p.get("reasoning_effort")),
        (OpenAIResponsesProvider, lambda p: (p.get("reasoning") or {}).get("effort")),
    ],
)
def test_openai_sends_native_efforts(provider_cls, read):
    """none 与 xhigh 都是 OpenAI 认的真实值。"""
    expected = {
        "none": "none",
        "minimal": "minimal",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "xhigh",
        "max": "xhigh",  # OpenAI 最高就是 xhigh
    }
    for key, value in expected.items():
        assert read(payload_for(provider_cls, key)) == value


@pytest.mark.parametrize(
    "provider_cls", [OpenAIChatProvider, OpenAIResponsesProvider]
)
def test_openai_only_warns_for_max(provider_cls):
    """只有「最高」在 OpenAI 下没有对等值，其余档位不该有提示。"""
    assert warnings_for(provider_cls, "max")
    for key in ("none", "minimal", "low", "medium", "high", "xhigh"):
        assert not warnings_for(provider_cls, key)


def test_openai_responses_asks_for_summary():
    """summary=auto 才会有 reasoning_summary_text.delta 事件可渲染。"""
    assert payload_for(OpenAIResponsesProvider, "low")["reasoning"] == {
        "effort": "low",
        "summary": "auto",
    }


def test_openai_legacy_clamps_to_four_levels():
    """老模型只认 minimal/low/medium/high。"""
    assert legacy_for(OpenAIChatProvider, "xhigh")["reasoning_effort"] == "high"
    assert legacy_for(OpenAIChatProvider, "max")["reasoning_effort"] == "high"
    assert legacy_for(OpenAIChatProvider, "medium")["reasoning_effort"] == "medium"


def test_openai_legacy_omits_field_for_none():
    """老模型不接受 ``none``，只能整个字段不发。"""
    assert "reasoning_effort" not in legacy_for(OpenAIChatProvider, "none")
    assert "reasoning" not in legacy_for(OpenAIResponsesProvider, "none")


def test_openai_no_service_tier_anymore():
    """「超级」档删了，service_tier 也不该再出现。"""
    for key in ALL_KEYS:
        assert "service_tier" not in payload_for(OpenAIChatProvider, key)
        assert "service_tier" not in payload_for(OpenAIResponsesProvider, key)


def test_openai_does_not_scale_max_tokens():
    """倍数机制随「超级」档一起删了，上限应原样透传。"""
    assert payload_for(OpenAIChatProvider, "max")["max_tokens"] == 1000
    assert payload_for(OpenAIResponsesProvider, "max")["max_output_tokens"] == 1000


# --- Gemini：thinkingLevel ---

def _thinking(payload: dict) -> dict:
    return payload["generationConfig"]["thinkingConfig"]


def test_gemini_sends_thinking_level():
    for key, level in [("low", "low"), ("medium", "medium"), ("high", "high")]:
        config = _thinking(payload_for(GeminiProvider, key))
        assert config["thinkingLevel"] == level
        assert config["includeThoughts"] is True
        # thinkingLevel 与 thinkingBudget 互斥，同时给会被上游拒
        assert "thinkingBudget" not in config


def test_gemini_clamps_top_tiers_to_high_with_note():
    """thinkingLevel 只有三级，极高 / 最高都落到 high。"""
    for key in ("xhigh", "max"):
        assert _thinking(payload_for(GeminiProvider, key))["thinkingLevel"] == "high"
        assert warnings_for(GeminiProvider, key)


def test_gemini_none_uses_zero_budget():
    """thinkingLevel 没有「关闭」这一档，只能靠预算 0 表达。"""
    config = _thinking(payload_for(GeminiProvider, "none"))
    assert config == {"includeThoughts": False, "thinkingBudget": 0}


def test_gemini_legacy_uses_budget():
    config = _thinking(legacy_for(GeminiProvider, "max"))
    assert config == {
        "thinkingBudget": reasoning.LEVELS_BY_KEY["max"].budget,
        "includeThoughts": True,
    }
    assert "thinkingLevel" not in config


# --- 老模型回退 ---

@respx.mock
async def test_falls_back_to_legacy_payload_on_effort_rejection():
    """400 指向 effort 时改用旧写法重发，并把回退如实告诉用户。"""
    sent: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if "effort" in body:
            return httpx.Response(
                400, json={"error": {"message": "effort: unsupported parameter"}}
            )
        return httpx.Response(
            200,
            text=(
                'data: {"type":"content_block_delta","delta":{"text":"好"}}\n\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    respx.post("https://api.example.com/v1/messages").mock(side_effect=responder)

    provider = AnthropicProvider(CONFIG)
    async with httpx.AsyncClient() as client:
        events = [
            event
            async for event in provider.stream(
                replace(REQUEST, reasoning="max"), client=client
            )
        ]

    assert len(sent) == 2
    assert "effort" in sent[0]
    assert sent[1]["thinking"]["budget_tokens"] == reasoning.LEVELS_BY_KEY["max"].budget
    assert any(
        isinstance(e, WarningEvent) and e.message == LEGACY_FALLBACK_NOTE
        for e in events
    )
    # 回退后的正文照常送达
    assert "".join(e.text for e in events if e.type == "text") == "好"


@respx.mock
async def test_unrelated_error_does_not_retry():
    """鉴权之类的错误与档位无关，重试只是白跑一趟。"""
    route = respx.post("https://api.example.com/v1/messages").mock(
        return_value=httpx.Response(401, json={"error": {"message": "invalid api key"}})
    )

    provider = AnthropicProvider(CONFIG)
    async with httpx.AsyncClient() as client:
        events = [
            event
            async for event in provider.stream(
                replace(REQUEST, reasoning="max"), client=client
            )
        ]

    assert route.call_count == 1
    assert [e.type for e in events] == ["error"]
    assert events[0].status == 401


@respx.mock
async def test_fallback_gives_up_after_one_retry():
    """旧写法也被拒时报出真实错误，不无限重试。"""
    route = respx.post("https://api.example.com/v1/messages").mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "effort: still unsupported"}}
        )
    )

    provider = AnthropicProvider(CONFIG)
    async with httpx.AsyncClient() as client:
        events = [
            event
            async for event in provider.stream(
                replace(REQUEST, reasoning="max"), client=client
            )
        ]

    assert route.call_count == 2
    assert [e for e in events if e.type == "error"]


# --- 端到端：档位经 /api/chat/stream 传到上游 ---

@respx.mock
async def test_chat_stream_forwards_reasoning():
    """前端选的档位应体现在发往上游的 payload 里。"""
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            text='data: {"type": "message_stop"}\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "provider": "anthropic",
                "base_url": "https://api.anthropic.com",
                "api_key": "sk-test",
                "model": "claude-opus-4-5",
                "messages": [{"role": "user", "content": "嗨"}],
                "reasoning": "xhigh",
            },
        )
        assert response.status_code == 200
        response.read()

    sent = json.loads(route.calls.last.request.content)
    assert sent["effort"] == "xhigh"


def test_reasoning_levels_endpoint():
    """前端靠这个端点渲染抽屉。"""
    with TestClient(app) as client:
        levels = client.get("/api/providers/reasoning").json()
    assert len(levels) == 7
    assert levels[0]["label"] == "无"
    assert levels[-1]["key"] == "max"
    assert "boosted" not in levels[-1]
    assert "fast" not in levels[-1]


def test_probe_does_not_send_reasoning_params():
    """连通性测试只验 Key/端点/模型名，不该带推理参数进去。"""
    provider = AnthropicProvider(CONFIG)
    request = ChatRequest(
        model="m", messages=(ChatMessage(role="user", content="ping"),), reasoning="none"
    )
    assert "effort" not in provider.build_payload(request)


def test_warning_event_shape():
    """payload_warnings 的内容最终会包成 WarningEvent 进流。"""
    note = reasoning.LEVELS_BY_KEY["max"].openai.note
    assert note and "xhigh" in note
    assert WarningEvent(message=note).type == "warning"
