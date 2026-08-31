"""附件上传、回读、落库与清理的测试。"""

from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import attachments, store
from app.main import app
from app.providers import (
    AnthropicProvider,
    Attachment,
    ChatMessage,
    ChatRequest,
    GeminiProvider,
    OpenAIChatProvider,
    OpenAIResponsesProvider,
    ProviderConfig,
)
from app.providers import content

CONFIG = ProviderConfig(base_url="https://api.example.com", api_key="sk-test")

PNG_BYTES = base64.b64decode(
    # 1×1 透明 PNG
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """把数据库与附件目录都指到临时路径，别碰真实的 data/。"""
    monkeypatch.setattr(store, "DB_FILE", tmp_path / "chats.db")
    monkeypatch.setattr(attachments, "ATTACH_DIR", tmp_path / "attachments")
    return tmp_path


@pytest.fixture
def client(isolated: Path):
    with TestClient(app) as test_client:
        yield test_client


def upload(client: TestClient, name: str, mime: str, raw: bytes) -> dict:
    response = client.post(
        "/api/attachments",
        json={
            "name": name,
            "mime": mime,
            "data": base64.b64encode(raw).decode("ascii"),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- 上传与回读 ---

def test_upload_returns_metadata_without_bytes(client: TestClient):
    meta = upload(client, "note.txt", "text/plain", b"hello")
    assert meta["name"] == "note.txt"
    assert meta["mime"] == "text/plain"
    assert meta["size"] == 5
    assert attachments.is_valid_id(meta["id"])
    # 元数据不该把字节原样带回前端
    assert "data" not in meta


def test_round_trip_preserves_bytes_and_mime(client: TestClient):
    meta = upload(client, "dot.png", "image/png", PNG_BYTES)
    response = client.get(f"/api/attachments/{meta['id']}")
    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"


def test_chinese_filename_survives_round_trip(client: TestClient):
    meta = upload(client, "报告 2026.txt", "text/plain", b"x")
    response = client.get(f"/api/attachments/{meta['id']}")
    # RFC 5987 写法，中文名不会在下载时变成乱码
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    assert response.status_code == 200


def test_rejects_empty_and_oversize(client: TestClient, monkeypatch):
    assert client.post(
        "/api/attachments", json={"name": "e.txt", "mime": "text/plain", "data": ""}
    ).status_code == 400

    monkeypatch.setattr(attachments, "MAX_FILE_BYTES", 4)
    response = client.post(
        "/api/attachments",
        json={
            "name": "big.txt",
            "mime": "text/plain",
            "data": base64.b64encode(b"12345").decode(),
        },
    )
    assert response.status_code == 413


def test_rejects_bad_base64(client: TestClient):
    response = client.post(
        "/api/attachments",
        json={"name": "x.txt", "mime": "text/plain", "data": "!!!not base64!!!"},
    )
    assert response.status_code == 400


def test_missing_attachment_is_404(client: TestClient):
    assert client.get("/api/attachments/att_000000000000").status_code == 404


@pytest.mark.parametrize(
    "bad_id",
    [
        "../config.json",
        "..%2F..%2Fconfig.json",
        "att_ZZZZZZZZZZZZ",
        "att_short",
        "chats.db",
    ],
)
def test_id_is_validated_against_traversal(client: TestClient, bad_id: str):
    """id 来自 URL，不校验就能拼出 ../ 读到任意文件。"""
    assert client.get(f"/api/attachments/{bad_id}").status_code == 404


# --- 落库与历史 ---

def test_attachments_survive_reload(client: TestClient):
    meta = upload(client, "a.png", "image/png", PNG_BYTES)
    conv = client.post("/api/conversations", json={}).json()
    client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"role": "user", "content": "看这张", "attachments": [meta]},
    )

    detail = client.get(f"/api/conversations/{conv['id']}").json()
    assert detail["messages"][0]["attachments"] == [meta]


def test_messages_without_attachments_read_back_empty(client: TestClient):
    conv = client.post("/api/conversations", json={}).json()
    client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"role": "user", "content": "纯文字"},
    )
    detail = client.get(f"/api/conversations/{conv['id']}").json()
    assert detail["messages"][0]["attachments"] == []


def test_unknown_attachment_ids_are_dropped(client: TestClient):
    """前端回传了已被清理的 id 时，不该在历史里留下加载不出来的空附件。"""
    conv = client.post("/api/conversations", json={}).json()
    client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={
            "role": "user",
            "content": "x",
            "attachments": [{"id": "att_000000000000", "name": "gone.png"}],
        },
    )
    detail = client.get(f"/api/conversations/{conv['id']}").json()
    assert detail["messages"][0]["attachments"] == []


def test_deleting_conversation_removes_files(client: TestClient):
    meta = upload(client, "a.png", "image/png", PNG_BYTES)
    conv = client.post("/api/conversations", json={}).json()
    client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"role": "user", "content": "看这张", "attachments": [meta]},
    )

    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 200
    # 数据库行随外键消失，磁盘上的字节也得跟着走
    assert client.get(f"/api/attachments/{meta['id']}").status_code == 404
    assert attachments.load_bytes(meta["id"]) is None


def test_deleting_conversation_keeps_other_attachments(client: TestClient):
    keep = upload(client, "keep.png", "image/png", PNG_BYTES)
    drop = upload(client, "drop.png", "image/png", PNG_BYTES)
    kept_conv = client.post("/api/conversations", json={}).json()
    doomed_conv = client.post("/api/conversations", json={}).json()
    client.post(
        f"/api/conversations/{kept_conv['id']}/messages",
        json={"role": "user", "content": "a", "attachments": [keep]},
    )
    client.post(
        f"/api/conversations/{doomed_conv['id']}/messages",
        json={"role": "user", "content": "b", "attachments": [drop]},
    )

    client.delete(f"/api/conversations/{doomed_conv['id']}")
    assert client.get(f"/api/attachments/{keep['id']}").status_code == 200
    assert client.get(f"/api/attachments/{drop['id']}").status_code == 404


def test_truncate_removes_message_and_everything_after(client: TestClient):
    """编辑提问 / 重新生成回答的底层原语：从这条起把历史砍掉。"""
    conv = client.post("/api/conversations", json={}).json()
    ids = []
    for role, text in [
        ("user", "一"),
        ("assistant", "二"),
        ("user", "三"),
        ("assistant", "四"),
    ]:
        ids.append(
            client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={"role": role, "content": text},
            ).json()["id"]
        )

    response = client.delete(f"/api/conversations/{conv['id']}/messages/{ids[2]}")
    assert response.status_code == 200
    assert response.json()["deleted"] == 2

    remaining = client.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert [m["content"] for m in remaining] == ["一", "二"]


def test_truncate_cleans_up_attachment_files(client: TestClient):
    meta = upload(client, "a.png", "image/png", PNG_BYTES)
    conv = client.post("/api/conversations", json={}).json()
    kept = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"role": "user", "content": "留着"},
    ).json()
    doomed = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"role": "user", "content": "砍掉", "attachments": [meta]},
    ).json()

    client.delete(f"/api/conversations/{conv['id']}/messages/{doomed['id']}")
    assert client.get(f"/api/attachments/{meta['id']}").status_code == 404
    remaining = client.get(f"/api/conversations/{conv['id']}").json()["messages"]
    assert [m["id"] for m in remaining] == [kept["id"]]


def test_truncate_rejects_message_from_another_conversation(client: TestClient):
    """前端状态错乱时不该把别的会话的历史削掉。"""
    first = client.post("/api/conversations", json={}).json()
    second = client.post("/api/conversations", json={}).json()
    message = client.post(
        f"/api/conversations/{first['id']}/messages",
        json={"role": "user", "content": "属于第一个会话"},
    ).json()

    response = client.delete(
        f"/api/conversations/{second['id']}/messages/{message['id']}"
    )
    assert response.status_code == 404
    # 原会话的消息一条都没少
    assert len(client.get(f"/api/conversations/{first['id']}").json()["messages"]) == 1


def test_truncate_unknown_message_is_404(client: TestClient):
    conv = client.post("/api/conversations", json={}).json()
    assert (
        client.delete(f"/api/conversations/{conv['id']}/messages/999999").status_code
        == 404
    )


def test_truncate_bumps_conversation_activity(client: TestClient):
    """砍历史也算一次活动，会话该留在列表前面。"""
    older = client.post("/api/conversations", json={}).json()
    message = client.post(
        f"/api/conversations/{older['id']}/messages",
        json={"role": "user", "content": "x"},
    ).json()
    client.post("/api/conversations", json={})  # 更新的会话，此时排在前面

    client.delete(f"/api/conversations/{older['id']}/messages/{message['id']}")
    titles = [c["id"] for c in client.get("/api/conversations").json()]
    assert titles[0] == older["id"]


# --- 老库迁移 ---

def test_migration_adds_column_to_existing_db(isolated: Path):
    """老库没有 attachments 列，connect() 应就地补上而不丢数据。"""
    old_schema = """
    CREATE TABLE conversations (
        id TEXT PRIMARY KEY, title TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role TEXT NOT NULL, content TEXT NOT NULL,
        thinking TEXT, created_at TEXT NOT NULL
    );
    """
    conn = sqlite3.connect(store.DB_FILE)
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO conversations VALUES ('c_old', '旧对话', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO messages (conversation_id, role, content, created_at)"
        " VALUES ('c_old', 'user', '老消息', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    messages = store.list_messages("c_old")
    assert [m["content"] for m in messages] == ["老消息"]
    assert messages[0]["attachments"] == []

    # 幂等：再连一次不该报「列已存在」
    store.connect().close()
    assert store.list_messages("c_old")[0]["content"] == "老消息"


def test_corrupt_attachments_column_is_ignored(isolated: Path):
    """手工改坏 / 半截写入的 JSON 不该让打开会话直接崩。"""
    store.create_conversation("坏数据")
    conv_id = store.list_conversations()[0]["id"]
    store.add_message(conv_id, "user", "x")
    with store.connect() as conn:
        conn.execute("UPDATE messages SET attachments = '{not json'")
    assert store.list_messages(conv_id)[0]["attachments"] == []


# --- 孤儿清理 ---

def test_purge_removes_unreferenced_old_files(client: TestClient, monkeypatch):
    orphan = upload(client, "orphan.png", "image/png", PNG_BYTES)
    used = upload(client, "used.png", "image/png", PNG_BYTES)
    conv = client.post("/api/conversations", json={}).json()
    client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"role": "user", "content": "x", "attachments": [used]},
    )

    # 刚上传的还在宽限期内，不该被扫掉
    attachments.purge_orphans(store.all_attachment_ids())
    assert attachments.load_bytes(orphan["id"]) is not None

    monkeypatch.setattr(attachments, "ORPHAN_GRACE_SECONDS", -1)
    attachments.purge_orphans(store.all_attachment_ids())
    assert attachments.load_bytes(orphan["id"]) is None
    assert attachments.load_bytes(used["id"]) is not None


def test_purge_on_missing_dir_is_noop(isolated: Path):
    assert attachments.purge_orphans(set()) == 0


# --- 分类与文本内联 ---

def test_classification():
    def att(name: str, mime: str = "") -> Attachment:
        return Attachment(id="att_1", name=name, mime=mime, data="")

    assert content.is_image(att("a.png", "image/png"))
    # SVG 是 image/*，但视觉模型基本不认，给源码更有用
    assert not content.is_image(att("a.svg", "image/svg+xml"))
    assert content.is_text(att("a.svg", "image/svg+xml"))
    assert content.is_pdf(att("a.pdf", "application/pdf"))
    # 浏览器常给源码报空 MIME，得靠扩展名兜底
    assert content.is_text(att("main.py", ""))
    assert content.is_text(att("Dockerfile", "application/octet-stream"))
    assert not content.is_text(att("a.zip", "application/zip"))
    assert not content.is_image(att("a.zip", "application/zip"))


def test_text_block_fence_survives_backticks():
    """内容里本来就有 ``` 时，围栏得更长才不会被提前闭合。"""
    body = "见下：\n```\ncode\n```"
    attachment = Attachment(
        id="att_1",
        name="x.md",
        mime="text/markdown",
        data=base64.b64encode(body.encode()).decode(),
    )
    block = content.as_text_block(attachment)
    assert "````" in block
    assert body in block


def test_long_text_is_truncated(monkeypatch):
    monkeypatch.setattr(content, "MAX_INLINE_CHARS", 10)
    attachment = Attachment(
        id="att_1",
        name="x.txt",
        mime="text/plain",
        data=base64.b64encode(b"0123456789abcdef").decode(),
    )
    text = content.decode_text(attachment)
    assert text.startswith("0123456789")
    assert "已截断" in text


def test_broken_base64_does_not_raise():
    attachment = Attachment(id="att_1", name="x.txt", mime="text/plain", data="%%%")
    assert content.raw_bytes(attachment) == b""


# --- 四套协议的多模态 payload ---

def _request(*attachment_list: Attachment) -> ChatRequest:
    return ChatRequest(
        model="m",
        messages=(
            ChatMessage(role="user", content="这是什么", attachments=attachment_list),
        ),
        reasoning="none",
    )


IMAGE = Attachment(
    id="att_img",
    name="dot.png",
    mime="image/png",
    data=base64.b64encode(PNG_BYTES).decode(),
)
PDF = Attachment(
    id="att_pdf",
    name="doc.pdf",
    mime="application/pdf",
    data=base64.b64encode(b"%PDF-1.4").decode(),
)
CODE = Attachment(
    id="att_py",
    name="main.py",
    mime="",
    data=base64.b64encode(b"print(1)").decode(),
)


def test_plain_message_keeps_string_content():
    """没有附件时 payload 形状必须和改动前一致。"""
    request = ChatRequest(
        model="m", messages=(ChatMessage(role="user", content="嗨"),), reasoning="none"
    )
    assert AnthropicProvider(CONFIG).build_payload(request)["messages"][0]["content"] == "嗨"
    assert OpenAIChatProvider(CONFIG).build_payload(request)["messages"][0]["content"] == "嗨"
    assert OpenAIResponsesProvider(CONFIG).build_payload(request)["input"][0]["content"] == "嗨"
    gemini = GeminiProvider(CONFIG).build_payload(request)
    assert gemini["contents"][0]["parts"] == [{"text": "嗨"}]


def test_anthropic_multimodal_parts():
    payload = AnthropicProvider(CONFIG).build_payload(_request(IMAGE, PDF, CODE))
    parts = payload["messages"][0]["content"]
    assert parts[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": IMAGE.data},
    }
    assert parts[1]["type"] == "document"
    assert parts[1]["source"]["media_type"] == "application/pdf"
    assert parts[2]["type"] == "text" and "main.py" in parts[2]["text"]
    # 提问排在附件之后
    assert parts[-1] == {"type": "text", "text": "这是什么"}


def test_openai_chat_multimodal_parts():
    payload = OpenAIChatProvider(CONFIG).build_payload(_request(IMAGE, PDF, CODE))
    parts = payload["messages"][0]["content"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[1] == {
        "type": "file",
        "file": {"filename": "doc.pdf", "file_data": content.data_url(PDF)},
    }
    assert parts[2]["type"] == "text" and "print(1)" in parts[2]["text"]
    assert parts[-1] == {"type": "text", "text": "这是什么"}


def test_openai_responses_multimodal_parts():
    payload = OpenAIResponsesProvider(CONFIG).build_payload(_request(IMAGE, PDF, CODE))
    parts = payload["input"][0]["content"]
    assert parts[0] == {"type": "input_image", "image_url": content.data_url(IMAGE)}
    assert parts[1] == {
        "type": "input_file",
        "filename": "doc.pdf",
        "file_data": content.data_url(PDF),
    }
    # Responses 的文本块叫 input_text，不是 text
    assert parts[2]["type"] == "input_text"
    assert parts[-1] == {"type": "input_text", "text": "这是什么"}


def test_gemini_multimodal_parts():
    payload = GeminiProvider(CONFIG).build_payload(_request(IMAGE, PDF, CODE))
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"inlineData": {"mimeType": "image/png", "data": IMAGE.data}}
    assert parts[1] == {"inlineData": {"mimeType": "application/pdf", "data": PDF.data}}
    assert "print(1)" in parts[2]["text"]
    assert parts[-1] == {"text": "这是什么"}


def test_binary_attachment_becomes_a_note():
    """既不是图片也不是 PDF、又解不成文本时，至少告诉模型有这么个文件。"""
    blob = Attachment(
        id="att_zip",
        name="a.zip",
        mime="application/zip",
        data=base64.b64encode(b"PK\x03\x04").decode(),
    )
    parts = AnthropicProvider(CONFIG).build_payload(_request(blob))["messages"][0]["content"]
    assert parts[0]["type"] == "text"
    assert "a.zip" in parts[0]["text"]


def test_assistant_attachments_are_stripped_at_the_boundary(client: TestClient):
    """四家都不接受 assistant 消息带附件，路由层就该剔掉。"""
    from app.routes.chat import Message, _to_chat_message

    meta = upload(client, "a.png", "image/png", PNG_BYTES)
    assistant = _to_chat_message(
        Message(role="assistant", content="给你", attachments=[meta])
    )
    assert assistant.attachments == ()
    user = _to_chat_message(Message(role="user", content="看", attachments=[meta]))
    assert len(user.attachments) == 1
    assert user.attachments[0].name == "a.png"


def test_unreadable_attachment_is_skipped_not_fatal(client: TestClient):
    """附件文件没了也要能把这轮对话发出去。"""
    from app.routes.chat import Message, _to_chat_message

    message = _to_chat_message(
        Message(
            role="user",
            content="看",
            attachments=[{"id": "att_000000000000", "name": "gone.png"}],
        )
    )
    assert message.attachments == ()
    assert message.content == "看"
