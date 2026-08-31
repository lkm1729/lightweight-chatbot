"""生成 exe 图标（无第三方依赖）。

只用标准库的 zlib + struct 手写一张 256×256 的 PNG，再套上 ICO 头。图标本身是
「圆角方块 + 白色对话气泡 + 三个点」，目的很实际：文件夹里一眼能认出该点哪个，
这比多一行说明有用。

Windows Vista 之后的 ICO 允许直接内嵌 PNG，所以单条 256×256 记录就够，小尺寸
由资源管理器自己缩。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 256

# 背景渐变（左上 → 右下）与气泡颜色
TOP_LEFT = (79, 70, 229)      # 靛蓝
BOTTOM_RIGHT = (124, 58, 237)  # 紫
BUBBLE = (255, 255, 255)


def _coverage(distance: float) -> float:
    """把「到边界的有符号距离」换成 0~1 的覆盖率，得到一像素宽的抗锯齿边。

    distance > 0 在形状内。不做超采样，单纯按距离线性过渡即可，
    这个尺寸下肉眼看不出差别。
    """
    return max(0.0, min(1.0, distance + 0.5))


def _rounded_rect_distance(
    x: float, y: float, left: float, top: float, right: float, bottom: float, radius: float
) -> float:
    """点到圆角矩形的有符号距离，内部为正。"""
    # 收缩到「圆心可活动的内矩形」，再按到该矩形的距离减去半径
    cx = min(max(x, left + radius), right - radius)
    cy = min(max(y, top + radius), bottom - radius)
    dx = x - cx
    dy = y - cy
    return radius - (dx * dx + dy * dy) ** 0.5


def _circle_distance(x: float, y: float, cx: float, cy: float, radius: float) -> float:
    return radius - ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5


def _blend(base: tuple[int, int, int], layer: tuple[int, int, int], alpha: float) -> tuple[int, int, int]:
    return (
        round(base[0] + (layer[0] - base[0]) * alpha),
        round(base[1] + (layer[1] - base[1]) * alpha),
        round(base[2] + (layer[2] - base[2]) * alpha),
    )


def _render() -> bytearray:
    """画出 RGBA 像素，返回 PNG 要求的「每行前置一个 filter 字节」的原始数据。"""
    raw = bytearray()

    # 气泡主体与尾巴的几何（相对 256 画布）
    b_left, b_top, b_right, b_bottom = 52.0, 62.0, 204.0, 168.0
    tail = ((84.0, 166.0), (84.0, 214.0), (132.0, 166.0))

    for py in range(SIZE):
        raw.append(0)  # filter type 0 (None)
        for px in range(SIZE):
            x = px + 0.5
            y = py + 0.5

            # 外形：圆角方块，决定 alpha
            outer = _coverage(_rounded_rect_distance(x, y, 2.0, 2.0, SIZE - 2.0, SIZE - 2.0, 54.0))
            if outer <= 0.0:
                raw.extend((0, 0, 0, 0))
                continue

            # 背景渐变
            t = (px + py) / (2.0 * SIZE)
            color = (
                round(TOP_LEFT[0] + (BOTTOM_RIGHT[0] - TOP_LEFT[0]) * t),
                round(TOP_LEFT[1] + (BOTTOM_RIGHT[1] - TOP_LEFT[1]) * t),
                round(TOP_LEFT[2] + (BOTTOM_RIGHT[2] - TOP_LEFT[2]) * t),
            )

            # 气泡：圆角矩形 + 左下角的三角尾巴
            bubble = _coverage(_rounded_rect_distance(x, y, b_left, b_top, b_right, b_bottom, 30.0))
            if bubble < 1.0:
                (x0, y0), (x1, y1), (x2, y2) = tail
                # 三条边的半平面取交集，等价于「在三角形内」。同号即可，
                # 这样顶点顺时针或逆时针给都成立，不必记住绕向
                e0 = (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0)
                e1 = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
                e2 = (x0 - x2) * (y - y2) - (y0 - y2) * (x - x2)
                if (e0 >= 0 and e1 >= 0 and e2 >= 0) or (e0 <= 0 and e1 <= 0 and e2 <= 0):
                    bubble = 1.0
            if bubble > 0.0:
                color = _blend(color, BUBBLE, bubble)

                # 气泡里的三个点：只在气泡实心处挖，避免糊到边缘
                for dot_x in (98.0, 128.0, 158.0):
                    dot = _coverage(_circle_distance(x, y, dot_x, 115.0, 11.0))
                    if dot > 0.0:
                        # 点的颜色取该处的背景色，看起来像镂空
                        hole = (
                            round(TOP_LEFT[0] + (BOTTOM_RIGHT[0] - TOP_LEFT[0]) * t),
                            round(TOP_LEFT[1] + (BOTTOM_RIGHT[1] - TOP_LEFT[1]) * t),
                            round(TOP_LEFT[2] + (BOTTOM_RIGHT[2] - TOP_LEFT[2]) * t),
                        )
                        color = _blend(color, hole, dot * bubble)

            raw.extend((color[0], color[1], color[2], round(outer * 255)))

    return raw


def _png(raw: bytearray) -> bytes:
    """把像素数据打成一个最小可用的 PNG。"""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)  # 8bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _ico(png: bytes) -> bytes:
    """单条记录的 ICO 容器。256 尺寸在头里写 0，这是格式规定。"""
    directory = struct.pack(
        "<BBBBHHII",
        0,          # width：256 记作 0
        0,          # height：同上
        0,          # 调色板数
        0,          # 保留字节
        1,          # 色彩平面
        32,         # 位深
        len(png),
        22,         # 数据偏移：6 字节文件头 + 16 字节目录项
    )
    return struct.pack("<HHH", 0, 1, 1) + directory + png


def build(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(_ico(_png(_render())))
    return output


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "chatbox.ico"
    print(f"图标已生成：{build(target)}")
