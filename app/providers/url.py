"""Base URL 归一化。

四套后端协议共用同一个 Base URL 输入框：用户只需填写基础地址
（`https://api.example.com` 或 `https://api.example.com/v1`），由本模块
推断出完整的请求端点，自动补齐 `v1` / `v1beta` 之类的版本段与协议路径。

约定：Base URL 以 `#` 结尾表示「这已经是完整端点，不要再补任何路径」，
与 Cherry Studio 等客户端一致，方便对接路径古怪的中转站。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit

# 形如 v1 / v2 / v1beta / v1alpha / v1beta2 的版本段
_VERSION_SEGMENT = re.compile(r"^v\d+(?:alpha|beta)?\d*$", re.IGNORECASE)

# 末尾 `#`：锁定 URL，跳过全部补全逻辑
LOCK_SUFFIX = "#"


class InvalidBaseURLError(ValueError):
    """Base URL 无法解析成合法端点。"""


@dataclass(frozen=True)
class ResolvedEndpoint:
    """归一化结果。`warnings` 供 UI 提示，不影响请求本身。"""

    url: str
    warnings: tuple[str, ...] = field(default=())


def resolve_endpoint(
    base_url: str,
    *,
    api_path: str,
    version_segment: str,
    endpoint_markers: Sequence[str] = (),
) -> ResolvedEndpoint:
    """把用户填写的 Base URL 补全为完整端点。

    Args:
        base_url: 用户输入，可以是裸域名、域名 + 版本段，或完整端点。
        api_path: 版本段之后的协议路径，如 ``messages``、``chat/completions``。
        version_segment: 该协议的默认版本段，如 ``v1``、``v1beta``。
        endpoint_markers: 若路径以其中任一项结尾，说明用户已给出完整端点。

    Raises:
        InvalidBaseURLError: 输入为空、协议不支持或缺少主机名。
    """
    raw = (base_url or "").strip()
    if not raw:
        raise InvalidBaseURLError("Base URL 不能为空")

    locked = raw.endswith(LOCK_SUFFIX)
    if locked:
        raw = raw[: -len(LOCK_SUFFIX)].strip()
        if not raw:
            raise InvalidBaseURLError("Base URL 不能只填一个 '#'")

    # 允许省略协议：api.openai.com -> https://api.openai.com
    if "://" not in raw:
        raw = f"https://{raw}"

    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise InvalidBaseURLError(
            f"不支持的协议 {parts.scheme!r}，请使用 http 或 https"
        )
    if not parts.netloc:
        raise InvalidBaseURLError(f"无法从 {base_url!r} 解析出主机名")
    # 主机名里带空白一定是笔误（如漏写协议的一句话），此时不该拼出个
    # 百分号转义的假地址去发请求，就地报错更好排查
    if any(ch.isspace() for ch in parts.netloc):
        raise InvalidBaseURLError(f"主机名 {parts.netloc!r} 含空格，请检查 Base URL")

    path = parts.path.rstrip("/")

    # 用户显式锁定，或路径本身已指向端点（含 Azure 那种自定义 deployment 路径）
    if locked or any(path.endswith(marker) for marker in endpoint_markers):
        return ResolvedEndpoint(_rebuild(parts, path))

    segments = [seg for seg in path.split("/") if seg]
    warnings: list[str] = []

    if segments and _VERSION_SEGMENT.match(segments[-1]):
        # 用户已写明版本段，尊重用户的选择：中转站常把 Gemini 挂在 /v1 上
        if segments[-1].lower() != version_segment.lower():
            warnings.append(
                f"Base URL 的版本段是 {segments[-1]!r}，该协议官方默认为 "
                f"{version_segment!r}；已按你填写的版本发起请求。"
            )
        tail = [*segments, api_path]
    else:
        tail = [*segments, version_segment, api_path]

    return ResolvedEndpoint(_rebuild(parts, "/" + "/".join(tail)), tuple(warnings))


def _rebuild(parts, path: str) -> str:
    """重组 URL，保留原有 query（Azure 的 ?api-version= 依赖它），丢弃 fragment。"""
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def mask_secret(secret: str, *, keep: int = 4) -> str:
    """把密钥掩码成 ``sk-ant…7f2a`` 形式，用于回传前端与写日志。"""
    if not secret:
        return ""
    if len(secret) <= keep * 2:
        return "…" * 3
    return f"{secret[:keep]}…{secret[-keep:]}"
