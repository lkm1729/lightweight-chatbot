"""把 web/markdown.js 的 JS 测试带进 pytest。

渲染器跑在浏览器里，但它的输出要进 innerHTML，是这个应用里最需要回归保护的一段
代码（模型输出属于不可信文本）。项目没有 JS 测试框架，也不该为此引一个，所以
markdown.js 写成了 Node 也能加载的形式，这里用子进程把它的测试跑起来——
``uv run pytest`` 一条命令仍能全跑到。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

TEST_SCRIPT = Path(__file__).parent / "markdown_test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能跑 JS 测试")
def test_markdown_renderer():
    """web/markdown.js 的全部断言，含 XSS 载荷。"""
    result = subprocess.run(
        ["node", str(TEST_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=TEST_SCRIPT.parent.parent,
    )
    # 失败细节在 stderr 里，直接抬到 pytest 的报错里，不用再手动跑一遍 node
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"


def test_renderer_is_plain_text_source():
    """markdown.js 不该含控制字符。

    占位符用 String.fromCharCode(0) 在运行时构造，而不是把 NUL 写进源码——
    源码里带裸控制字符会让 grep / diff 把它当二进制文件。
    """
    source = (TEST_SCRIPT.parent.parent / "web" / "markdown.js").read_bytes()
    assert b"\x00" not in source
