"""运行期路径解析：开发树里和打包成 exe 之后都能找对地方。

区分两类路径，混用会出事：

* **可写数据**（``data/``：配置、会话库、附件）——必须放在 **exe 自身所在目录**。
  PyInstaller onefile 每次启动会把代码解压到一个临时目录（``sys._MEIPASS``），
  那个目录的名字每次都变、退出时还会被删掉，把配置写进去等于用户每次重开都是
  一张白纸。锚在 exe 旁边还顺带让整个文件夹能直接拷到 U 盘带走。
* **只读资源**（``web/`` 前端静态文件）——被打包进了 exe，得从 ``sys._MEIPASS``
  里取；开发时则在仓库根。

另外不能依赖「当前工作目录」：双击 exe 时 cwd 通常是 exe 所在处，但从快捷方式、
计划任务或别的目录启动时 cwd 会是别的地方，原先的相对路径 ``Path("data")`` 就会
在意料之外的位置又建一份空 data/，用户看起来就是「配置和历史全丢了」。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 允许外部指定数据目录，便于多份配置并存或放到同步盘里
DATA_DIR_ENV = "CHATBOX_DATA_DIR"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的 exe 里。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """可写数据的落脚点：打包后是 exe 所在目录，开发时是仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """``data/`` 的绝对路径，环境变量可覆盖。"""
    override = os.environ.get(DATA_DIR_ENV)
    if override and override.strip():
        return Path(override.strip()).expanduser().resolve()
    return app_root() / "data"


def bundle_dir() -> Path:
    """只读资源根目录：onefile 下是临时解压目录，开发时是仓库根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    """前端静态文件目录。"""
    return bundle_dir() / "web"
