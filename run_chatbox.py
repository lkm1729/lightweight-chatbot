"""桌面版启动脚本，也是 PyInstaller 的打包入口。

单独留一个顶层脚本而不是直接打 ``app/desktop.py``：那样同一个文件既是包内模块
又是 ``__main__``，同一份代码会被导入两次（一次作为 ``app.desktop``，一次作为
``__main__``），模块级状态跟着出现两份。

开发时想看真实窗口效果，直接跑：uv run python run_chatbox.py
"""

from __future__ import annotations

from app.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
