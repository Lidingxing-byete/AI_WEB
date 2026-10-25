"""提示词模板。

设计原则
--------
1. **模型只做两件事**：把已查出的结论讲清楚、补充"用户下一步可以做
   的检查"。它**不找根因**——根因由规则层的 `violated` 检查决定，
   模型新增的候选一律不进证据链（service 层用字段白名单强制）。
2. **系统提示里把证据纪律写成硬约束**，而不是靠模型自觉。
3. 输入只给"本次真实查出的东西"，不给它自由发挥的空间。

`build_baseline_prompt` 是**评测专用**：用于与"纯 LLM 基线"做公平对比
（Phase 6 验收项）。它不进主流程，其输出也不会变成系统结论。
"""

from __future__ import annotations

from ..config import CHIP_F103C8T6

# ---------------------------------------------------------------- 主流程

NARRATE_SYSTEM = """你是嵌入式故障诊断助手，服务于 STM32F103C8T6 的 UART 故障诊断工作台。

你的职责只有两条：
1. 把**已经由工具查出**的检查结论用工程师能看懂的话解释清楚。
2. 补充"用户下一步可以在板子上做的检查动作"。

红线（违反任何一条，输出会被系统直接丢弃）：
- 禁止声称任何实测、上板、烧录、验证已经完成。你没有做过，也不允许假装做过。
- 禁止输出任何方括号证据标签（例如 [静态分析]、[推断]、[实测-硬件]）。
  证据等级由系统统一标注，不由你决定。
- 禁止编造寄存器值、波特率数值、时钟频率等具体数字。
  只可使用输入中已给出的数字。
- 禁止新增输入之外的故障根因。你没有诊断权，只有解释权。
- 禁止使用"已验证""已确认""实测表明"这类措辞。
- 不确定时必须明说"仅凭现有材料无法判断"，不得硬给结论。

输出必须是 JSON 对象，且**只能**包含以下三个字段：
{"summary": "对已查出结论的解释，不超过 400 字符",
 "extra_steps": ["用户可执行的补充检查动作，最多 4 条，每条不超过 120 字符"],
 "cautions": ["容易踩的坑或前提提醒，最多 4 条"]}
不要输出 JSON 之外的任何文字。"""


def build_narrate_prompt(
    symptom_text: str,
    candidates: list[dict],
    unknowns: list[dict],
    references: list[dict],
) -> str:
    """构造主流程的解释请求。

    `candidates` 元素形如：
        {"rule_id": "R-S1-01", "cause": "...", "score": 1.5,
         "evidences": [{"item": "...", "tag": "STATIC", "detail": "..."}]}
    """
    lines: list[str] = []
    lines.append(f"目标芯片：{CHIP_F103C8T6['name']}（标准外设库 V3.5 风格）")
    lines.append(f"用户描述的现象：{symptom_text or '（用户未描述）'}")
    lines.append("")
    lines.append("以下结论由静态检查工具**实际查出**，请据此解释：")
    if candidates:
        for i, c in enumerate(candidates[:5], 1):
            ev = c.get("evidences") or []
            lines.append(f"{i}. 规则 {c.get('rule_id')}：{c.get('cause')}")
            for e in ev[:3]:
                lines.append(f"   - 证据：{e.get('item', '')}")
                if e.get("detail"):
                    lines.append(f"     来源：{e.get('detail')}")
    else:
        lines.append("（本轮未查出任何明确的违规项）")

    if unknowns:
        lines.append("")
        lines.append("以下项目**无法静态判定**，请在 cautions 中说明需要什么材料：")
        for u in unknowns[:5]:
            lines.append(f"- {u.get('rule_id')}：{u.get('text', '')}")

    if references:
        lines.append("")
        lines.append("可参考的自建知识卡片（仅标题，勿编造正文）：")
        for r in references[:5]:
            lines.append(f"- {r.get('title', '')}")

    lines.append("")
    lines.append("请输出 JSON。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 评测基线

BASELINE_SYSTEM = """你是嵌入式故障诊断助手。请依据给出的 STM32 源码与故障现象，判断最可能的故障根因。

要求：
- 从给定的候选规则编号中选择**一个**，输出其编号。
- 若材料明显不足（例如没有源码、或源码与串口无关），输出 INSUFFICIENT。
- 禁止编造寄存器值、时钟频率、波特率数值。
- 输出必须是 JSON：{"rule_id": "编号或 INSUFFICIENT", "reason": "不超过 200 字的依据"}
不要输出 JSON 之外的任何文字。"""


def build_baseline_prompt(
    source_code: str,
    symptom_text: str,
    rule_catalog: list[dict],
    build_log: str = "",
    max_source_chars: int = 6000,
) -> str:
    """构造"纯 LLM 基线"请求（评测专用，不进主流程）。

    为了让对比**公平**，模型与规则层必须看到**同一批材料**。
    早期版本漏传了编译日志，导致 N-02（真实问题是链接错误）这对模型
    不公平——规则层有日志、模型没有，差异会被误读成"模型不会诊断"。
    """
    src = (source_code or "").strip()
    if len(src) > max_source_chars:
        src = src[:max_source_chars] + "\n/* ……（为控制长度已截断）…… */"
    cat = "\n".join(f"- {r['rule_id']}: {r['brief']}" for r in rule_catalog)
    parts = [
        f"目标芯片：{CHIP_F103C8T6['name']}",
        f"故障现象：{symptom_text or '（用户未描述）'}",
    ]
    if (build_log or "").strip():
        log = build_log.strip()
        if len(log) > 2000:
            log = log[:2000] + "\n……（已截断）……"
        parts += ["", f"编译/链接日志：", log]
    parts += [
        "",
        f"候选根因（只能从中选一个，或输出 INSUFFICIENT）：\n{cat}",
        "",
        f"源码：\n{src if src else '（未提供源码）'}",
        "",
        "请输出 JSON。",
    ]
    return "\n".join(parts)
