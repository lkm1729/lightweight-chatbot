# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：单文件、无控制台窗口。

打包名用纯 ASCII（Easy-Chatbox），带中文的最终文件名由 build.py 改名得到——
PyInstaller 的中间产物目录会用这个名字，非 ASCII 在部分环境下会出岔子。
"""

from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH 由 PyInstaller 注入

# 动态 import 的模块，静态分析扫不到，必须手工点名
hiddenimports = [
    # uvicorn 用字符串按需加载各协议实现
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # pywebview 的 Windows 后端经 pythonnet 载入 WebView2
    "clr",
    "clr_loader",
    "webview.platforms.edgechromium",
    "webview.platforms.winforms",
]

# 测试与开发期依赖不进包，省体积
excludes = [
    "tkinter",
    "pytest",
    "_pytest",
    "respx",
    "watchfiles",  # 只有 --reload 用得到
    "pip",
    "setuptools._distutils",
]

a = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "run_chatbox.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    # 前端静态文件打进包内，运行时由 app.paths.web_dir() 定位
    datas=[(str(PROJECT_ROOT / "web"), "web")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Easy-Chatbox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # 刻意不开 UPX：压过的 exe 更容易被杀软误判，省下的体积不值这个麻烦
    upx=False,
    runtime_tmpdir=None,
    # 无控制台窗口。stdout/stderr 由 app.desktop 接到 data/startup.log
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "packaging" / "chatbox.ico"),
)
