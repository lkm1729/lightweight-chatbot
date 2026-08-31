"""路由层测试：端点行为、配置管理、掩码保护。

用 TestClient 或 httpx.ASGITransport 直接调 FastAPI app，不启动真实服务器。
上游 provider 请求用 respx 拦截。
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app

# 测试时用内存配置，不污染真实 data/
@pytest.fixture(autouse=True)
def mock_config_file(tmp_path: Path, monkeypatch):
    """每个测试用独立的临时配置文件。"""
    config_dir = tmp_path / "data"
    config_dir.mkdir()
    config_file = config_dir / "config.json"

    monkeypatch.setattr("app.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("app.config.CONFIG_FILE", config_file)

    return config_file


@pytest.fixture
def client():
    """FastAPI 测试客户端。"""
    return TestClient(app)


def write_vendor_config(
    path: Path,
    provider: str,
    *,
    vendor_id: str = "v_test",
    api_key: str = "",
    base_url: str = "",
    models: list[dict[str, str]] | None = None,
) -> None:
    """写一份 v2 结构的配置，省得每个测试都手拼三层嵌套。"""
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "providers": {
                    provider: {
                        "vendors": [
                            {
                                "id": vendor_id,
                                "name": "测试站",
                                "base_url": base_url,
                                "api_key": api_key,
                                "models": models or [],
                            }
                        ]
                    }
                },
                "selection": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# --- GET /api/providers ---

def test_get_providers_returns_list(client: TestClient):
    """协议清单应返回四个 provider。"""
    response = client.get("/api/providers")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 4
    names = {p["name"] for p in data}
    assert names == {"anthropic", "openai_chat", "openai_responses", "gemini"}


# --- GET /api/providers/config ---

def test_get_config_empty_when_no_file(client: TestClient):
    """配置文件不存在时返回空的 v2 骨架。"""
    response = client.get("/api/providers/config")
    assert response.status_code == 200
    assert response.json() == {
        "version": 2,
        "providers": {},
        "selection": {},
        "search": {
            "type": "",
            "name": "",
            "base_url": "",
            "api_key": "",
            "max_results": 5,
        },
    }


def test_get_config_masks_api_keys(client: TestClient, mock_config_file: Path):
    """每个供应商的 API key 都应被掩码。"""
    write_vendor_config(
        mock_config_file,
        "anthropic",
        base_url="https://api.anthropic.com",
        api_key="sk-ant-api03-1234567890abcdef",
    )

    response = client.get("/api/providers/config")
    assert response.status_code == 200
    data = response.json()

    vendor = data["providers"]["anthropic"]["vendors"][0]
    assert "…" in vendor["api_key"]
    assert "1234567890abcdef" not in vendor["api_key"]
    assert vendor["name"] == "测试站"


def test_get_config_migrates_v1_flat_schema(client: TestClient, mock_config_file: Path):
    """旧的扁平结构应被迁移成单个供应商，默认模型成为它的第一个模型。"""
    mock_config_file.write_text(
        json.dumps(
            {
                "openai_chat": {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-legacy-key",
                    "model": "gpt-4o",
                },
                "gemini": {"base_url": "", "model": ""},
            }
        ),
        encoding="utf-8",
    )

    data = client.get("/api/providers/config").json()

    vendors = data["providers"]["openai_chat"]["vendors"]
    assert len(vendors) == 1
    assert vendors[0]["base_url"] == "https://api.example.com/v1"
    assert vendors[0]["models"] == [{"id": "gpt-4o", "name": "gpt-4o"}]
    assert "…" in vendors[0]["api_key"]
    # 空壳配置不该变出一个空供应商
    assert data["providers"]["gemini"]["vendors"] == []


# --- POST /api/providers/config ---

def test_save_config_writes_file(client: TestClient, mock_config_file: Path):
    """保存配置应写入文件，模型的 ID 与显示名分开存。"""
    payload = {
        "config": {
            "version": 2,
            "providers": {
                "openai_chat": {
                    "vendors": [
                        {
                            "id": "v_1",
                            "name": "OpenRouter",
                            "base_url": "https://api.openai.com",
                            "api_key": "sk-proj-test123",
                            "models": [{"id": "gpt-4o", "name": "GPT-4o"}],
                        }
                    ]
                }
            },
            "selection": {"provider": "openai_chat", "vendor": "v_1", "model": "gpt-4o"},
        }
    }

    response = client.post("/api/providers/config", json=payload)
    assert response.status_code == 200
    assert response.json() == {"message": "配置已保存"}

    saved = json.loads(mock_config_file.read_text(encoding="utf-8"))
    vendor = saved["providers"]["openai_chat"]["vendors"][0]
    assert vendor["api_key"] == "sk-proj-test123"
    assert vendor["name"] == "OpenRouter"
    assert vendor["models"] == [{"id": "gpt-4o", "name": "GPT-4o"}]
    assert saved["selection"]["model"] == "gpt-4o"


def test_save_config_supports_multiple_vendors(client: TestClient, mock_config_file: Path):
    """同一协议下可挂多个供应商，删除即不出现在提交里。"""
    def payload(vendors):
        return {"config": {"version": 2, "providers": {"anthropic": {"vendors": vendors}}}}

    two = [
        {"id": "v_a", "name": "官方", "base_url": "https://api.anthropic.com", "api_key": "k1"},
        {"id": "v_b", "name": "中转", "base_url": "https://relay.example.com", "api_key": "k2"},
    ]
    assert client.post("/api/providers/config", json=payload(two)).status_code == 200
    saved = json.loads(mock_config_file.read_text(encoding="utf-8"))
    assert [v["name"] for v in saved["providers"]["anthropic"]["vendors"]] == ["官方", "中转"]

    # 前端删掉第一个后重新提交
    assert client.post("/api/providers/config", json=payload(two[1:])).status_code == 200
    saved = json.loads(mock_config_file.read_text(encoding="utf-8"))
    vendors = saved["providers"]["anthropic"]["vendors"]
    assert len(vendors) == 1
    assert vendors[0]["id"] == "v_b"


def test_save_config_drops_models_without_id(client: TestClient, mock_config_file: Path):
    """只填了显示名却没底层 ID 的模型行是半成品，应被丢掉。"""
    payload = {
        "config": {
            "version": 2,
            "providers": {
                "gemini": {
                    "vendors": [
                        {
                            "id": "v_1",
                            "name": "官方",
                            "models": [
                                {"id": "", "name": "写了一半"},
                                {"id": "gemini-2.5-pro", "name": ""},
                            ],
                        }
                    ]
                }
            },
        }
    }

    assert client.post("/api/providers/config", json=payload).status_code == 200
    saved = json.loads(mock_config_file.read_text(encoding="utf-8"))
    models = saved["providers"]["gemini"]["vendors"][0]["models"]
    # 显示名留空时回落到 ID，免得下拉框出现空白项
    assert models == [{"id": "gemini-2.5-pro", "name": "gemini-2.5-pro"}]


def test_save_config_preserves_masked_keys(client: TestClient, mock_config_file: Path):
    """掩码值回传时应按供应商 id 保留已存的真密钥。"""
    write_vendor_config(mock_config_file, "anthropic", api_key="sk-ant-real-secret")

    # 前端拿到掩码后原样回传
    payload = {
        "config": {
            "version": 2,
            "providers": {
                "anthropic": {
                    "vendors": [
                        {
                            "id": "v_test",
                            "name": "测试站",
                            "base_url": "https://api.anthropic.com",
                            "api_key": "sk-a…cret",
                        }
                    ]
                }
            },
        }
    }

    response = client.post("/api/providers/config", json=payload)
    assert response.status_code == 200

    saved = json.loads(mock_config_file.read_text(encoding="utf-8"))
    assert saved["providers"]["anthropic"]["vendors"][0]["api_key"] == "sk-ant-real-secret"


# --- POST /api/providers/probe ---

@respx.mock
def test_probe_success(client: TestClient):
    """连通性测试成功。"""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            text="event: message_start\ndata: {}\n\n",
            headers={"content-type": "text/event-stream"},
        )
    )

    payload = {
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-test",
        "model": "claude-3-5-sonnet-20241022",
    }

    response = client.post("/api/providers/probe", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["ok"] is True
    assert "anthropic.com" in data["endpoint"]
    assert data["error"] is None


@respx.mock
def test_probe_upstream_error(client: TestClient):
    """上游返回 401 时 probe 应报告失败。"""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            401,
            json={"error": {"message": "密钥无效"}},
        )
    )

    payload = {
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-bad",
        "model": "claude-3-5-sonnet-20241022",
    }

    response = client.post("/api/providers/probe", json=payload)
    assert response.status_code == 200
    data = response.json()

    assert data["ok"] is False
    assert "密钥无效" in data["error"]
    assert data["status"] == 401


def test_probe_invalid_provider(client: TestClient):
    """未知 provider 应返回 400。"""
    payload = {
        "provider": "claude",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-test",
        "model": "test",
    }

    response = client.post("/api/providers/probe", json=payload)
    assert response.status_code == 400
    assert "未知的协议" in response.json()["detail"]


@respx.mock
def test_probe_uses_stored_key_when_masked(client: TestClient, mock_config_file: Path):
    """前端只持有掩码值时，probe 应回落到对应供应商的本地明文。"""
    # 先存一个真密钥
    write_vendor_config(
        mock_config_file, "anthropic", vendor_id="v_x", api_key="sk-ant-real-key"
    )

    # 设置上游期望：密钥应该是真的，不是掩码
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            text="event: message_start\ndata: {}\n\n",
            headers={"content-type": "text/event-stream"},
        )
    )

    # 前端只有掩码值
    payload = {
        "provider": "anthropic",
        "vendor_id": "v_x",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-a…-key",  # 掩码
        "model": "claude-3-5-sonnet-20241022",
    }

    response = client.post("/api/providers/probe", json=payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True

    # 验证上游收到的是真密钥
    assert route.called
    request = route.calls.last.request
    assert request.headers["x-api-key"] == "sk-ant-real-key"


@respx.mock
def test_probe_picks_key_of_requested_vendor(client: TestClient, mock_config_file: Path):
    """同协议下有多个供应商时，应按 vendor_id 取对应那份密钥。"""
    mock_config_file.write_text(
        json.dumps(
            {
                "version": 2,
                "providers": {
                    "anthropic": {
                        "vendors": [
                            {"id": "v_a", "name": "甲", "api_key": "sk-key-a"},
                            {"id": "v_b", "name": "乙", "api_key": "sk-key-b"},
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            text="event: message_start\ndata: {}\n\n",
            headers={"content-type": "text/event-stream"},
        )
    )

    payload = {
        "provider": "anthropic",
        "vendor_id": "v_b",
        "base_url": "https://api.anthropic.com",
        "api_key": "",
        "model": "claude-3-5-sonnet-20241022",
    }

    assert client.post("/api/providers/probe", json=payload).status_code == 200
    assert route.calls.last.request.headers["x-api-key"] == "sk-key-b"


# --- POST /api/chat/stream ---

@respx.mock
def test_chat_stream_success(client: TestClient):
    """流式对话应返回 SSE。"""
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            text=(
                "event: content_block_delta\n"
                'data: {"delta": {"text": "你好"}}\n\n'
                "event: message_delta\n"
                'data: {"delta": {"stop_reason": "end_turn"}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )
    )

    payload = {
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-test",
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "嗨"}],
    }

    response = client.post("/api/chat/stream", json=payload)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    # 解析 SSE
    events = []
    for line in response.text.strip().split("\n\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    assert len(events) == 2
    assert events[0]["type"] == "text"
    assert events[0]["text"] == "你好"
    assert events[1]["type"] == "done"


@respx.mock
def test_chat_stream_uses_stored_key(client: TestClient, mock_config_file: Path):
    """chat/stream 也应在掩码时回落到本地明文。"""
    write_vendor_config(
        mock_config_file, "openai_chat", vendor_id="v_o", api_key="sk-real-openai-key"
    )

    route = respx.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            text='data: {"choices": [{"delta": {"content": "Hi"}}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
        )
    )

    payload = {
        "provider": "openai_chat",
        "vendor_id": "v_o",
        "base_url": "https://api.openai.com",
        "api_key": "",  # 空值
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    response = client.post("/api/chat/stream", json=payload)
    assert response.status_code == 200

    # 验证上游收到的是真密钥
    assert route.called
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-real-openai-key"


def test_chat_stream_invalid_provider(client: TestClient):
    """未知 provider 应返回 400。"""
    payload = {
        "provider": "unknown",
        "base_url": "https://example.com",
        "api_key": "test",
        "model": "test",
        "messages": [],
    }

    response = client.post("/api/chat/stream", json=payload)
    assert response.status_code == 400


# --- 静态前端 ---

def test_serves_frontend(client: TestClient):
    """根路径应返回 index.html。"""
    response = client.get("/")
    assert response.status_code == 200
    assert "Easy Chatbox" in response.text


def test_serves_static_assets(client: TestClient):
    """样式与脚本应可访问。"""
    for path in ("/style.css", "/app.js"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_api_routes_win_over_static_mount(client: TestClient):
    """静态挂载在 / 上，但 /api/* 必须仍走路由而非静态文件。"""
    response = client.get("/api/providers")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

