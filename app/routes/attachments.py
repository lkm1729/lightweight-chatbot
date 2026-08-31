"""附件上传与回读端点。

上传走 base64 JSON 而不是 multipart：multipart 需要额外装 ``python-multipart``，
而本应用刻意只依赖 fastapi / uvicorn / httpx / pydantic 四个包。请求体因此胖三
分之一，但全程走本地回环，10 MB 的附件也无所谓。
"""

from __future__ import annotations

import base64
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from app import attachments

router = APIRouter(prefix="/api/attachments", tags=["attachments"])


class UploadRequest(BaseModel):
    """上传请求体。``data`` 是 base64 编码的文件字节（不含 ``data:`` 前缀）。"""

    name: str
    mime: str = ""
    data: str


@router.post("")
async def upload_attachment(request: UploadRequest) -> dict[str, Any]:
    """存下一个附件，返回它的元数据。"""
    try:
        raw = base64.b64decode(request.data, validate=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="附件内容不是合法的 base64") from exc

    if not raw:
        raise HTTPException(status_code=400, detail="附件是空文件")
    if len(raw) > attachments.MAX_FILE_BYTES:
        limit = attachments.MAX_FILE_BYTES // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"单个附件不能超过 {limit} MB")

    return attachments.save(request.name, request.mime, raw)


@router.get("/{attachment_id}")
async def get_attachment(attachment_id: str) -> Response:
    """回读附件原始字节，供前端渲染历史消息里的缩略图或下载。"""
    meta = attachments.load_meta(attachment_id)
    raw = attachments.load_bytes(attachment_id)
    if meta is None or raw is None:
        raise HTTPException(status_code=404, detail="附件不存在")

    name = str(meta.get("name") or attachment_id)
    return Response(
        content=raw,
        media_type=str(meta.get("mime") or "application/octet-stream"),
        headers={
            # inline 让图片能直接在页面里显示；文件名用 RFC 5987 写法带上，
            # 中文名才不会在下载时变成乱码
            "Content-Disposition": f"inline; filename*=UTF-8''{_quote(name)}",
            "Cache-Control": "private, max-age=86400",
        },
    )


def _quote(name: str) -> str:
    from urllib.parse import quote

    return quote(name, safe="")
