"""配置文件管理（data/config.json）。

配置以「协议 → 供应商列表 → 模型列表」三层组织：四套协议是写死的适配器，
每套协议下可挂任意多个供应商（官方站或中转站），每个供应商各自带一份
Base URL / API Key 与自己支持的模型清单。模型分 ``id``（发给上游的底层
标识）与 ``name``（界面显示名），前端下拉框只显示后者。

API key 明文保存在本地，返回前端时掩码。
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.paths import data_dir
from app.providers.url import mask_secret

# 配置文件路径。取绝对路径而非相对的 "data"：打包成 exe 后 cwd 未必是 exe 所在处，
# 相对路径会在别的目录另建一份空配置，用户看起来就是密钥全没了。见 app.paths
CONFIG_DIR = data_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"

# 掩码标记：mask_secret 用 U+2026 省略号，真实密钥里不会出现
MASK_MARKER = "…"

# 当前配置结构版本。1 = 旧的「协议 → 单份配置」扁平结构。
SCHEMA_VERSION = 2


def _ensure_data_dir() -> None:
    """确保 data/ 目录存在。"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def new_id(prefix: str) -> str:
    """生成供应商 / 模型条目的稳定标识。"""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# --------------------------------------------------------------------------
# 结构规整与迁移
# --------------------------------------------------------------------------


def _normalize_model(raw: Any) -> dict[str, str] | None:
    """规整一条模型记录。

    只有底层 id 是必需的；显示名留空时回落到 id，免得下拉框出现空白项。
    """
    if isinstance(raw, str):
        raw = {"id": raw}
    if not isinstance(raw, dict):
        return None

    model_id = str(raw.get("id") or "").strip()
    if not model_id:
        return None
    name = str(raw.get("name") or "").strip() or model_id
    return {"id": model_id, "name": name}


def _normalize_vendor(raw: Any) -> dict[str, Any] | None:
    """规整一个供应商记录，缺失的 id 就地补一个。"""
    if not isinstance(raw, dict):
        return None

    models: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw.get("models") or []:
        model = _normalize_model(item)
        # 同一供应商内底层 id 去重，否则切换模型时无法区分
        if model and model["id"] not in seen:
            seen.add(model["id"])
            models.append(model)

    return {
        "id": str(raw.get("id") or "").strip() or new_id("v"),
        "name": str(raw.get("name") or "").strip(),
        "base_url": str(raw.get("base_url") or "").strip(),
        "api_key": raw.get("api_key") if isinstance(raw.get("api_key"), str) else "",
        "models": models,
    }


def _migrate_v1(raw: dict[str, Any]) -> dict[str, Any]:
    """把旧的扁平结构提升为 v2。

    v1 每个协议只有一份 ``{base_url, api_key, model}``，直接折叠成该协议下
    唯一的一个供应商，默认模型（若有）成为它的第一个模型条目。空壳配置
    （Base URL 与 Key 都没填）不必保留，否则界面上会多出四个空供应商。
    """
    providers: dict[str, Any] = {}
    for provider_name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        base_url = str(entry.get("base_url") or "").strip()
        api_key = entry.get("api_key") if isinstance(entry.get("api_key"), str) else ""
        model = str(entry.get("model") or "").strip()
        if not base_url and not api_key:
            providers[provider_name] = {"vendors": []}
            continue
        providers[provider_name] = {
            "vendors": [
                _normalize_vendor(
                    {
                        "name": "默认",
                        "base_url": base_url,
                        "api_key": api_key,
                        "models": [{"id": model, "name": model}] if model else [],
                    }
                )
            ]
        }
    return {"version": SCHEMA_VERSION, "providers": providers, "selection": {}}


def normalize_config(raw: Any) -> dict[str, Any]:
    """把任意来源的配置整成 v2 结构，顺带迁移旧版本。"""
    if not isinstance(raw, dict) or not raw:
        return {"version": SCHEMA_VERSION, "providers": {}, "selection": {}}

    # 没有 providers 键就是 v1 的扁平结构
    if "providers" not in raw:
        raw = _migrate_v1(raw)

    providers: dict[str, Any] = {}
    for provider_name, entry in (raw.get("providers") or {}).items():
        vendors_raw = entry.get("vendors") if isinstance(entry, dict) else None
        vendors: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in vendors_raw or []:
            vendor = _normalize_vendor(item)
            if vendor and vendor["id"] not in seen:
                seen.add(vendor["id"])
                vendors.append(vendor)
        providers[str(provider_name)] = {"vendors": vendors}

    selection = raw.get("selection")
    search = raw.get("search")
    return {
        "version": SCHEMA_VERSION,
        "providers": providers,
        "selection": selection if isinstance(selection, dict) else {},
        "search": _normalize_search(search),
    }


def _normalize_search(raw: Any) -> dict[str, Any]:
    """规整搜索配置。"""
    if not isinstance(raw, dict):
        return {
            "type": "",
            "name": "",
            "base_url": "",
            "api_key": "",
            "max_results": 5,
        }
    return {
        "type": str(raw.get("type") or "").strip(),
        "name": str(raw.get("name") or "").strip(),
        "base_url": str(raw.get("base_url") or "").strip(),
        "api_key": raw.get("api_key") if isinstance(raw.get("api_key"), str) else "",
        "max_results": int(raw.get("max_results") or 5) if isinstance(raw.get("max_results"), (int, float)) else 5,
    }


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    """读取配置文件并规整为 v2；不存在或损坏时返回空结构。"""
    if not CONFIG_FILE.exists():
        return normalize_config(None)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return normalize_config(None)
    return normalize_config(raw)


def save_config(config: dict[str, Any]) -> None:
    """规整后保存配置到文件，覆盖前先留一份上一版。

    配置文件里存着明文密钥，一次坏的写入（前端 bug、结构变更）就够把它们抹掉，
    ``config.json.bak`` 让这种情况还能手动救回来。
    """
    _ensure_data_dir()
    payload = normalize_config(config)
    if CONFIG_FILE.exists():
        try:
            # 就地取名而非用模块常量：测试会 monkeypatch CONFIG_FILE，
            # 常量会让备份漏写到真实的 data/ 里去
            CONFIG_FILE.with_name(CONFIG_FILE.name + ".bak").write_bytes(
                CONFIG_FILE.read_bytes()
            )
        except OSError:
            # 备份失败不该拦住正常保存
            pass
    CONFIG_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# 密钥掩码与合并
# --------------------------------------------------------------------------


def mask_api_keys(config: dict[str, Any]) -> dict[str, Any]:
    """返回前端前掩码每个供应商的 API key 以及搜索配置的 API key。"""
    result = normalize_config(config)
    for entry in result["providers"].values():
        entry["vendors"] = [
            {**vendor, "api_key": mask_secret(vendor.get("api_key") or "")}
            for vendor in entry["vendors"]
        ]
    # 搜索配置的密钥也必须掩码——漏掉就是泄密
    if result.get("search"):
        result["search"]["api_key"] = mask_secret(result["search"].get("api_key") or "")
    return result


def is_masked(api_key: Any) -> bool:
    """判断一个 api_key 是不是掩码后的占位符。

    掩码用的省略号 U+2026 不会出现在真实密钥里，据此区分即可。
    """
    return isinstance(api_key, str) and MASK_MARKER in api_key


def merge_config(incoming: dict[str, Any], stored: dict[str, Any]) -> dict[str, Any]:
    """合并前端提交的配置与已存配置，保护被掩码的密钥。

    ``GET /config`` 返回的是掩码后的密钥，前端原样回传时不能把占位符当成真
    密钥写回去，否则一次保存就把密钥毁了。此处按供应商 id 对齐，遇到掩码值
    （或空值）就沿用已存的明文。搜索配置的密钥也同样保护。
    """
    incoming = normalize_config(incoming)
    stored = normalize_config(stored)

    for provider_name, entry in incoming["providers"].items():
        stored_vendors = {
            vendor["id"]: vendor
            for vendor in stored["providers"].get(provider_name, {}).get("vendors", [])
        }
        for vendor in entry["vendors"]:
            api_key = vendor.get("api_key")
            if api_key and not is_masked(api_key):
                continue
            stored_key = stored_vendors.get(vendor["id"], {}).get("api_key")
            vendor["api_key"] = stored_key if isinstance(stored_key, str) else ""

    # 搜索配置的密钥也必须保护
    incoming_search_key = incoming.get("search", {}).get("api_key")
    if not incoming_search_key or is_masked(incoming_search_key):
        stored_search_key = stored.get("search", {}).get("api_key")
        incoming["search"]["api_key"] = stored_search_key if isinstance(stored_search_key, str) else ""

    return incoming


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------


def find_vendor(
    provider_name: str,
    vendor_id: str | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """按协议名 + 供应商 id 取供应商配置。

    ``vendor_id`` 为空时回落到该协议下的第一个供应商，让只有单个渠道的场景
    以及从 v1 迁移上来的配置不必关心 id。
    """
    conf = config if config is not None else load_config()
    vendors = normalize_config(conf)["providers"].get(provider_name, {}).get("vendors", [])
    if not vendors:
        return None
    if not vendor_id:
        return vendors[0]
    for vendor in vendors:
        if vendor["id"] == vendor_id:
            return vendor
    return None


def resolve_api_key(
    provider_name: str,
    api_key: str | None,
    vendor_id: str | None = None,
) -> str:
    """取实际发请求用的明文密钥。

    前端可能只持有掩码值（或压根不传），这时回落到本地配置里对应供应商的明文。
    """
    if api_key and not is_masked(api_key):
        return api_key
    vendor = find_vendor(provider_name, vendor_id)
    stored = vendor.get("api_key") if vendor else None
    return stored if isinstance(stored, str) else ""
