"""推理强度档位（7 档）与各协议的参数映射。

界面上是统一的 7 档，但四套协议表达「想多久」的方式互不相同：

* Anthropic —— 顶层 ``effort``，认 ``low|medium|high|xhigh|max``，配合
  ``thinking: {"type": "adaptive"}``
* OpenAI 两套 —— ``reasoning_effort`` / ``reasoning.effort``，认
  ``none|minimal|low|medium|high|xhigh``
* Gemini —— ``thinkingConfig.thinkingLevel``，认 ``low|medium|high``，且与
  ``thinkingBudget`` 互斥（同时给会被上游拒）

所以每档为三套协议各记一个落地值。某协议确实没有对等档位时（Anthropic 没有
``minimal``、OpenAI 没有 ``max``、Gemini 只有三级），取最近的一档并附一句说明，
由适配器包成 ``WarningEvent`` 交给前端——免得用户以为选了没生效。

``budget`` 是同一档位按 token 预算的表述，只在**回退路径**上用：较老的模型不认
上面这些档位字符串，收到会直接 400，这时 ``Provider.stream()`` 会改用旧写法
（``thinking.budget_tokens`` / ``thinkingBudget`` / 四级 effort）重发一次。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Mapped:
    """一个档位在某协议下的落地值。

    ``value`` 为 None 表示该协议下不发这个字段，适配器另有兜底（Anthropic 关掉
    thinking，Gemini 写 ``thinkingBudget: 0``）。``note`` 非空表示这是一次折衷
    映射——该协议没有真正对等的档位，需要如实告诉用户。
    """

    value: str | None
    note: str | None = None


@dataclass(frozen=True)
class ReasoningLevel:
    """一个推理强度档位。"""

    key: str
    label: str            # 中文档位名，界面直接用
    label_en: str
    budget: int           # 回退路径的思维链 token 预算，0 = 关闭思考
    anthropic: Mapped
    openai: Mapped
    gemini: Mapped
    note: str = ""


_NO_MINIMAL = "该协议的推理档位没有「最小」，已按 low 发送"
_ONLY_THREE = "Gemini 的 thinkingLevel 只有 low/medium/high 三级，已按 high 发送"
_NO_MAX = "OpenAI 的推理档位最高是 xhigh，「最高」已按 xhigh 发送"

# 顺序即界面上从弱到强的排列
LEVELS: tuple[ReasoningLevel, ...] = (
    ReasoningLevel(
        "none", "无", "None", 0,
        anthropic=Mapped(None),
        openai=Mapped("none"),
        gemini=Mapped(None),
        note="不做额外推理，最快",
    ),
    ReasoningLevel(
        "minimal", "最小", "Minimal", 1024,
        anthropic=Mapped("low", _NO_MINIMAL),
        openai=Mapped("minimal"),
        gemini=Mapped("low", _NO_MINIMAL),
    ),
    ReasoningLevel(
        "low", "低", "Low", 4096,
        anthropic=Mapped("low"),
        openai=Mapped("low"),
        gemini=Mapped("low"),
    ),
    ReasoningLevel(
        "medium", "中", "Medium", 8192,
        anthropic=Mapped("medium"),
        openai=Mapped("medium"),
        gemini=Mapped("medium"),
    ),
    ReasoningLevel(
        "high", "高", "High", 16384,
        anthropic=Mapped("high"),
        openai=Mapped("high"),
        gemini=Mapped("high"),
    ),
    ReasoningLevel(
        "xhigh", "极高", "Xhigh", 24576,
        anthropic=Mapped("xhigh"),
        openai=Mapped("xhigh"),
        gemini=Mapped("high", _ONLY_THREE),
    ),
    ReasoningLevel(
        "max", "最高", "Max", 32768,
        anthropic=Mapped("max"),
        openai=Mapped("xhigh", _NO_MAX),
        gemini=Mapped("high", _ONLY_THREE),
    ),
)

LEVELS_BY_KEY = {level.key: level for level in LEVELS}

DEFAULT_KEY = "medium"

# 回退路径上 OpenAI 老模型只认的四级
LEGACY_OPENAI_EFFORTS = frozenset({"minimal", "low", "medium", "high"})


def resolve(key: str | None) -> ReasoningLevel:
    """按 key 取档位，未知值回落到默认档。"""
    if not key:
        return LEVELS_BY_KEY[DEFAULT_KEY]
    return LEVELS_BY_KEY.get(key, LEVELS_BY_KEY[DEFAULT_KEY])


def list_levels() -> list[dict[str, object]]:
    """供前端抽屉使用的档位清单。"""
    return [
        {
            "key": level.key,
            "label": level.label,
            "label_en": level.label_en,
            "note": level.note,
        }
        for level in LEVELS
    ]


def legacy_openai_effort(level: ReasoningLevel) -> str | None:
    """回退路径上 OpenAI 能认的档位字符串；None 表示不发这个字段。"""
    if level.budget == 0:
        return None
    value = level.openai.value
    return value if value in LEGACY_OPENAI_EFFORTS else "high"
