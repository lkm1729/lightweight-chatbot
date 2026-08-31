"""会话持久化与端点测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import store
from app.main import app


@pytest.fixture(autouse=True)
def temp_db(tmp_path: Path, monkeypatch):
    """每个测试一个独立的临时库，不碰真实 data/chats.db。"""
    monkeypatch.setattr("app.store.DB_FILE", tmp_path / "chats.db")
    store.init_db()


@pytest.fixture
def client():
    return TestClient(app)


def create(client: TestClient, title: str | None = None) -> dict:
    payload = {} if title is None else {"title": title}
    response = client.post("/api/conversations", json=payload)
    assert response.status_code == 200
    return response.json()


# --- 默认标题编号 ---

def test_default_titles_increment(client: TestClient):
    """未命名的会话应依次叫「对话1」「对话2」。"""
    assert create(client)["title"] == "对话1"
    assert create(client)["title"] == "对话2"
    assert create(client)["title"] == "对话3"


def test_default_title_skips_used_numbers(client: TestClient):
    """删掉中间那条后不该撞名——编号取最大值 +1，而非总数 +1。"""
    first = create(client)      # 对话1
    create(client)              # 对话2
    client.delete(f"/api/conversations/{first['id']}")
    assert create(client)["title"] == "对话3"


def test_custom_title_is_kept(client: TestClient):
    assert create(client, "选题调研")["title"] == "选题调研"


def test_blank_title_falls_back_to_default(client: TestClient):
    """只有空白的标题当没填处理。"""
    assert create(client, "   ")["title"] == "对话1"


# --- 列表与排序 ---

def test_list_is_empty_initially(client: TestClient):
    assert client.get("/api/conversations").json() == []


def test_list_orders_by_recent_activity(client: TestClient):
    """最近有新消息的会话排在最前。"""
    first = create(client)
    second = create(client)

    client.post(f"/api/conversations/{first['id']}/messages",
                json={"role": "user", "content": "把我顶上去"})

    titles = [c["title"] for c in client.get("/api/conversations").json()]
    assert titles[0] == first["title"]
    assert second["title"] in titles


def test_list_reports_message_count(client: TestClient):
    conv = create(client)
    for i in range(3):
        client.post(f"/api/conversations/{conv['id']}/messages",
                    json={"role": "user", "content": f"第 {i} 条"})
    listed = client.get("/api/conversations").json()[0]
    assert listed["message_count"] == 3


# --- 消息 ---

def test_messages_round_trip_in_order(client: TestClient):
    """消息按写入顺序取回，thinking 一并保存。"""
    conv = create(client)
    client.post(f"/api/conversations/{conv['id']}/messages",
                json={"role": "user", "content": "你好"})
    client.post(f"/api/conversations/{conv['id']}/messages",
                json={"role": "assistant", "content": "你好！", "thinking": "先问候"})

    detail = client.get(f"/api/conversations/{conv['id']}").json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "你好"),
        ("assistant", "你好！"),
    ]
    assert detail["messages"][1]["thinking"] == "先问候"


def test_add_message_rejects_unknown_conversation(client: TestClient):
    """会话不存在时不该凭空造一个。"""
    response = client.post("/api/conversations/c_nope/messages",
                           json={"role": "user", "content": "喂"})
    assert response.status_code == 404


def test_add_message_rejects_bad_role(client: TestClient):
    conv = create(client)
    response = client.post(f"/api/conversations/{conv['id']}/messages",
                           json={"role": "system", "content": "越权"})
    assert response.status_code == 400


# --- 重命名 ---

def test_rename_conversation(client: TestClient):
    conv = create(client)
    response = client.patch(f"/api/conversations/{conv['id']}",
                            json={"title": "改个名字"})
    assert response.status_code == 200
    assert response.json()["title"] == "改个名字"
    assert client.get(f"/api/conversations/{conv['id']}").json()["title"] == "改个名字"


def test_rename_ignores_blank_title(client: TestClient):
    """空标题不该把会话名清掉。"""
    conv = create(client)
    client.patch(f"/api/conversations/{conv['id']}", json={"title": "  "})
    assert client.get(f"/api/conversations/{conv['id']}").json()["title"] == conv["title"]


def test_rename_unknown_conversation_404(client: TestClient):
    response = client.patch("/api/conversations/c_nope", json={"title": "x"})
    assert response.status_code == 404


# --- 删除 ---

def test_delete_removes_conversation_and_messages(client: TestClient):
    """删会话应级联删掉它的消息，不留孤儿。"""
    conv = create(client)
    client.post(f"/api/conversations/{conv['id']}/messages",
                json={"role": "user", "content": "待删"})

    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 200
    assert client.get(f"/api/conversations/{conv['id']}").status_code == 404
    assert store.list_messages(conv["id"]) == []


def test_delete_only_touches_target(client: TestClient):
    keep = create(client, "留着")
    drop = create(client, "删掉")
    client.delete(f"/api/conversations/{drop['id']}")
    titles = [c["title"] for c in client.get("/api/conversations").json()]
    assert titles == ["留着"]
    assert keep["id"]


def test_delete_unknown_conversation_404(client: TestClient):
    assert client.delete("/api/conversations/c_nope").status_code == 404


def test_get_unknown_conversation_404(client: TestClient):
    assert client.get("/api/conversations/c_nope").status_code == 404
