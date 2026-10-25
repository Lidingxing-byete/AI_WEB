"""`analyze_isr_body` —— 中断服务函数体级检查（工具 T-06，Phase 4 新增）。

为什么需要这个工具
------------------
Phase 3 的 `check_uart_config` 只能看到"有没有写 ISR、名字对不对"，
而 R-S6-03 / R-S6-04 这两条规则**必须读函数体**才能判定：
    R-S6-03：中断里是否读了 DR（否则 RXNE 不清除，会反复进入中断）
    R-S6-04：是否处理接收溢出 ORE（否则溢出后接收彻底失效）
Phase 3 对这两条一律返回 unknown，C5/C6 案例因此无法量化。本工具补齐这一层。

判定纪律
--------
1. 只读代码文本，**不编译、不执行**（SEC-01）。
2. 只输出已有规则号（R-S6-03 / R-S6-04），不自造规则；
   造规则会让候选根因在语料库里查无此条，属证据链断裂。
3. 判不了就返回 unknown，并写清"需要什么才能判定"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .cparse import CodeFacts, IsrBody
from .evidence import EvidenceTag

TAG_STATIC = EvidenceTag.STATIC.value

# 读取接收数据寄存器：库函数或直接读 DR
_DR_READ_PATTERNS = (
    r"\bUSART_ReceiveData\s*\(",
    r"\bUSART\d?\s*->\s*DR\b",
    r"\bUSART_ReadData\s*\(",        # 少数工程的自封装
)

# 溢出标志处理：查 ORE 标志位（RM0008 Ch.19：ORE 需先读 SR 再读 DR 清除）
_ORE_PATTERNS = (
    r"USART_FLAG_ORE\b",
    r"USART_IT_ORE\b",
    r"\bUSART_GetFlagStatus\s*\([^)]*ORE",
    r"\bUSART_GetITStatus\s*\([^)]*ORE",
)

# 清 RXNE 的"错误姿势"：直接写 SR 寄存器清零（应靠读 DR 清除）
_SR_WRITE_PATTERN = r"\bUSART\d?\s*->\s*SR\s*="


@dataclass
class IsrReport:
    instance: str = "USART1"
    checks: list = field(default_factory=list)     # list[Check]，避免循环 import
    analyzed: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "instance": self.instance,
            "analyzed": self.analyzed,
            "checks": [c.to_dict() for c in self.checks],
            "limitations": self.limitations,
        }


def analyze_isr_body(facts: CodeFacts, instance: str = "USART1") -> IsrReport:
    """分析中断服务函数体，判定 R-S6-03 / R-S6-04。

    Args:
        facts: `analyze_c_code` 的输出（需含 `isr_bodies`）
        instance: 目标 USART 实例

    Returns:
        IsrReport。无 ISR 或不匹配时 `checks` 为空——**空列表表示不适用，
        不是"通过"**，编排层不得把它解释成 ok。
    """
    # 延迟导入：Check 定义在 uart_check 中，避免模块级循环依赖
    from .uart_check import Check

    report = IsrReport(instance=instance)
    expect_isr = f"{instance}_IRQHandler"
    bodies: list[IsrBody] = [b for b in facts.isr_bodies if b.name == expect_isr]

    if not facts.isr_bodies:
        report.limitations.append(
            "源码中未提取到任何 IRQHandler 函数体，本工具不适用"
        )
        return report
    if not bodies:
        report.limitations.append(
            f"提取到 {len(facts.isr_bodies)} 个中断服务函数（"
            f"{', '.join(b.name for b in facts.isr_bodies)}），"
            f"但没有 {expect_isr}，本工具对本实例不适用"
        )
        return report

    body_obj = bodies[0]
    body = body_obj.body
    report.analyzed.append(f"{body_obj.name}（第 {body_obj.line} 行）")

    reads_dr = _matches_any(body, _DR_READ_PATTERNS)
    handles_ore = _matches_any(body, _ORE_PATTERNS)
    writes_sr = re.search(_SR_WRITE_PATTERN, body) is not None

    # ---------------- R-S6-03 中断标志是否正确清除 ----------------
    if reads_dr:
        report.checks.append(Check(
            "R-S6-03", "ok",
            f"{expect_isr} 中调用了 USART_ReceiveData（或读 DR），"
            f"RXNE 标志会随读 DR 自动清除",
            ref="RM0008 Ch.19 USART（RXNE 由读 DR 清除）",
        ))
    elif writes_sr:
        report.checks.append(Check(
            "R-S6-03", "violated",
            f"{expect_isr} 中未读 DR，而是直接写 USART_SR；"
            f"写 SR 不能可靠清除 RXNE，中断会反复进入或丢失数据",
            ref="RM0008 Ch.19 USART（状态寄存器清除规则）",
            hint="改为调用 USART_ReceiveData(USARTx) 读一次 DR",
        ))
    else:
        report.checks.append(Check(
            "R-S6-03", "violated",
            f"{expect_isr} 中既未读 DR（USART_ReceiveData）也未处理 RXNE 标志；"
            f"RXNE 不会自动清除，中断退出后会立刻再次进入，程序表现为卡死或只响应一次",
            ref="RM0008 Ch.19 USART（RXNE 由读 DR 清除）",
            hint="在中断中调用 USART_ReceiveData 读取接收到的数据",
        ))

    # ---------------- R-S6-04 是否处理接收溢出 ----------------
    if handles_ore:
        report.checks.append(Check(
            "R-S6-04", "ok",
            f"{expect_isr} 中检测了 ORE 溢出标志，溢出后可恢复接收",
            ref="RM0008 Ch.19 USART（ORE 需先读 SR 再读 DR 清除）",
        ))
    else:
        report.checks.append(Check(
            "R-S6-04", "unknown",
            f"{expect_isr} 中未见到 ORE（溢出）标志处理；"
            f"是否成立取决于实际是否发生接收溢出——对端连续发送且 CPU 未及时取走时才会触发",
            hint="若接收中途彻底失效，按『读 SR → 读 DR』顺序清除 ORE；"
                 "本条需结合实测现象判定，静态不能确认为当前故障根因",
            ref="RM0008 Ch.19 USART（ORE 清除序列）",
        ))

    # ---------------- 解析局限如实声明 ----------------
    if not re.search(r"USART_GetITStatus|USART_GetFlagStatus", body):
        report.limitations.append(
            f"{expect_isr} 中未见 USART_GetITStatus / GetFlagStatus 判断，"
            f"若中断源由外部标志位驱动，本工具的判定可能不完整"
        )
    if len(body) < 20:
        report.limitations.append(
            f"{expect_isr} 函数体过短（{len(body)} 字符），可能是空实现或提取不完整"
        )

    return report


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    for p in patterns:
        if re.search(p, text):
            return True
    return False
