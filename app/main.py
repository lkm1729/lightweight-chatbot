"""FastAPI 应用入口。

本地开发服务器绑定 127.0.0.1（切勿改成 0.0.0.0），因为 API key 存明文、
且没有鉴权，一旦暴露到局域网就等于把密钥共享给同网段的所有人。

打包后的桌面入口在 app.desktop，它复用这里的 ``app``。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import attachments as attachment_store
from app.paths import web_dir
from app.routes import attachments, chat, conversations, providers
from app.store import all_attachment_ids, init_db


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """建好会话库，并清掉上传后从未被引用的孤儿附件。"""
    init_db()
    attachment_store.purge_orphans(all_attachment_ids())
    yield


app = FastAPI(
    title="Easy Chatbox",
    version="0.1.0",
    description="轻量级多协议聊天机器人",
    lifespan=lifespan,
)

# 注册路由
app.include_router(providers.router)
app.include_router(conversations.router)
app.include_router(chat.router)
app.include_router(attachments.router)

# 挂载前端静态文件。打包后 web/ 在 PyInstaller 的临时解压目录里，由 app.paths 定位
WEB_DIR = web_dir()
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="static")


def main() -> None:
    """开发服务器入口，仅绑定本地回环地址。"""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
