"""一键打包：生成图标 → 调 PyInstaller → 组装发布文件夹。

发布目录取名带明确指向（``Easy-Chatbox-聊天助手`` / ``Easy-Chatbox 启动.exe``），
让拿到文件夹的人不用问就知道该点哪个。

刻意只覆盖 exe 与说明文件，绝不整目录删重建：用户的 ``data/``（配置、历史、附件）
就在同一个文件夹里，一次「清空重建」就能把人家的密钥和聊天记录全抹掉。

    uv run python packaging/build.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent

SPEC = HERE / "Easy-Chatbox.spec"
ICON = HERE / "chatbox.ico"
README_SRC = HERE / "使用说明.txt"

# PyInstaller 的中间产物，别混进发布目录
WORK_DIR = HERE / "build_tmp"
DIST_DIR = HERE / "dist"

# 最终交付物
RELEASE_DIR = PROJECT_ROOT / "Easy-Chatbox-聊天助手"
EXE_NAME = "Easy-Chatbox 启动.exe"
README_NAME = "使用说明.txt"


def _run(command: list[str], label: str) -> None:
    print(f"\n>>> {label}")
    result = subprocess.run(command, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"{label} 失败（退出码 {result.returncode}）")


def build_icon() -> None:
    from make_icon import build  # 同目录，作为脚本运行时可直接导入

    build(ICON)
    print(f"图标：{ICON}")


def run_pyinstaller() -> Path:
    _run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            str(SPEC),
            "--noconfirm",
            "--workpath",
            str(WORK_DIR),
            "--distpath",
            str(DIST_DIR),
        ],
        "PyInstaller 打包",
    )
    built = DIST_DIR / "Easy-Chatbox.exe"
    if not built.exists():
        raise SystemExit(f"没找到打包产物：{built}")
    return built


def assemble(built_exe: Path) -> None:
    """把 exe 与说明放进发布目录，保留已存在的 data/。"""
    RELEASE_DIR.mkdir(exist_ok=True)

    target_exe = RELEASE_DIR / EXE_NAME
    # 覆盖前先删：正在运行的 exe 无法被覆盖，删不掉就说明程序还开着
    if target_exe.exists():
        try:
            target_exe.unlink()
        except OSError as exc:
            raise SystemExit(f"无法覆盖 {target_exe}，请先关闭正在运行的程序。\n{exc}")
    shutil.copy2(built_exe, target_exe)

    # 说明文件转成 CRLF + UTF-8 BOM，老一点的记事本也不会显示成乱码或一整行
    text = README_SRC.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    (RELEASE_DIR / README_NAME).write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))

    size_mb = target_exe.stat().st_size / 1024 / 1024
    print(f"\n发布目录：{RELEASE_DIR}")
    print(f"  {EXE_NAME}  ({size_mb:.1f} MB)")
    print(f"  {README_NAME}")


def cleanup() -> None:
    """清掉中间产物，只留发布目录。"""
    for path in (WORK_DIR, DIST_DIR):
        shutil.rmtree(path, ignore_errors=True)


def main() -> int:
    sys.path.insert(0, str(HERE))
    build_icon()
    built = run_pyinstaller()
    assemble(built)
    cleanup()
    print("\n完成。双击发布目录里的 exe 即可使用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
