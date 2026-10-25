"""证据分级标签。

这是项目最高红线（AGENTS.md 第四节）的**代码层保障**：
任何对外呈现的结论、证据、候选根因，都必须携带证据标签，
`Finding` / `Candidate` 在构造时强制校验，缺标签直接抛错。

标签含义见 AGENTS.md 4.1，此处仅做机器可校验的枚举。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class EvidenceTag(str, Enum):
    """六级证据标签。"""

    MEASURED_HW = "[实测-硬件]"      # 用户在真实板卡上完成并提供原始记录
    MEASURED_SIM = "[实测-仿真]"     # Keil 仿真 / 软件模拟实际运行
    STATIC = "[静态分析]"            # 仅代码、配置、寄存器定义检查，未上板
    INFERRED = "[推断]"              # 基于已有证据的推理
    ASSUMED = "[假设]"               # 缺少证据时的显式声明
    UNVERIFIED = "[待验证]"          # 尚未取得任何证据


# 允许出现在结论中的标签（ASSUMED 需显式声明，允许但 UI 必须高亮）
ALL_TAGS = tuple(t.value for t in EvidenceTag)

# 只有这两级可以被视为"实测"，其余一律不得冒充
MEASURED_TAGS = (EvidenceTag.MEASURED_HW.value, EvidenceTag.MEASURED_SIM.value)


def is_measured(tag: str) -> bool:
    return tag in MEASURED_TAGS


def require_tag(tag: str) -> str:
    """校验标签合法，非法即抛错。

    用于让"忘记打标签"在开发期就暴露，而不是到评审时才被发现。
    """
    if tag not in ALL_TAGS:
        raise ValueError(
            f"非法证据标签 {tag!r}；必须取其中之一：{', '.join(ALL_TAGS)}"
        )
    return tag


class Finding:
    """一条诊断发现（证据 / 检查结论）。

    Attributes:
        source: 发现来源，如工具名 `check_uart_config` 或 `user_input`
        tag: 证据标签，强制
        text: 人类可读描述
        rule_id: 关联规则号（可选）
        ref: 技术依据引用，如 "RM0008 Ch.19"（章节级，不含手册原文）
    """

    __slots__ = ("source", "tag", "text", "rule_id", "ref")

    def __init__(
        self,
        source: str,
        tag: str | EvidenceTag,
        text: str,
        rule_id: str | None = None,
        ref: str | None = None,
    ) -> None:
        t = tag.value if isinstance(tag, EvidenceTag) else tag
        self.source = source
        self.tag = require_tag(t)
        self.text = text
        self.rule_id = rule_id
        self.ref = ref
        if not self.text.strip():
            raise ValueError("Finding.text 不得为空")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "tag": self.tag,
            "text": self.text,
            "rule_id": self.rule_id,
            "ref": self.ref,
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Finding {self.tag} {self.source}: {self.text[:40]}>"


class Candidate:
    """一个候选根因。

    score 只表示**证据支持度的相对排序**，不是概率，也不是准确率。
    """

    __slots__ = ("rule_id", "cause", "score", "supporting", "refuting",
                 "verification_steps", "ref", "linked_case", "rank",
                 "status")

    def __init__(
        self,
        rule_id: str,
        cause: str,
        score: float,
        supporting: list[Finding],
        refuting: list[Finding],
        verification_steps: list[str],
        ref: str | None = None,
        linked_case: str | None = None,
        rank: int = 0,
        status: str = "active",      # active / refuted / confirmed
    ) -> None:
        self.rule_id = rule_id
        self.cause = cause
        self.score = score
        self.supporting = supporting
        self.refuting = refuting
        self.verification_steps = verification_steps
        self.ref = ref
        self.linked_case = linked_case
        self.rank = rank
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rank": self.rank,
            "status": self.status,
            "cause": self.cause,
            "score": round(self.score, 3),
            "supporting": [f.to_dict() for f in self.supporting],
            "refuting": [f.to_dict() for f in self.refuting],
            "verification_steps": list(self.verification_steps),
            "ref": self.ref,
            "linked_case": self.linked_case,
        }
