"""桌面版入口：本地起 uvicorn，再用 pywebview 开一个原生窗口装前端。

启动顺序上有两个坑，代码里按这个顺序绕开：

1. **先占端口再交给 uvicorn**。常见写法是「先找一个空端口、关掉、再让服务去 bind」，
   两步之间那一瞬别的进程可能把端口抢走，表现为偶发的启动失败。这里自己 bind + listen
   拿到 socket，原样交给 ``Server.run(sockets=...)``，中间没有窗口期。
2. **等服务真正能应答再开窗**。因为监听 socket 是我们自己建的，TCP 连接从一开始就能
   成功，用「端口通不通」判断就绪会过早——窗口会开在一个还没挂上路由的服务上，
   显示 404 或空白。所以必须发真实 HTTP 请求确认。

窗口关掉即退出；uvicorn 跑在守护线程里，进程结束一并收走。

安全上沿用开发服务器的约定：只绑 127.0.0.1。配置里的 API key 是明文且服务无鉴权，
绑到 0.0.0.0 等于把密钥送给同网段所有人。
"""

from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from app.paths import data_dir, is_frozen

WINDOW_TITLE = "Easy Chatbox"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 820
WINDOW_MIN_SIZE = (900, 600)

# 就绪等待上限。冷启动要解压依赖、建库、扫孤儿附件，慢盘上几秒是正常的
READY_TIMEOUT_SECONDS = 60.0
READY_POLL_INTERVAL = 0.15


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------


def _redirect_output_to_log() -> Path | None:
    """打包成无控制台的 exe 后，把 stdout/stderr 接到日志文件。

    ``--noconsole`` 下 ``sys.stdout`` / ``sys.stderr`` 是 None，而 uvicorn 的日志
    handler 会往 stderr 写——不接管的话，第一条日志就能把整个进程带崩，而且用户
    什么也看不到。顺带留下一份可发来排查的日志。
    """
    if not is_frozen():
        return None
    if sys.stdout is not None and sys.stderr is not None:
        return None

    log_path = data_dir() / "startup.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        # 日志开不出来（目录只读等）也不能拦住启动，退回丢弃输出
        devnull = open("nul", "w", encoding="utf-8")
        sys.stdout = sys.stderr = devnull
        return None

    stream.write(f"\n===== 启动于 {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    sys.stdout = stream
    sys.stderr = stream
    return log_path


# --------------------------------------------------------------------------
# 本地服务
# --------------------------------------------------------------------------


def _listening_socket() -> tuple[socket.socket, int]:
    """在 127.0.0.1 上占一个系统分配的空端口，返回已 listen 的 socket 与端口号。

    刻意不设 SO_REUSEADDR：Windows 上它允许别的进程绑到同一端口，正好是这里想
    避免的事。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock, sock.getsockname()[1]


def _serve(sock: socket.socket) -> "object":
    """在守护线程里跑 uvicorn，返回 Server 实例供关窗时收尾。"""
    import uvicorn

    from app.main import app as fastapi_app

    config = uvicorn.Config(
        fastapi_app,
        log_level="warning",
        # 端口已经由 sock 决定，这里不再传 host/port
        access_log=False,
    )
    server = uvicorn.Server(config)

    def run() -> None:
        try:
            server.run(sockets=[sock])
        except Exception:  # noqa: BLE001 - 线程里的异常必须自己落盘，否则静默消失
            import traceback

            traceback.print_exc()

    threading.Thread(target=run, name="uvicorn", daemon=True).start()
    return server


def _wait_until_ready(port: int, timeout: float = READY_TIMEOUT_SECONDS) -> bool:
    """轮询到服务能正常应答 HTTP 为止。

    只看「有没有回一个 HTTP 响应」，不挑状态码：能应答就说明 ASGI 栈已经起来，
    后续路由由前端自己去要。
    """
    import httpx

    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/"
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0) as client:
                client.get(url)
            return True
        except Exception:  # noqa: BLE001 - 服务还没起来，继续等
            time.sleep(READY_POLL_INTERVAL)
    return False


# --------------------------------------------------------------------------
# 界面
# --------------------------------------------------------------------------


def _show_error(message: str) -> None:
    """弹一个原生对话框。无控制台的 exe 里，这是唯一能让用户看到错误的途径。"""
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, WINDOW_TITLE, 0x10)
    except Exception:  # noqa: BLE001 - 非 Windows 或调用失败时退回打印
        print(message)


def _open_window(url: str) -> None:
    """开原生窗口。失败则抛出，由调用方决定退回浏览器。

    强制走 edgechromium（WebView2）后端：pywebview 在 Windows 上还留着基于 IE 的
    mshtml 后端，界面用的现代 JS 在那上面会碎成一片白屏或错乱布局——那种「看起来
    像坏了」比明确报错再退回浏览器糟得多。
    """
    import webview

    webview.create_window(
        WINDOW_TITLE,
        url,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=WINDOW_MIN_SIZE,
        text_select=True,
    )
    # 把 WebView2 的缓存放到 data/ 下，别散在用户临时目录里
    webview.start(
        gui="edgechromium",
        private_mode=False,
        storage_path=str(data_dir() / "webview"),
    )


def _fallback_to_browser(url: str, reason: str) -> None:
    """原生窗口开不出来时，退回默认浏览器，保证功能可用。"""
    _show_error(
        "无法打开内置窗口，已改用默认浏览器。\n\n"
        f"地址：{url}\n"
        "（关闭本程序请在任务管理器结束 Easy-Chatbox）\n\n"
        f"原因：{reason}"
    )
    webbrowser.open(url)
    # 浏览器是独立进程，这里必须继续活着，否则服务随进程一起没了
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------


def main() -> int:
    log_path = _redirect_output_to_log()

    try:
        sock, port = _listening_socket()
    except OSError as exc:
        _show_error(f"无法在本机监听端口，启动失败。\n\n原因：{exc}")
        return 1

    url = f"http://127.0.0.1:{port}/"
    print(f"数据目录：{data_dir()}")
    print(f"本地服务：{url}")

    server = _serve(sock)

    if not _wait_until_ready(port):
        detail = f"\n\n日志：{log_path}" if log_path else ""
        _show_error(
            "本地服务启动超时。\n\n"
            "常见原因是安全软件拦截了程序在本机监听端口，"
            "把本程序加入信任列表后重试即可。" + detail
        )
        return 1

    try:
        _open_window(url)
    except Exception as exc:  # noqa: BLE001 - 缺 WebView2 运行时等都归到这里
        import traceback

        traceback.print_exc()
        _fallback_to_browser(url, f"{type(exc).__name__}: {exc}")

    # 关窗后请求服务退出；它在守护线程里，进程结束也会被收走
    setattr(server, "should_exit", True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
