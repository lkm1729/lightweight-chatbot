"""Provider 相关端点：协议清单、配置管理、连通性测试。"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import (
    is_masked,
    load_config,
    mask_api_keys,
    merge_config,
    resolve_api_key,
    save_config,
)
from app.providers import UnknownProviderError, get_provider, list_providers
from app.providers.base import ProviderConfig
from app.reasoning import list_levels
from app import search as search_module

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
async def get_providers() -> list[dict[str, str]]:
    """协议清单，供前端下拉框使用。"""
    return list_providers()


@router.get("/reasoning")
async def get_reasoning_levels() -> list[dict[str, Any]]:
    """推理强度档位清单，供前端抽屉使用。"""
    return list_levels()


@router.get("/config")
async def get_config() -> dict[str, Any]:
    """读取所有 provider 配置（含供应商与模型清单），密钥一律掩码。

    明文密钥不出后端：前端只需要知道「这里配过一把钥匙」，掩码值足够渲染表单。
    保存时前端原样回传掩码值，由 ``merge_config`` 用已存明文兜底。
    """
    return mask_api_keys(load_config())


class SaveConfigRequest(BaseModel):
    """保存配置请求体。"""

    config: dict[str, Any]


@router.post("/config")
async def update_config(request: SaveConfigRequest) -> dict[str, str]:
    """保存 provider 配置。

    前端拿到的密钥是掩码值，原样回传时用已存的明文兜底，避免一次保存就把
    真密钥覆盖成占位符。
    """
    save_config(merge_config(request.config, load_config()))
    return {"message": "配置已保存"}


class ProbeRequest(BaseModel):
    """连通性测试请求体。"""

    provider: str
    base_url: str
    api_key: str = ""
    model: str
    vendor_id: str | None = None
    extra_headers: dict[str, str] = {}


@router.post("/probe")
async def probe_provider(request: ProbeRequest) -> dict[str, Any]:
    """测试 provider 连通性，返回 ProbeResult（不含密钥）。"""
    try:
        provider_cls = get_provider(request.provider)
    except UnknownProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = ProviderConfig(
        base_url=request.base_url,
        # 前端可能只持有掩码值，回落到对应供应商的本地明文
        api_key=resolve_api_key(request.provider, request.api_key, request.vendor_id),
        extra_headers=request.extra_headers,
    )
    provider = provider_cls(config)

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=30.0)) as client:
        result = await provider.probe(request.model, client=client)

    # ProbeResult 可直接序列化，不含密钥
    return {
        "ok": result.ok,
        "endpoint": result.endpoint,
        "error": result.error,
        "status": result.status,
        "warnings": result.warnings,
    }


class SearchProbeRequest(BaseModel):
    """搜索连通性测试请求体。"""

    type: str
    base_url: str
    api_key: str = ""


@router.post("/search/probe")
async def probe_search(request: SearchProbeRequest) -> dict[str, Any]:
    """测试搜索 API 连通性，返回成功/失败与错误信息。"""
    stored = load_config().get("search", {})
    # 保存过一次后，前端持有的是掩码值（tvly-…abcd），原样发出去必然 401。
    # 与供应商 probe 的 resolve_api_key 同一个道理：认出掩码就回落到本地明文。
    api_key = request.api_key
    if not api_key or is_masked(api_key):
        api_key = stored.get("api_key") or ""
    base_url = request.base_url or stored.get("base_url") or ""
    search_type = request.type or stored.get("type") or ""

    if not search_type:
        return {"ok": False, "error": "请先选择搜索协议"}
    if not base_url:
        return {"ok": False, "error": "请先填写 Base URL"}
    # SearXNG 是自建服务，无需密钥
    if not api_key and search_type != "searxng":
        return {"ok": False, "error": "请先填写 API Key"}

    try:
        config = search_module.SearchConfig(
            type=search_type,
            name="",
            base_url=base_url,
            api_key=api_key,
            max_results=1,
        )
        results = await search_module.search(config, "test")
        return {"ok": True, "result_count": len(results)}
    except Exception as exc:
        return {"ok": False, "error": _friendly_search_error(exc)}


def _friendly_search_error(exc: Exception) -> str:
    """把 httpx 的异常翻成能看懂的话。"""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        hint = {
            401: "API Key 无效或已过期",
            403: "API Key 无权访问该端点",
            404: "端点不存在，检查 Base URL 是否正确",
            429: "请求过于频繁或额度已用尽",
        }.get(status)
        return f"[{status}] {hint}" if hint else f"[{status}] 上游返回错误"
    if isinstance(exc, httpx.HTTPError):
        return f"连接失败：{exc}"
    return str(exc)
