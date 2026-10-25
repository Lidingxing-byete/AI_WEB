"""把模型能力包装成**受控工具**（T-08）。

为什么它必须走注册表
--------------------
不走注册表的话，模型调用就会变成编排层里的一段裸调用：
没有耗时、没有失败原因、不可单独测试、也无法在工具面板里被看见。
走注册表后，它与其它 7 个工具共享同一套留痕与失败处理，
**模型失败只记 error，不掀翻整轮诊断**。

为什么默认不注册
----------------
`build_default_registry()` 里**没有**它——默认引擎是纯规则层，
保证评测、冒烟、消融这些场景不受模型可用性与耗时影响。
只有显式开启（`DiagnosticRequest.use_llm=True`）时才由引擎注册。
"""

from __future__ import annotations

from ..engine.evidence import EvidenceTag
from .service import LlmNarrative, LlmService

TAG_INFERRED = EvidenceTag.INFERRED.value


def llm_assist(
    symptom_text: str = "",
    candidates: "list[dict] | None" = None,
    unknowns: "list[dict] | None" = None,
    references: "list[dict] | None" = None,
    service: LlmService | None = None,
) -> LlmNarrative:
    """对**已查出**的结论做解释，并补充用户可执行的验证建议。

    返回恒为 `LlmNarrative` 实例（失败时 degraded=True）。
    注意：本函数**不新增候选根因、不改评分**——模型没有诊断权。
    """
    svc = service or LlmService()
    return svc.narrate(
        symptom_text=symptom_text,
        candidates=candidates or [],
        unknowns=unknowns or [],
        references=references or [],
    )


def summarize_narrative(n: LlmNarrative) -> str:
    """工具面板/时间线里显示的一句话摘要。"""
    if not n.available:
        return f"未采用模型输出（{n.reason or '不可用'}），本轮结论来自规则层"
    return (f"{n.model} 生成解读 {len(n.summary)} 字"
            f"、补充步骤 {len(n.extra_steps)} 条"
            f"（{n.duration_ms} ms，标签 {n.tag}）")
