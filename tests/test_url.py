"""Base URL 归一化的全场景覆盖。"""

from __future__ import annotations

import pytest

from app.providers.url import (
    InvalidBaseURLError,
    mask_secret,
    resolve_endpoint,
)

# 各协议的调用参数，与适配器保持一致
ANTHROPIC = dict(
    api_path="messages",
    version_segment="v1",
    endpoint_markers=("/messages",),
)
OPENAI_CHAT = dict(
    api_path="chat/completions",
    version_segment="v1",
    endpoint_markers=("/chat/completions",),
)
OPENAI_RESPONSES = dict(
    api_path="responses",
    version_segment="v1",
    endpoint_markers=("/responses",),
)
GEMINI = dict(
    api_path="models/gemini-2.5-pro:streamGenerateContent",
    version_segment="v1beta",
    endpoint_markers=(":generateContent", ":streamGenerateContent"),
)


@pytest.mark.parametrize(
    ("base_url", "kwargs", "expected"),
    [
        # 裸域名：补版本段 + 协议路径
        (
            "https://api.anthropic.com",
            ANTHROPIC,
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "https://api.openai.com",
            OPENAI_CHAT,
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "https://api.openai.com",
            OPENAI_RESPONSES,
            "https://api.openai.com/v1/responses",
        ),
        (
            "https://generativelanguage.googleapis.com",
            GEMINI,
            "https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-2.5-pro:streamGenerateContent",
        ),
        # 用户自带版本段：只补协议路径，不重复补 v1
        (
            "https://api.anthropic.com/v1",
            ANTHROPIC,
            "https://api.anthropic.com/v1/messages",
        ),
        (
            "https://api.openai.com/v1",
            OPENAI_CHAT,
            "https://api.openai.com/v1/chat/completions",
        ),
        # 中转站挂在子路径上
        (
            "https://openrouter.ai/api/v1",
            OPENAI_CHAT,
            "https://openrouter.ai/api/v1/chat/completions",
        ),
        (
            "https://openrouter.ai/api",
            OPENAI_CHAT,
            "https://openrouter.ai/api/v1/chat/completions",
        ),
        # 自建网关前缀，没有版本段 -> 版本段照补
        (
            "https://gateway.corp.internal/llm",
            OPENAI_CHAT,
            "https://gateway.corp.internal/llm/v1/chat/completions",
        ),
        # 尾随斜杠 / 多余斜杠
        (
            "https://api.openai.com/v1/",
            OPENAI_CHAT,
            "https://api.openai.com/v1/chat/completions",
        ),
        (
            "https://api.openai.com///",
            OPENAI_CHAT,
            "https://api.openai.com/v1/chat/completions",
        ),
        # 省略协议 -> 默认 https
        (
            "api.openai.com/v1",
            OPENAI_CHAT,
            "https://api.openai.com/v1/chat/completions",
        ),
        # 本地反代允许 http
        (
            "http://127.0.0.1:11434/v1",
            OPENAI_CHAT,
            "http://127.0.0.1:11434/v1/chat/completions",
        ),
        # 前后空白
        (
            "  https://api.openai.com/v1  ",
            OPENAI_CHAT,
            "https://api.openai.com/v1/chat/completions",
        ),
    ],
)
def test_resolve(base_url: str, kwargs: dict, expected: str) -> None:
    assert resolve_endpoint(base_url, **kwargs).url == expected


@pytest.mark.parametrize(
    ("base_url", "kwargs"),
    [
        ("https://api.anthropic.com/v1/messages", ANTHROPIC),
        ("https://api.openai.com/v1/chat/completions", OPENAI_CHAT),
        ("https://api.openai.com/v1/responses", OPENAI_RESPONSES),
        (
            "https://generativelanguage.googleapis.com"
            "/v1beta/models/gemini-2.5-pro:streamGenerateContent",
            GEMINI,
        ),
    ],
)
def test_full_endpoint_is_left_alone(base_url: str, kwargs: dict) -> None:
    """粘贴完整端点时不应再叠加一层路径。"""
    resolved = resolve_endpoint(base_url, **kwargs)
    assert resolved.url == base_url
    assert resolved.warnings == ()


def test_lock_suffix_disables_all_completion() -> None:
    """末尾 `#` 表示端点已完整，路径再古怪也原样发出。"""
    resolved = resolve_endpoint(
        "https://relay.example.com/weird/path#", **OPENAI_CHAT
    )
    assert resolved.url == "https://relay.example.com/weird/path"


def test_lock_suffix_keeps_bare_host() -> None:
    resolved = resolve_endpoint("https://relay.example.com#", **OPENAI_CHAT)
    assert resolved.url == "https://relay.example.com"


def test_query_string_is_preserved() -> None:
    """Azure OpenAI 依赖 ?api-version=，不能在重组时丢掉。"""
    resolved = resolve_endpoint(
        "https://x.openai.azure.com/openai/v1?api-version=2024-10-21",
        **OPENAI_CHAT,
    )
    assert resolved.url == (
        "https://x.openai.azure.com/openai/v1/chat/completions"
        "?api-version=2024-10-21"
    )


def test_user_version_segment_wins_with_warning() -> None:
    """中转站常把 Gemini 挂在 /v1：按用户填的版本走，但要提示。"""
    resolved = resolve_endpoint("https://relay.example.com/v1", **GEMINI)
    assert resolved.url == (
        "https://relay.example.com/v1/models/gemini-2.5-pro:streamGenerateContent"
    )
    assert len(resolved.warnings) == 1
    assert "v1beta" in resolved.warnings[0]


def test_matching_version_segment_has_no_warning() -> None:
    resolved = resolve_endpoint(
        "https://generativelanguage.googleapis.com/v1beta", **GEMINI
    )
    assert resolved.warnings == ()


def test_version_segment_is_case_insensitive() -> None:
    resolved = resolve_endpoint("https://api.openai.com/V1", **OPENAI_CHAT)
    assert resolved.url == "https://api.openai.com/V1/chat/completions"
    assert resolved.warnings == ()


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "   ",
        "#",
        "  #  ",
        "ftp://api.openai.com",
        "https://",
        "https:///v1",
        # 主机名内部带空格：不能百分号转义成假地址后真发出去
        "not a url",
        "https://api example.com",
    ],
)
def test_invalid_input_raises(base_url: str) -> None:
    with pytest.raises(InvalidBaseURLError):
        resolve_endpoint(base_url, **OPENAI_CHAT)


@pytest.mark.parametrize(
    ("secret", "expected"),
    [
        ("", ""),
        ("short", "………"),
        ("sk-ant-api03-abcdefgh7f2a", "sk-a…7f2a"),
    ],
)
def test_mask_secret(secret: str, expected: str) -> None:
    assert mask_secret(secret) == expected


def test_mask_secret_never_leaks_middle() -> None:
    key = "sk-proj-SUPERSECRETMIDDLE-1234"
    assert "SUPERSECRET" not in mask_secret(key)
