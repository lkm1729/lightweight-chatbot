"""附件的落盘存储（``data/attachments/``）。

附件字节不进数据库：一张几 MB 的图片按 base64 存进 messages 行，会让每次
``list_messages()``（打开会话就要跑一次）把整个会话的附件全读进内存。所以字节
落盘，数据库里只留元数据。

每个附件对应两个文件，名字都由 id 直接推出，因此按 id 就能唯一定位：

* ``<id>.bin``  —— 原始字节
* ``<id>.json`` —— ``{name, mime, size}``，上传时就写好，供回读时给出正确的
  Content-Type 与下载文件名（此时消息行还不存在，元数据无处可取）

id 形如 ``att_`` + 12 位十六进制，``is_valid_id()`` 严格校验后才拼路径——这个 id
来自 URL，不校验就是目录穿越。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.paths import data_dir

# 绝对路径，理由同 app.config / app.store，见 app.paths
ATTACH_DIR = data_dir() / "attachments"

# 单个附件上限。base64 会再胖三分之一，10 MB 的图片对本地回环足够用了
MAX_FILE_BYTES = 10 * 1024 * 1024

# 单轮对话所有附件合计上限
MAX_TOTAL_BYTES = 25 * 1024 * 1024

_ID_RE = re.compile(r"^att_[0-9a-f]{12}$")

# 上传后一直没被任何消息引用的孤儿文件，超过这个时长才清——正在挑文件、还没点
# 发送的附件不能被扫掉
ORPHAN_GRACE_SECONDS = 24 * 3600


def new_id() -> str:
    return f"att_{uuid.uuid4().hex[:12]}"


def is_valid_id(attachment_id: Any) -> bool:
    """严格校验 id 形状。id 来自 URL，不校验就能拼出 ``../`` 读到任意文件。"""
    return isinstance(attachment_id, str) and bool(_ID_RE.match(attachment_id))


def _blob_path(attachment_id: str) -> Path:
    return ATTACH_DIR / f"{attachment_id}.bin"


def _meta_path(attachment_id: str) -> Path:
    return ATTACH_DIR / f"{attachment_id}.json"


def save(name: str, mime: str, data: bytes) -> dict[str, Any]:
    """写入一个附件，返回可直接交给前端 / 存进消息行的元数据。"""
    ATTACH_DIR.mkdir(parents=True, exist_ok=True)
    attachment_id = new_id()
    meta = {
        "id": attachment_id,
        "name": name or attachment_id,
        "mime": mime or "application/octet-stream",
        "size": len(data),
    }
    _blob_path(attachment_id).write_bytes(data)
    _meta_path(attachment_id).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8"
    )
    return meta


def load_meta(attachment_id: str) -> dict[str, Any] | None:
    """读元数据；id 非法或文件不存在都返回 None。"""
    if not is_valid_id(attachment_id):
        return None
    path = _meta_path(attachment_id)
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def load_bytes(attachment_id: str) -> bytes | None:
    """读原始字节；id 非法或文件不存在都返回 None。"""
    if not is_valid_id(attachment_id):
        return None
    try:
        return _blob_path(attachment_id).read_bytes()
    except OSError:
        return None


def remove(attachment_ids: Iterable[str]) -> int:
    """删掉这些附件的字节与元数据，返回实际删掉的个数。

    删不掉（文件已不在、被占用）不算失败：调用方是在删对话，不该因为一个附件
    没清干净就中断。
    """
    removed = 0
    for attachment_id in attachment_ids:
        if not is_valid_id(attachment_id):
            continue
        gone = False
        for path in (_blob_path(attachment_id), _meta_path(attachment_id)):
            try:
                path.unlink()
                gone = True
            except OSError:
                pass
        removed += 1 if gone else 0
    return removed


def purge_orphans(referenced: set[str]) -> int:
    """清理没有任何消息引用、且已过宽限期的附件。

    附件是先上传、后随消息落库的，中间用户可能把它移出输入框或干脆关掉页面，
    那份字节就再没人引用了。启动时扫一次，避免 ``data/attachments/`` 无限长大。
    """
    if not ATTACH_DIR.exists():
        return 0

    import time

    cutoff = time.time() - ORPHAN_GRACE_SECONDS
    orphans: set[str] = set()
    for path in ATTACH_DIR.glob("*.bin"):
        attachment_id = path.stem
        if attachment_id in referenced or not is_valid_id(attachment_id):
            continue
        try:
            if path.stat().st_mtime < cutoff:
                orphans.add(attachment_id)
        except OSError:
            continue
    return remove(orphans)
