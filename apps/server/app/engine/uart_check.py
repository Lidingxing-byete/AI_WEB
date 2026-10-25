"""`check_uart_config` —— 配置一致性检查（工具 T-03）。

设计要点：
1. 每条检查**绑定具体规则号**（R-S1-01 … R-S6-04），结论可追溯到语料。
2. 三态判定：`violated` / `ok` / `unknown`。
   `unknown` 不是失败，而是"静态层面确实判不了"，必须如实返回，
   由编排层转成「需用户补充的信息」——这是"知道自己不知道"的实现。
3. 绝不把 `unknown` 包装成确定结论（Phase 4 验收项）。

所有产出标签恒为 `[静态分析]`：本模块只读代码，不上板。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import (
    DEBUG_PINS,
    GPIO_MODE_RX_OK,
    GPIO_MODE_TX_OK,
    USART_BUS,
    USART_PINS,
)
from .cparse import CodeFacts, GpioInit
from .evidence import EvidenceTag

TAG_STATIC = EvidenceTag.STATIC.value


@dataclass
class Check:
    """单条检查结果。"""

    rule_id: str
    status: str                 # violated / ok / unknown
    text: str
    tag: str = TAG_STATIC
    ref: str | None = None
    hint: str | None = None     # unknown 时给出"需要什么才能判定"

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "status": self.status,
            "text": self.text,
            "tag": self.tag,
            "ref": self.ref,
            "hint": self.hint,
        }


@dataclass
class CheckReport:
    instance: str = "USART1"
    checks: list[Check] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def violated(self) -> list[Check]:
        return [c for c in self.checks if c.status == "violated"]

    def unknown(self) -> list[Check]:
        return [c for c in self.checks if c.status == "unknown"]

    def ok(self) -> list[Check]:
        return [c for c in self.checks if c.status == "ok"]

    def to_dict(self) -> dict:
        return {
            "instance": self.instance,
            "checks": [c.to_dict() for c in self.checks],
            "limitations": self.limitations,
        }


# ------------------------------------------------------------------ 辅助


# 仅在"材料确实属于 UART 场景"时才做违规判定。判定依据是可观测事实，不是猜测。
UART_RULE_IDS = (
    "R-S1-01", "R-S1-02", "R-S1-03", "R-S1-04", "R-S1-05", "R-S1-06",
    "R-S2-01", "R-S2-02", "R-S2-03", "R-S2-04",
    "R-S3-01", "R-S3-02",
    "R-S4-01", "R-S4-02", "R-S4-03",
    "R-S5-01", "R-S5-02",
    "R-S6-01", "R-S6-02", "R-S6-03", "R-S6-04",
)


def has_usart_evidence(facts: CodeFacts) -> bool:
    """材料中是否存在任何 USART 相关事实。

    为什么要这一步（Phase 6 评测 N-01 / N-03 抓出的缺陷）：
    原实现在事实全空时依然按默认 USART1 去判"未找到时钟使能 → violated"，
    等于把**"没看到"当成了"确定有错"**。空材料、或提交的是 I2C 代码的场景
    下，系统会凭空报出 USART1 时钟未使能等根因——这正是项目红线所禁止的
    "强行给出唯一根因"。
    """
    if facts.usart_inits or facts.usart_cmd or facts.usart_it:
        return True
    clocks = list(facts.apb2_enabled) + list(facts.apb1_enabled)
    return any("USART" in t.upper() for t in clocks)


def _pin_init(facts: CodeFacts, port: str, pin: str) -> GpioInit | None:
    """找出配置了指定端口 + 引脚的那次 GPIO_Init。"""
    for g in facts.gpio_inits:
        if g.port == port and pin in g.pins:
            return g
    return None


def _uses_remap_pins(facts: CodeFacts, instance: str) -> bool:
    remap_port, remap_tx, _ = USART_PINS.get(instance, USART_PINS["USART1"])["remap"]
    return any(g.port == remap_port for g in facts.gpio_inits)


def check_uart_config(
    facts: CodeFacts,
    instance: str | None = None,
    actual_hse_hz: int | None = None,
) -> CheckReport:
    """对解析出的事实做配置一致性检查。

    Args:
        facts: `analyze_c_code` 的输出
        instance: 期望检查的 USART 实例；为空时取源码中实际初始化的第一个
        actual_hse_hz: 用户提供的实际外部晶振频率；提供后才可判定 R-S2-01
    """
    if facts.usart_inits:
        inst = facts.usart_inits[0].instance
    else:
        inst = instance or "USART1"

    report = CheckReport(instance=inst)
    report.limitations.extend(facts.limitations)

    # ---------------- 场景前置判定：材料是否属于 UART 场景 ----------------
    if not has_usart_evidence(facts):
        report.limitations.append(
            "材料中未检出任何 USART 相关配置（无 USART_Init / USART_Cmd / "
            "串口中断使能 / USART 时钟使能）。当前版本仅支持 STM32F103C8T6 的 "
            "USART 故障诊断，本次材料可能**超出支持范围**；"
            "以下 UART 专属检查一律记为 unknown——没看到不等于有错误，"
            "绝不据此判定违规。"
        )
        for rid in UART_RULE_IDS:
            report.checks.append(Check(
                rid, "unknown",
                "材料中未检出 USART 相关配置，无法判定本条",
                hint="请确认提交的是 USART 工程代码；若问题来自其他外设"
                     "（I2C / SPI / CAN 等），当前版本不支持",
            ))
        return report

    bus = USART_BUS.get(inst, "APB2")
    pins = USART_PINS.get(inst, USART_PINS["USART1"])
    def_port, def_tx, def_rx = pins["default"]
    remap_port, remap_tx, remap_rx = pins["remap"]

    enabled = facts.apb2_enabled if bus == "APB2" else facts.apb1_enabled
    commented_names = {name for name, _ in facts.commented_apis}

    # 实际使用的端口：优先看源码里配了哪个
    using_remap = _uses_remap_pins(facts, inst)
    port = remap_port if using_remap else def_port
    tx_pin = remap_tx if using_remap else def_tx
    rx_pin = remap_rx if using_remap else def_rx

    # ---------------- R-S1-01 外设时钟未使能 ----------------
    periph_token = f"RCC_{bus}Periph_{inst}"
    if periph_token in enabled:
        report.checks.append(Check(
            "R-S1-01", "ok",
            f"{inst} 外设时钟已使能（{periph_token} 出现在 RCC_{bus}PeriphClockCmd 参数中）",
            ref=f"RM0008 Ch.7 RCC（{bus} 外设时钟使能）",
        ))
    elif f"RCC_{bus}PeriphClockCmd" in commented_names:
        report.checks.append(Check(
            "R-S1-01", "violated",
            f"未找到 {periph_token} 的使能，且源码注释中存在 RCC_{bus}PeriphClockCmd 调用——"
            f"该使能很可能被注释掉了",
            ref="RM0008 Ch.7 RCC（APB2 外设时钟使能）",
            hint="取消注释并确认参数含 " + periph_token,
        ))
    else:
        report.checks.append(Check(
            "R-S1-01", "violated",
            f"未找到 {periph_token} 的使能调用",
            ref="RM0008 Ch.7 RCC（APB2 外设时钟使能）",
        ))

    # ---------------- R-S1-02 GPIO 端口时钟未使能 ----------------
    port_token = f"RCC_APB2Periph_{port}"
    if port_token in facts.apb2_enabled:
        report.checks.append(Check(
            "R-S1-02", "ok",
            f"{port} 端口时钟已使能（{port_token}）",
            ref="RM0008 Ch.7 RCC",
        ))
    else:
        report.checks.append(Check(
            "R-S1-02", "violated",
            f"未找到 {port} 端口时钟使能（{port_token}）",
            ref="RM0008 Ch.7 RCC",
        ))

    # ---------------- R-S1-04 重映射但 AFIO 时钟未开 ----------------
    if using_remap or facts.pin_remaps:
        if "RCC_APB2Periph_AFIO" in facts.apb2_enabled:
            report.checks.append(Check(
                "R-S1-04", "ok",
                "使用了重映射相关配置，且 AFIO 时钟已使能",
                ref="RM0008 Ch.9 AFIO",
            ))
        else:
            report.checks.append(Check(
                "R-S1-04", "violated",
                "检测到重映射引脚/重映射配置，但 AFIO 时钟未使能——"
                "重映射必须先开启 AFIO 时钟，否则引脚仍是默认功能",
                ref="RM0008 Ch.9 AFIO（重映射需先使能 AFIO 时钟）",
            ))
    else:
        report.checks.append(Check(
            "R-S1-04", "ok",
            "未使用重映射引脚，本条不适用",
            ref="RM0008 Ch.9 AFIO",
        ))

    # ---------------- R-S3-01 引脚与实例不匹配 ----------------
    tx_init = _pin_init(facts, port, tx_pin)
    if tx_init is None:
        got = ", ".join(
            f"{g.port}:{','.join(g.pins) or '?'}" for g in facts.gpio_inits
        ) or "（未提取到任何 GPIO_Init）"
        report.checks.append(Check(
            "R-S3-01", "violated",
            f"{inst} 的 TX 应为 {port}.{tx_pin}，但源码中未找到该引脚的 GPIO_Init；"
            f"实际提取到的引脚配置为 {got}",
            ref="RM0008 Ch.9（复用功能引脚映射）",
        ))
    else:
        report.checks.append(Check(
            "R-S3-01", "ok",
            f"{inst} TX 引脚 {port}.{tx_pin} 已配置",
            ref="RM0008 Ch.9（复用功能引脚映射）",
        ))

    # ---------------- R-S3-02 调试引脚占用 ----------------
    if tx_pin in DEBUG_PINS and DEBUG_PINS[tx_pin] == port:
        released = any("SWJ" in r.upper() for r in facts.pin_remaps)
        if released:
            report.checks.append(Check(
                "R-S3-02", "ok",
                f"{port}.{tx_pin} 属调试引脚，源码中已调用 GPIO_PinRemapConfig 释放",
                ref="RM0008 Ch.9（调试端口复用）",
            ))
        else:
            report.checks.append(Check(
                "R-S3-02", "violated",
                f"{port}.{tx_pin} 默认为 JTAG 引脚，未调用 GPIO_PinRemapConfig 释放前作 GPIO 无效",
                ref="RM0008 Ch.9（调试端口复用）",
            ))

    # ---------------- R-S1-03 TX 模式必须复用推挽 ----------------
    if tx_init is None:
        report.checks.append(Check(
            "R-S1-03", "unknown",
            "未提取到 TX 引脚的 GPIO_Init，无法判定模式",
            hint="提供包含 TX 引脚初始化的源码",
        ))
    elif tx_init.mode in GPIO_MODE_TX_OK:
        report.checks.append(Check(
            "R-S1-03", "ok",
            f"TX 引脚模式为 {tx_init.mode}（复用输出），符合要求",
            ref="RM0008 Ch.9 GPIO（复用功能输出配置）",
        ))
    else:
        report.checks.append(Check(
            "R-S1-03", "violated",
            f"TX 引脚模式为 {tx_init.mode or '（未设置）'}，不是复用推挽输出（GPIO_Mode_AF_PP）；"
            f"非复用模式下引脚不会被 USART 外设驱动",
            ref="RM0008 Ch.9 GPIO（复用功能输出配置）",
        ))

    # ---------------- R-S4-01 RX 必须为输入模式 ----------------
    rx_init = _pin_init(facts, port, rx_pin)
    if rx_init is None:
        report.checks.append(Check(
            "R-S4-01", "unknown",
            f"未提取到 RX 引脚 {port}.{rx_pin} 的配置，无法判定模式",
            hint="若仅发送不接收则本条不适用；需接收请提供 RX 引脚初始化代码",
        ))
    elif rx_init.mode in GPIO_MODE_RX_OK:
        report.checks.append(Check(
            "R-S4-01", "ok",
            f"RX 引脚模式为 {rx_init.mode}（输入模式），符合要求",
            ref="RM0008 Ch.9 GPIO（输入配置）",
        ))
    else:
        report.checks.append(Check(
            "R-S4-01", "violated",
            f"RX 引脚模式为 {rx_init.mode}，不是输入模式；"
            f"RX 被配成输出会与对端 TX 互相驱动，可能损坏引脚",
            ref="RM0008 Ch.9 GPIO（输入配置）",
        ))

    # ---------------- R-S4-02 接收未使能 ----------------
    mode_field = ""
    if facts.usart_inits:
        mode_field = facts.usart_inits[0].mode or ""
    rx_it = any(i == inst and "RXNE" in s for i, s in facts.usart_it)
    if "Rx" in mode_field or "USART_Mode_Rx" in mode_field:
        report.checks.append(Check(
            "R-S4-02", "ok",
            f"{inst} 的 USART_Mode 包含接收（{mode_field}）",
            ref="RM0008 Ch.19 USART（CR1 RE 位）",
        ))
    elif rx_it:
        report.checks.append(Check(
            "R-S4-02", "ok",
            f"已使能 {inst} 的 RXNE 接收中断",
            ref="RM0008 Ch.19 USART（CR1 RE / RXNEIE 位）",
        ))
    else:
        report.checks.append(Check(
            "R-S4-02", "unknown",
            f"未在 {inst} 的 USART_Mode 中见到接收使能，也未见到 RXNE 中断使能；"
            f"若程序需要接收数据则本条成立",
            hint="确认是否仅需发送；若需接收，检查 USART_Mode 是否含 USART_Mode_Rx",
            ref="RM0008 Ch.19 USART（CR1 RE 位）",
        ))

    # ---------------- R-S5-01 printf 未重定向 ----------------
    if facts.uses_printf:
        if facts.redirect_impl:
            report.checks.append(Check(
                "R-S5-01", "ok",
                f"使用 printf 且已实现底层重定向（{', '.join(facts.redirect_impl)}）",
                ref="RM0008 Ch.19 USART（发送流程）",
            ))
        else:
            report.checks.append(Check(
                "R-S5-01", "violated",
                "源码调用了 printf，但未找到 fputc / _write / __io_putchar 重定向实现；"
                "printf 不会有串口输出",
                ref="RM0008 Ch.19 USART（发送流程）",
            ))
    else:
        report.checks.append(Check(
            "R-S5-01", "ok",
            "源码未使用 printf，本条不适用",
        ))

    # ---------------- R-S5-02 MicroLIB / 半主机 ----------------
    if facts.uses_printf and facts.redirect_impl:
        report.checks.append(Check(
            "R-S5-02", "unknown",
            "是否启用 MicroLIB 属工程配置（Keil Options for Target → Target），"
            "不在源码文本中，静态无法判定；未启用时半主机可能导致程序卡死",
            hint="在 Keil 中勾选 Use MicroLIB，或确认链接器未引入半主机",
        ))

    # ---------------- R-S6-01 中断未挂 NVIC ----------------
    if facts.usart_it:
        expect_irq = f"{inst}_IRQn"
        configured = any(
            (v.irq_channel and inst in v.irq_channel) for v in facts.nvic_inits
        )
        if configured:
            report.checks.append(Check(
                "R-S6-01", "ok",
                f"{inst} 中断已在 NVIC 中配置",
                ref="RM0008 Ch.10 NVIC",
            ))
        else:
            report.checks.append(Check(
                "R-S6-01", "violated",
                f"已调用 USART_ITConfig 使能 {inst} 中断，但 NVIC_Init 中未配置 {expect_irq}；"
                f"中断不会真正进入",
                ref="RM0008 Ch.10 NVIC（中断使能寄存器）",
            ))

        # ---------------- R-S6-02 ISR 函数名与向量表一致 ----------------
        expect_isr = f"{inst}_IRQHandler"
        if expect_isr in facts.isr_functions:
            report.checks.append(Check(
                "R-S6-02", "ok",
                f"已定义 {expect_isr}，与启动文件向量表默认名一致",
                ref="RM0008 Ch.10 / 启动文件 startup_stm32f10x_md.s",
            ))
        else:
            got = ", ".join(facts.isr_functions) or "（未提取到任何 IRQHandler 定义）"
            report.checks.append(Check(
                "R-S6-02", "violated",
                f"使能了 {inst} 中断但未找到 {expect_isr} 定义；"
                f"实际提取到的中断服务函数为 {got}",
                ref="启动文件 startup_stm32f10x_md.s（向量表函数名）",
            ))

        # ---------------- R-S6-03 / R-S6-04 由 T-06 负责 ----------------
        # 这两条必须读中断服务函数**函数体**才能判定（是否读 DR、是否处理 ORE），
        # 本模块只看得到调用与名字，因此不在本层产出任何结论——
        # 恒返回 unknown 会让用户误以为"已检查过但判不了"，实际是**没检查**。
        # 判定交由 `engine.isr_check.analyze_isr_body`（工具 T-06）完成。

    # ---------------- R-S2-01 HSE_VALUE 与实际晶振 ----------------
    if actual_hse_hz is not None and facts.hse_value is not None:
        if facts.hse_value == actual_hse_hz:
            report.checks.append(Check(
                "R-S2-01", "ok",
                f"HSE_VALUE（{facts.hse_value}）与实际晶振（{actual_hse_hz}）一致",
                ref="RM0008 Ch.7 RCC（时钟树与 HSE）",
            ))
        else:
            report.checks.append(Check(
                "R-S2-01", "violated",
                f"HSE_VALUE 定义为 {facts.hse_value}，而实际晶振为 {actual_hse_hz}；"
                f"库函数按错误的 HSE 计算时钟，波特率会整体偏移",
                ref="RM0008 Ch.7 RCC（时钟树与 HSE）",
            ))
    else:
        report.checks.append(Check(
            "R-S2-01", "unknown",
            "判定本条需要同时知道源码中的 HSE_VALUE 与实际晶振频率",
            hint="提供板载晶振实际频率（看晶振丝印，常见 8MHz）",
        ))

    # ---------------- 静态无法判定的物理层与工程项 ----------------
    report.checks.append(Check(
        "R-S1-05", "unknown",
        "TX/RX 是否交叉连接、是否共地，属物理接线，静态不可见",
        hint="确认转换器 RXD 接 MCU TX、TXD 接 MCU RX，且 GND 共地",
    ))
    report.checks.append(Check(
        "R-S1-06", "unknown",
        "电平标准是否匹配（TTL 3.3V/5V 与 RS232）属硬件层，静态不可见",
        hint="确认转换器 IO 电平；RS232 电平不可直连 MCU 引脚",
    ))
    report.checks.append(Check(
        "R-S2-03", "unknown",
        "APB 分频配置是否在 SystemInit / SetSysClock 中被修改，需查看时钟配置函数",
        hint="提供系统时钟配置代码（SetSysClockTo72 等）以便计算真实 PCLK",
    ))
    report.checks.append(Check(
        "R-S2-04", "unknown",
        "通信双方参数是否一致需知道对端（PC 串口助手）设置的波特率/数据位/校验位/停止位",
        hint="提供对端串口参数；本项目已将 IO 电平 3.3V 记录在案",
    ))

    return report


def find_baud_targets(facts: CodeFacts) -> list[tuple[str, int]]:
    """从事实中提取（实例, 目标波特率）列表，供 `calc_baud_error` 使用。"""
    return [(u.instance, u.baudrate) for u in facts.usart_inits if u.baudrate]
