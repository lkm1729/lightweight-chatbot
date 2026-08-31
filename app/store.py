"""对话持久化（data/chats.db，stdlib sqlite3）。

用 SQLite 而不是再开一个 JSON 文件：每轮对话都要往末尾追加消息，JSON 得整篇
重写，条数一多就明显变慢；SQLite 的 INSERT 是增量的，顺带白拿事务与外键级联
删除。整个应用只跑在本地回环地址上，单连接足够，因此不做连接池。

附件只在这里存元数据（``messages.attachments`` 列，JSON 数组），字节在
``app.attachments`` 里落盘。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.paths import data_dir

# 绝对路径。相对的 "data" 会随 cwd 漂移，打包后等于每次可能开到不同的库，见 app.paths
DB_DIR = data_dir()
DB_FILE = DB_DIR / "chats.db"

# 默认标题形如「对话3」，新建时取已有编号的最大值 +1
TITLE_PREFIX = "对话"
_TITLE_RE = re.compile(rf"^{TITLE_PREFIX}(\d+)$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    thinking        TEXT,
    attachments     TEXT,
    origin          TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, id);
"""

# 建表之后补的列。CREATE TABLE IF NOT EXISTS 对已存在的老表不会加列，
# 得显式 ALTER —— 见 _migrate()
ADDED_COLUMNS = (
    ("messages", "attachments", "TEXT"),
    ("messages", "origin", "TEXT"),
)


def _now() -> str:
    """当前时刻的 ISO 字符串，微秒精度。

    刻意不用秒精度：会话列表按 updated_at 排序，同一秒内建两个会话再给早的那个
    发消息，秒精度下两行的 updated_at 完全相同，排序只能退到 created_at，早的那
    个反而排在后面——明明刚说过话却不在列表最前。ISO 字符串按字典序比较，补上
    小数位仍与旧数据（无小数位）正确比较。
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _migrate(conn: sqlite3.Connection) -> None:
    """给老库补上后来新增的列。缺则加，有则跳过，反复跑没有副作用。"""
    for table, column, decl in ADDED_COLUMNS:
        existing = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def connect() -> sqlite3.Connection:
    """打开连接并确保表结构存在。

    ``DB_FILE`` 在调用时才读取，测试才能 monkeypatch 到临时目录。
    """
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    # 级联删除默认是关的，不打开就会留下孤儿消息
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def init_db() -> None:
    """建库建表，应用启动时调一次。"""
    connect().close()


# --------------------------------------------------------------------------
# 会话
# --------------------------------------------------------------------------


def next_default_title(conn: sqlite3.Connection) -> str:
    """下一个「对话N」标题。

    取现有编号的最大值 +1 而非总数 +1：删掉中间某条后，用总数会撞名。
    """
    largest = 0
    for row in conn.execute("SELECT title FROM conversations"):
        match = _TITLE_RE.match(row["title"] or "")
        if match:
            largest = max(largest, int(match.group(1)))
    return f"{TITLE_PREFIX}{largest + 1}"


def list_conversations() -> list[dict[str, Any]]:
    """会话列表，最近活跃的在前，附带消息条数。"""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
              FROM conversations c
              LEFT JOIN messages m ON m.conversation_id = c.id
             GROUP BY c.id
             ORDER BY c.updated_at DESC, c.created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def create_conversation(title: str | None = None) -> dict[str, Any]:
    """新建会话，未给标题时自动编号。"""
    conversation_id = f"c_{uuid.uuid4().hex[:12]}"
    now = _now()
    with connect() as conn:
        final_title = (title or "").strip() or next_default_title(conn)
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (conversation_id, final_title, now, now),
        )
    return {
        "id": conversation_id,
        "title": final_title,
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }


def get_conversation(conversation_id: str) -> dict[str, Any] | None:
    """取单个会话（不含消息）。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def rename_conversation(conversation_id: str, title: str) -> dict[str, Any] | None:
    """改标题。空标题视为无效，直接返回当前状态。"""
    clean = title.strip()
    if not clean:
        return get_conversation(conversation_id)
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE conversations SET title = ? WHERE id = ?",
            (clean, conversation_id),
        )
        if not cursor.rowcount:
            return None
    return get_conversation(conversation_id)


def conversation_attachment_ids(conversation_id: str) -> list[str]:
    """一个会话引用到的所有附件 id。删会话前用来一并清理磁盘上的文件。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT attachments FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
    ids: list[str] = []
    for row in rows:
        for item in _decode_attachments(row["attachments"]):
            attachment_id = item.get("id")
            if isinstance(attachment_id, str):
                ids.append(attachment_id)
    return ids


def all_attachment_ids() -> set[str]:
    """全库被引用的附件 id，供启动时清理孤儿文件。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT attachments FROM messages WHERE attachments IS NOT NULL"
        ).fetchall()
    ids: set[str] = set()
    for row in rows:
        for item in _decode_attachments(row["attachments"]):
            attachment_id = item.get("id")
            if isinstance(attachment_id, str):
                ids.add(attachment_id)
    return ids


def delete_conversation(conversation_id: str) -> bool:
    """删会话，消息随外键级联删除。

    磁盘上的附件文件不受外键管辖，由调用方拿 ``conversation_attachment_ids()``
    先取出 id 再清——放在路由层做，store 只管数据库。
    """
    with connect() as conn:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conversation_id,)
        )
        return bool(cursor.rowcount)


# --------------------------------------------------------------------------
# 消息
# --------------------------------------------------------------------------


def _decode_attachments(raw: Any) -> list[dict[str, Any]]:
    """把 attachments 列还原成元数据数组。坏数据当没有附件处理。"""
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _decode_origin(raw: Any) -> dict[str, Any] | None:
    """还原 origin 列。坏数据当没有来源处理——界面宁可只显示「AI」，
    也不该因为一条脏数据把整个会话打不开。"""
    if not raw:
        return None
    try:
        origin = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return origin if isinstance(origin, dict) else None


def list_messages(conversation_id: str) -> list[dict[str, Any]]:
    """按写入顺序取一个会话的全部消息。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, role, content, thinking, attachments, origin, created_at"
            "  FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()
    return [
        {
            **dict(row),
            "attachments": _decode_attachments(row["attachments"]),
            "origin": _decode_origin(row["origin"]),
        }
        for row in rows
    ]


def delete_messages_from(
    conversation_id: str, message_id: int
) -> tuple[int, list[str]] | None:
    """删掉这条消息**及其之后**的所有消息，返回 (条数, 附件 id 列表)。

    编辑自己的提问与重新生成回答共用这一个原语：两者都是「把这条之后的历史砍掉，
    再重新走一轮」。消息不属于该会话时返回 None，让路由层回 404——否则前端状态
    错乱时可能把别的会话的历史削掉。

    磁盘上的附件文件不受外键管辖，所以把 id 一并返回，由调用方清理。
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
        if row is None:
            return None

        doomed = conn.execute(
            "SELECT attachments FROM messages"
            " WHERE conversation_id = ? AND id >= ?",
            (conversation_id, message_id),
        ).fetchall()
        attachment_ids: list[str] = []
        for item_row in doomed:
            for item in _decode_attachments(item_row["attachments"]):
                attachment_id = item.get("id")
                if isinstance(attachment_id, str):
                    attachment_ids.append(attachment_id)

        cursor = conn.execute(
            "DELETE FROM messages WHERE conversation_id = ? AND id >= ?",
            (conversation_id, message_id),
        )
        # 历史变短了也算一次活动，让会话在列表里保持在前面
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )
        return cursor.rowcount, attachment_ids


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    thinking: str | None = None,
    attachments: list[dict[str, Any]] | None = None,
    origin: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """追加一条消息并把会话的 updated_at 顶到最新。

    ``origin`` 记下这条回答是**哪个模型**生成的（模型名 / 供应商 / 协议）。
    历史消息要显示当时那个模型，而不是当前选中的——否则换了模型再翻历史，
    所有旧回答都会被标上新模型的名字。

    会话不存在时返回 None（而非建一个），避免前端状态错乱时凭空造出会话。
    """
    now = _now()
    encoded = json.dumps(attachments, ensure_ascii=False) if attachments else None
    encoded_origin = json.dumps(origin, ensure_ascii=False) if origin else None
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if not exists:
            return None
        cursor = conn.execute(
            "INSERT INTO messages"
            " (conversation_id, role, content, thinking, attachments, origin, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                role,
                content,
                thinking or None,
                encoded,
                encoded_origin,
                now,
            ),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )
    return {
        "id": cursor.lastrowid,
        "role": role,
        "content": content,
        "thinking": thinking or None,
        "attachments": attachments or [],
        "origin": origin,
        "created_at": now,
    }
