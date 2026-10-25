"""工具注册表 —— 受控的工具调用层（Phase 4 核心）。

为什么要有这一层
----------------
Phase 3 里 orchestrator 直接 `import` 各工具函数后裸调，带来三个问题：

1. **调用不可留痕**：工具耗时、入参摘要、失败原因没有统一落点，
   Phase 4 验收项"记录工具输入输出、执行状态和失败原因"无法自证。
2. **失败会连累整轮诊断**：任一工具抛异常，整条 SSE 流就 500。
3. **工具不可单独测试**：无法脱离编排层对某个工具做可重复的回归测试。

本模块把"调用"这件事收敛成一个入口：

    registry.invoke("analyze_c_code", seq=2, reason="...", input_sum="...",
                    source=src, path="main.c")

统一负责：计时 → 捕获异常 → 生成输出摘要 → 返回 `ToolRun` 记录。
**工具失败时 status 记为 `error` 并返回 None，绝不把异常抛给编排层**——
编排层据此走"该步不可信"的分支，而不是把半个结果包装成确定结论。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# 状态取值
STATUS_CALLED = "called"      # 实际执行且成功返回
STATUS_SKIPPED = "skipped"    # 条件不足，主动跳过（不是失败）
STATUS_ERROR = "error"        # 执行中抛异常


@dataclass
class ToolRun:
    """一次工具调用的可审计记录。

    与 `TraceStep` 的区别：TraceStep 是给 UI 看的时间线事件，
    ToolRun 是给审计与测试看的执行记录（含耗时、入参、错误）。
    orchestrator 会把它转成 TraceStep 推送并落库。
    """

    seq: int
    tool: str
    status: str
    reason: str = ""
    input_sum: str = ""
    output_sum: str = ""
    duration_ms: int = 0
    error: str | None = None

    def ok(self) -> bool:
        return self.status == STATUS_CALLED and self.error is None

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "tool": self.tool,
            "status": self.status,
            "reason": self.reason,
            "input_sum": self.input_sum,
            "output_sum": self.output_sum,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


@dataclass
class ToolSpec:
    name: str
    description: str
    fn: Callable[..., Any]
    summarize: Callable[[Any], str] | None = None
    # 该工具产出的结论默认证据等级（工具自己已在返回值里标时以其为准）
    default_tag: str = ""


class ToolRegistry:
    """工具注册表。工具必须显式注册，编排层不得调用未注册的函数。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        name: str,
        fn: Callable[..., Any],
        description: str = "",
        summarize: Callable[[Any], str] | None = None,
        default_tag: str = "",
    ) -> None:
        if name in self._tools:
            raise ValueError(f"工具重复注册：{name}")
        self._tools[name] = ToolSpec(
            name=name,
            description=description,
            fn=fn,
            summarize=summarize,
            default_tag=default_tag,
        )

    def unregister(self, name: str) -> bool:
        """注销工具。用于消融实验（Phase 6）在**受控**前提下替换某个工具，
        例如在评测中把检索换成空实现以量化 RAG 的实际贡献。
        """
        return self._tools.pop(name, None) is not None

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> list[dict]:
        """对外暴露工具清单（`/api/tools` 端点用）。"""
        return [
            {"name": s.name, "description": s.description,
             "default_tag": s.default_tag}
            for s in self._tools.values()
        ]

    # ------------------------------------------------------------------
    def invoke(
        self,
        name: str,
        seq: int,
        reason: str = "",
        input_sum: str = "",
        **kwargs: Any,
    ) -> tuple[ToolRun, Any | None]:
        """调用工具，返回 (调用记录, 结果 or None)。

        工具抛异常时：**记录 error，返回 None**，不向上抛。
        """
        spec = self._tools.get(name)
        if spec is None:
            return ToolRun(seq, name, STATUS_ERROR, reason, input_sum,
                           error=f"未注册的工具：{name}"), None

        t0 = time.perf_counter()
        try:
            result = spec.fn(**kwargs)
            err = None
            status = STATUS_CALLED
        except Exception as exc:                      # noqa: BLE001
            result = None
            err = f"{type(exc).__name__}: {exc}"
            status = STATUS_ERROR
        dur = int((time.perf_counter() - t0) * 1000)

        if result is not None and spec.summarize is not None:
            try:
                output_sum = spec.summarize(result)
            except Exception as exc:                  # noqa: BLE001
                output_sum = f"（摘要生成失败：{exc}）"
        elif result is None:
            output_sum = ""
        else:
            output_sum = str(result)[:200]

        return ToolRun(seq, name, status, reason, input_sum, output_sum, dur, err), result

    def skip(self, name: str, seq: int, reason: str, input_sum: str = "") -> ToolRun:
        """记录一次"主动跳过"。

        跳过必须写清原因——"没做"和"做了没问题"是两回事，
        混在一起就是误导（AGENTS.md 4.2）。
        """
        return ToolRun(seq, name, STATUS_SKIPPED, reason, input_sum)


def build_default_registry() -> ToolRegistry:
    """注册全部诊断工具。

    Phase 4 工具清单（T-01..T-06）：
        T-01 parse_build_log     解析编译/链接日志
        T-02 analyze_c_code      解析 C 源码，提取可检查事实
        T-03 check_uart_config   UART/GPIO/RCC/NVIC 配置一致性检查
        T-04 calc_baud_error     波特率误差计算（正向）
        T-04r infer_actual_fck   由实测波特率反推实际总线时钟（反向）
        T-05 search_reference    检索自建知识卡片（BM25）
        T-06 analyze_isr_body    中断服务函数体级检查
        T-07 parse_serial_capture 串口实测抓帧解析（Phase 5，唯一产生 [实测-硬件] 证据的工具）
    """
    from .baud import calc_baud_error, infer_actual_fck
    from .buildlog import parse_build_log
    from .cparse import analyze_c_code
    from .isr_check import analyze_isr_body
    from .serialcap import parse_serial_capture
    from .uart_check import check_uart_config

    reg = ToolRegistry()

    reg.register(
        "parse_build_log", parse_build_log,
        "解析编译/链接日志，提取错误与警告；编译不过时后续配置检查仅作参考",
        summarize=lambda r: f"错误 {r.error_count} 条，警告 {r.warning_count} 条",
        default_tag="[静态分析]",
    )
    reg.register(
        "analyze_c_code", analyze_c_code,
        "解析 C 源码，提取 RCC/GPIO/USART/NVIC/ISR 等可检查事实（绝不编译执行）",
        summarize=lambda r: (
            f"USART_Init {len(r.usart_inits)} 处、GPIO_Init {len(r.gpio_inits)} 处、"
            f"NVIC_Init {len(r.nvic_inits)} 处、ISR {len(r.isr_bodies)} 个"
        ),
        default_tag="[静态分析]",
    )
    reg.register(
        "check_uart_config", check_uart_config,
        "对解析出的事实做配置一致性检查，逐条绑定规则号，输出 violated/ok/unknown 三态",
        summarize=lambda r: (
            f"未通过 {len(r.violated())} 项、通过 {len(r.ok())} 项、"
            f"无法判定 {len(r.unknown())} 项"
        ),
        default_tag="[静态分析]",
    )
    reg.register(
        "calc_baud_error", calc_baud_error,
        "按 fCK/(16*DIV) 计算目标波特率的实际误差（纯算术，不交给模型心算）",
        summarize=lambda r: (
            f"{r.instance} {r.target_baud} → BRR={r.brr_hex}，"
            f"误差 {r.error_pct:.3f}%，"
            f"{'在容差内' if r.within_tolerance else '超出容差'}"
        ),
        default_tag="[静态分析]",
    )
    reg.register(
        "infer_actual_fck", infer_actual_fck,
        "由用户实测波特率反推固件运行时的真实总线时钟（反向比对）",
        summarize=lambda r: (
            f"假定 {r.nominal_fck_hz} Hz 应得 {r.expected_baud_at_nominal:.0f} bps，"
            f"实测 {r.observed_baud:.0f} bps → 实际约 {r.implied_fck_hz:,.0f} Hz"
        ),
        default_tag="[推断]",
    )
    reg.register(
        "analyze_isr_body", analyze_isr_body,
        "分析中断服务函数体，判定中断标志是否清除、是否处理接收溢出",
        summarize=lambda r: (
            f"分析 {len(r.analyzed)} 个 ISR，产出检查 {len(r.checks)} 条"
            if r.analyzed else "未找到匹配的中断服务函数，本工具不适用"
        ),
        default_tag="[静态分析]",
    )
    reg.register(
        "parse_serial_capture", parse_serial_capture,
        "解析用户上板采集的串口抓帧，判定 EMPTY/GARBLED/NORMAL，"
        "并与案例预期现象做一致性比对（Phase 5 硬件闭环）",
        summarize=lambda r: f"{r.verdict}：{r.note}",
        default_tag="[实测-硬件]",
    )
    return reg


def register_llm_tool(reg: ToolRegistry, service: "object | None" = None) -> bool:
    """按需注册 T-08「模型解读」工具。

    **默认注册表里没有它**——默认引擎是纯规则层，评测/冒烟/消融这些场景
    不该被模型可用性与耗时影响。只有显式开启时才注册。

    编排层调用时需把 service 实例作为 `service=` 传入，
    这样测试里可以塞一个假的 service 而不真的联网。
    """
    from ..llm.tool import llm_assist, summarize_narrative

    if reg.has("llm_assist"):
        return False
    reg.register(
        "llm_assist", llm_assist,
        "调用本地模型对**已查出**的结论做解释并补充验证建议；"
        "输出恒为 [推断]，必须先过证据纪律守卫，模型不可用时降级为规则层",
        summarize=summarize_narrative,
        default_tag="[推断]",
    )
    return True
