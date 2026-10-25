"""诊断编排（自研轻量状态机 + 受控工具注册表）。

为什么不用 LangChain / LangGraph（Phase 1 ADR-04）：
赛事硬性要求"须能解释全部代码逻辑"，框架的抽象层在答辩时讲不清，
且本项目的诊断流程是**确定的**——不需要让模型自由发挥编排权。

工具清单（T-01..T-06，全部经 `registry` 注册后调用）：
    ① parse_build_log     —— 仅在提供编译日志时调用
    ② analyze_c_code      —— 必调（后续步骤的事实来源）
    ③ check_uart_config   —— 依赖 ②
    ④ calc_baud_error     —— 仅在能确定总线时钟时调用（正向）
    ⑤ analyze_isr_body    —— 仅在使能了串口中断或存在 ISR 时调用
    ⑥ infer_actual_fck    —— 仅在用户提供实测波特率时调用（反向）
    ⑦ search_reference    —— 必调（RAG 可追溯性载体）

Phase 4 相对 Phase 3 的三处实质变化：
1. 全部工具改由 `ToolRegistry` 调用：**每次调用都有耗时、入参摘要、
   输出摘要与失败原因**，工具抛异常只记 error，不掀翻整轮诊断。
2. 新增 T-05 / T-06 两个工具，C2（波特率）与 C5/C6（中断）因此可量化，
   不再是恒 unknown。
3. 引入 `followups`：证据不足时明确列出"要继续诊断需要补充什么"，
   把一次性的问答变成**可分多轮推进的诊断流程**。
"""

from __future__ import annotations

from collections.abc import Callable

from dataclasses import dataclass, field

from ..config import CHIP_F103C8T6, USART_BUS
from ..corpus.bm25 import BM25
from ..corpus.loader import load_rules, rules_by_id
from .baud import calc_baud_error
from .cparse import analyze_c_code
from .evidence import Candidate, EvidenceTag, Finding
from .isr_check import analyze_isr_body
from .registry import ToolRegistry, ToolRun, build_default_registry
from .serialcap import (EMPTY, findings_from_capture, match_phenomena,
                        parse_serial_capture as _parse_serial_capture)  # noqa: F401
from .uart_check import check_uart_config, find_baud_targets

TAG_STATIC = EvidenceTag.STATIC.value
TAG_INFERRED = EvidenceTag.INFERRED.value
TAG_ASSUMED = EvidenceTag.ASSUMED.value
TAG_UNVERIFIED = EvidenceTag.UNVERIFIED.value

# 症状关键词 -> 症状编号。命中为 [推断]，未命中不猜。
SYMPTOM_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("S2", ("乱码", "乱字符", "不可读", "错字", "garbled", "乱")),
    ("S4", ("收不到", "接收失败", "接收不到", "没反应", "收不到数据", "无法接收")),
    ("S3", ("无波形", "没有波形", "波形", "示波器", "逻辑分析仪")),
    ("S5", ("不发送", "没有发送", "未发送", "初始化正常", "不发数据")),
    ("S6", ("中断", "进不去中断", "只进一次", "中断不进", "irq")),
    ("S1", ("无输出", "没有输出", "不输出", "没输出", "无任何输出", "不通")),
]

SYMPTOM_KEYWORDS_INDEX = {c: i for i, (c, _) in enumerate(SYMPTOM_KEYWORDS)}


def detect_symptom(text: str) -> tuple[str | None, list[str]]:
    """从现象描述推断症状编号。返回 (症状号 or None, 命中的关键词)。"""
    hits: list[str] = []
    scores: dict[str, int] = {}
    low = (text or "").lower()
    for code, words in SYMPTOM_KEYWORDS:
        for w in words:
            if w.lower() in low:
                scores[code] = scores.get(code, 0) + 1
                hits.append(w)
    if not scores:
        return None, []
    # 取命中次数最多的；并列时按 SYMPTOM_KEYWORDS 顺序（越靠前越特异）
    best = max(scores.items(), key=lambda kv: (kv[1], -SYMPTOM_KEYWORDS_INDEX[kv[0]]))
    return best[0], hits


@dataclass
class DiagnosticRequest:
    source_code: str = ""
    source_path: str = "main.c"
    symptom_text: str = ""
    chip: str = CHIP_F103C8T6["name"]
    build_log: str = ""
    actual_hse_hz: int | None = None
    pclk2_hz: int | None = None     # USART1 的总线时钟
    pclk1_hz: int | None = None     # USART2/3 的总线时钟
    # Phase 4：用户实测得到的实际波特率（来源：串口助手实测 / 示波器位宽）
    observed_baud: float | None = None
    observed_baud_source: str = ""
    # Phase 5：用户在真实板卡上采集的串口抓帧原文（**只能由用户提供**）
    serial_capture: str = ""
    serial_capture_source: str = ""
    # 是否启用模型解读（T-08）。默认关闭：
    # 引擎默认保持纯规则层，评测/冒烟/消融不被模型的可用性与耗时影响。
    use_llm: bool = False

    def has_material(self) -> bool:
        return bool(self.source_code.strip() or self.symptom_text.strip())


@dataclass
class TraceStep:
    seq: int
    tool: str
    status: str                 # called / skipped / error
    reason: str
    summary: str = ""
    input_sum: str = ""
    duration_ms: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {"seq": self.seq, "tool": self.tool, "status": self.status,
                "reason": self.reason, "summary": self.summary,
                "input_sum": self.input_sum, "duration_ms": self.duration_ms,
                "error": self.error}


@dataclass
class DiagnosticReport:
    # 默认 ok；只有在材料确实不足时才改判 insufficient
    status: str = "ok"
    symptom: str | None = None
    symptom_hits: list[str] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    unknowns: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)
    overall_tag: str = TAG_STATIC
    insufficient_reason: str = ""
    # Phase 5：串口实测现象（唯一可能带 [实测-硬件] 的模块输出）
    phenomenon: dict | None = None
    phenomenon_matches: list[dict] = field(default_factory=list)
    # 模型解读（T-08）：恒为 [推断]，且**不参与**候选排序与评分
    llm_narrative: dict | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "symptom": self.symptom,
            "symptom_hits": self.symptom_hits,
            "candidates": [c.to_dict() for c in self.candidates],
            "unknowns": self.unknowns,
            "references": self.references,
            "trace": [t.to_dict() for t in self.trace],
            "limitations": self.limitations,
            "followups": self.followups,
            "overall_tag": self.overall_tag,
            "insufficient_reason": self.insufficient_reason,
            "phenomenon": self.phenomenon,
            "phenomenon_matches": self.phenomenon_matches,
            "llm_narrative": self.llm_narrative,
        }


class DiagnosticEngine:
    """确定性诊断引擎。

    根因判定**始终**由规则层 + 工具检查决定，这是本项目的立身之本：
    结论必须能追溯到某一条规则、某一次工具调用。

    模型解读（T-08）是**可选增强**，默认关闭（`use_llm=False`）：
      - 开启后模型也只做两件事：解释已查出的结论、补充用户可执行的验证建议；
      - 它不参与候选排序与评分，**没有诊断权**；
      - 输出恒为 `[推断]`，且必须先过证据纪律守卫；
      - 不可用 / 超时 / 被守卫拒绝 → 降级，诊断结果不受影响。
    """

    def __init__(self, registry: ToolRegistry | None = None,
                 llm: "object | None" = None) -> None:
        self.rules = rules_by_id()
        self.bm25 = BM25()
        self.registry = registry or build_default_registry()
        # 模型服务**惰性创建**：构造引擎不应触发任何网络探测，
        # 否则纯规则层场景也会被模型可用性拖住。
        self.llm = llm
        # 检索工具依赖引擎自身的 BM25 索引，因此在此以闭包形式注册，
        # 但它同样走注册表调用——留痕与失败处理与其余工具完全一致。
        if not self.registry.has("search_reference"):
            self.registry.register(
                "search_reference", self.bm25.search,
                description="检索自建知识卡片（BM25），返回可追溯到章节的引用",
                summarize=lambda hits: f"命中 {len(hits)} 条",
                default_tag=TAG_STATIC,
            )

    # ------------------------------------------------------------------
    def run(self, req: DiagnosticRequest,
            on_event: "Callable[[str, dict], None] | None" = None
            ) -> DiagnosticReport:
        """执行诊断。

        `on_event` 为流式回调：每完成一步即推送结构化事件。
        SSE 端点据此推送**真实执行过程**，不由前端伪造动画。
        传 None 时行为与原先完全一致（CLI 场景）。
        """
        report = DiagnosticReport()
        seq = 0

        def emit(etype: str, data: dict) -> None:
            if on_event is not None:
                on_event(etype, data)

        def push(run: "ToolRun") -> ToolRun:
            """把一次工具调用落成 trace 步骤并推送。"""
            nonlocal seq
            step = TraceStep(run.seq, run.tool, run.status, run.reason,
                             run.output_sum, run.input_sum, run.duration_ms,
                             run.error)
            report.trace.append(step)
            emit("step_done", step.to_dict())
            return run

        def call(name: str, reason: str, input_sum: str = "", **kw):
            nonlocal seq
            run, result = self.registry.invoke(
                name, seq=seq + 1, reason=reason, input_sum=input_sum, **kw)
            seq += 1
            return push(run), result

        def skip(name: str, reason: str, input_sum: str = ""):
            nonlocal seq
            seq += 1
            return push(self.registry.skip(name, seq, reason, input_sum))

        # ---------- 材料充分性（R-X-02）----------
        if not req.has_material():
            report.status = "insufficient"
            report.insufficient_reason = (
                "未提交任何源码或现象描述，无法定位。请至少提供其中一项。"
            )
            report.candidates = [self._candidate_from_rule(
                "R-X-02", 1.0,
                [Finding("orchestrator", TAG_STATIC,
                         "提交材料为空，不足以定位问题", "R-X-02")],
                [],
            )]
            # 提前返回前必须发出 done，否则前端流不会正常收敛
            emit("done", {"status": report.status,
                          "hypothesis_count": len(report.candidates),
                          "unknown_count": 0})
            return report

        symptom, hits = detect_symptom(req.symptom_text)
        report.symptom = symptom
        report.symptom_hits = hits
        emit("symptom", {"symptom": symptom, "hits": hits})

        # ---------- ⓪ parse_serial_capture（Phase 5：唯一的 [实测-硬件] 来源）----------
        # 放在最前面，因为现象判定不依赖源码；且它是唯一能把结论从
        # "只看代码"推向"看过板子"的输入。抓帧只能由用户提供，
        # 系统不存在任何自动生成抓帧的接口。
        cap_findings: list[Finding] = []
        if req.serial_capture.strip():
            run_cap, cap = call("parse_serial_capture",
                                "解析用户上板采集的串口抓帧",
                                f"{len(req.serial_capture)} 字符",
                                raw_text=req.serial_capture)
            if run_cap.ok() and cap is not None:
                report.phenomenon = cap.to_dict()
                report.phenomenon["captured_by"] = (
                    req.serial_capture_source or "用户提交，未注明采集方式")
                cap_findings = findings_from_capture(cap)
                # 现象本身不强行造候选根因，而是作为**独立证据块**呈现：
                # "板子上看到什么"与"代码里查出什么"分开，避免二者互相冒充。
                report.phenomenon["findings"] = [f.to_dict() for f in cap_findings]
                emit("phenomenon", report.phenomenon)
                report.limitations.extend(cap.limitations)

                cap_findings = findings_from_capture(cap)
                matches = match_phenomena(cap)
                report.phenomenon_matches = [m.to_dict() for m in matches]
                if matches:
                    emit("phenomenon_matches", {"items": report.phenomenon_matches})
                    same = sum(1 for m in matches if m.consistent)
                    if same > 1:
                        report.limitations.append(
                            f"有 {same} 个案例的预期现象同为 {cap.verdict}，"
                            f"**实测现象无法单独区分它们**——这类故障必须结合"
                            f"代码检查与接线确认，不能只靠现象下结论"
                        )
            else:
                report.limitations.append(
                    f"串口抓帧解析失败（{run_cap.error}），本轮未纳入实测现象证据")
        else:
            skip("parse_serial_capture",
                 "未提供串口抓帧；本轮结论将**全部基于静态分析**")
            report.limitations.append(
                "未提供串口实测抓帧，本轮所有结论均不含硬件实测证据"
                "（证据等级最高只到 [静态分析]）"
            )
            report.followups.append(
                "采集一段真实串口抓帧（串口助手 / Web Serial 页面）并提交，"
                "即可把结论从「代码检查」推进到「实测现象与代码互证」"
            )

        # ---------- ① parse_build_log ----------
        if req.build_log.strip():
            run, build = call("parse_build_log", "提交了编译日志",
                              f"日志 {len(req.build_log)} 字符",
                              log=req.build_log)
            if run.ok() and build is not None:
                if build.error_count > 0:
                    # 编译都没过，后面的配置检查意义有限——必须显式告知
                    report.limitations.append(
                        f"编译/链接存在 {build.error_count} 个错误，程序可能未成功生成；"
                        f"以下配置检查基于源码文本，不代表实际运行的固件"
                    )
                    report.candidates.append(self._candidate_from_rule(
                        "R-X-01", 1.5,
                        [Finding("parse_build_log", TAG_STATIC,
                                 f"编译日志含 {build.error_count} 个错误，例如："
                                 f"{build.issues[0].message[:80]}", "R-X-01")],
                        [],
                    ))
                    # 分支：编译不过时，先让用户修编译错误，避免后续检查误导
                    report.followups.append(
                        "先修复编译/链接错误并重新提交完整日志——"
                        "在程序能成功生成之前，配置检查只能说明源码意图，不代表实际固件行为"
                    )
        else:
            skip("parse_build_log",
                 "未提供编译日志；若问题实为编译失败，本步无法拦截")

        # ---------- ② analyze_c_code ----------
        run_code, facts = call("analyze_c_code", "解析提交的 C 源码",
                               f"{len(req.source_code)} 字符",
                               source=req.source_code, path=req.source_path)
        if not run_code.ok():
            # 事实提取失败，后续依赖它的工具全部跳过——绝不拿空事实硬算
            report.status = "insufficient"
            report.insufficient_reason = (
                f"源码解析失败（{run_code.error}），无法提取可检查的配置事实。"
            )
            report.limitations.append("源码解析异常，本轮未执行任何配置检查")
            skip("check_uart_config", "依赖 analyze_c_code，上一步失败")
            skip("calc_baud_error", "依赖 analyze_c_code，上一步失败")
            skip("analyze_isr_body", "依赖 analyze_c_code，上一步失败")
            emit("done", {"status": report.status,
                          "hypothesis_count": len(report.candidates),
                          "unknown_count": 0})
            return report

        report.limitations.extend(facts.limitations)

        if not req.source_code.strip():
            report.status = "insufficient"
            report.insufficient_reason = (
                "未提交源码，只能依据现象描述给出通用排查方向，无法定位到具体配置。"
            )
            report.followups.extend([
                "请补充串口初始化相关源码（.c / .h 均可），"
                "这是定位配置类问题的必要材料",
                "如可补充：编译/链接日志、串口助手观察到的实际现象，"
                "可进一步缩小范围",
            ])

        # ---------- ③ check_uart_config ----------
        inst = facts.usart_inits[0].instance if facts.usart_inits else "USART1"
        run_check, checks = call("check_uart_config", "对解析结果做配置一致性检查",
                                 f"实例 {inst}",
                                 facts=facts, actual_hse_hz=req.actual_hse_hz)
        all_checks = list(checks.checks) if run_check.ok() and checks else []
        if run_check.ok() and checks is not None:
            report.limitations.extend(checks.limitations)
            # 超范围材料（如提交的是 I2C 代码）必须显式告知，不静默套用 UART 结论
            if any("超出支持范围" in m for m in checks.limitations):
                report.followups.append(
                    "当前版本仅支持 STM32F103C8T6 的 USART 故障诊断；"
                    "本次材料未检出 USART 相关配置，可能**超出支持范围**。"
                    "请确认提交的是 USART 工程代码，或补充含串口初始化的源码"
                )
        if not run_check.ok():
            report.limitations.append(
                f"配置一致性检查工具执行失败（{run_check.error}），本轮该项未纳入结论"
            )

        # ---------- ④ analyze_isr_body（Phase 4 新增）----------
        need_isr = bool(facts.usart_it) or bool(facts.isr_bodies)
        if need_isr:
            run_isr, isr = call("analyze_isr_body",
                                f"检测到串口中断配置/ISR 定义，做函数体级检查",
                                f"ISR {len(facts.isr_bodies)} 个",
                                facts=facts, instance=inst)
            if run_isr.ok() and isr is not None:
                all_checks.extend(isr.checks)
                report.limitations.extend(isr.limitations)
                if isr.checks:
                    # T-06 已给出真实判定，移除"需读函数体"的提示性 unknown
                    all_checks = [
                        c for c in all_checks
                        if not (c.rule_id in ("R-S6-03", "R-S6-04")
                                and c.status == "unknown")
                    ]
                else:
                    # 使能了中断却找不到对应 ISR 体，如实标 unknown
                    report.unknowns.append({
                        "rule_id": "R-S6-03",
                        "cause": "中断标志未正确清除",
                        "text": f"使能了串口中断但未找到 {inst}_IRQHandler 的函数体，"
                                f"无法判定中断标志是否正确清除",
                        "hint": "提供中断服务函数完整代码",
                        "tag": TAG_UNVERIFIED,
                    })
        else:
            skip("analyze_isr_body", "源码未使能串口中断且无 ISR 定义，本工具不适用")

        # ---------- ⑤ calc_baud_error ----------
        baud_findings: list[Finding] = []
        targets = find_baud_targets(facts)
        if targets:
            for t_inst, baud in targets:
                bus = USART_BUS.get(t_inst, "APB2")
                fck = req.pclk2_hz if bus == "APB2" else req.pclk1_hz
                if fck:
                    run_b, r = call("calc_baud_error",
                                    f"{t_inst} 总线时钟已知（{fck} Hz）",
                                    f"{t_inst} {baud} @ {fck}Hz",
                                    fck_hz=fck, target_baud=baud,
                                    instance=t_inst, bus=bus)
                    if run_b.ok() and r is not None and not r.within_tolerance:
                        baud_findings.append(Finding(
                            "calc_baud_error", r.evidence_tag,
                            f"{t_inst} 目标 {baud} 在 {fck} Hz 下：{r.note}"
                            f"（实际 {r.actual_baud:.1f}）",
                            "R-S2-02", "RM0008 Ch.19 USART",
                        ))
                else:
                    skip("calc_baud_error",
                         f"{t_inst} 所在 {bus} 总线时钟未知，无法计算波特率误差",
                         f"{t_inst} {baud}")
                    report.followups.append(
                        f"补充 {t_inst} 所在的 {bus} 总线时钟（PCLK2 或 PCLK1）"
                        f"或直接提供实测波特率，才能量化波特率偏差"
                    )
        else:
            skip("calc_baud_error", "源码中未提取到 USART_Init 的波特率配置")

        # ---------- ⑥ infer_actual_fck（Phase 4 新增：反向比对）----------
        if req.observed_baud and targets:
            t_inst, baud = targets[0]
            bus = USART_BUS.get(t_inst, "APB2")
            nominal = req.pclk2_hz if bus == "APB2" else req.pclk1_hz
            assumed = nominal is None
            if assumed:
                nominal = (CHIP_F103C8T6["pclk2_max_hz"] if bus == "APB2"
                           else CHIP_F103C8T6["pclk1_max_hz"])
            run_i, inf = call("infer_actual_fck",
                              "用户提供了实测波特率，做反向时钟推断",
                              f"实测 {req.observed_baud} bps / 目标 {baud}",
                              target_baud=baud, nominal_fck_hz=nominal,
                              observed_baud=req.observed_baud,
                              instance=t_inst, bus=bus)
            if run_i.ok() and inf is not None:
                if assumed:
                    report.limitations.append(
                        f"未提供 {bus} 总线时钟，反向推断按工程常见值 "
                        f"{nominal} Hz 作为假定基准；该假定未经验证，"
                        f"推断出的实际时钟值随之存在偏差"
                    )
                baud_findings.append(Finding(
                    "infer_actual_fck", inf.evidence_tag,
                    f"实测 {req.observed_baud:.0f} bps（来源："
                    f"{req.observed_baud_source or '用户提交，未注明来源'}）："
                    f"{inf.note}",
                    "R-S2-01", "RM0008 Ch.19 USART（波特率与总线时钟关系）",
                ))
                report.limitations.extend(inf.limitations)
                # 把实测观测本身单独留一条 [实测-硬件] 证据，与推断结论分开
                if abs(inf.ratio - 1.0) > 0.01:
                    report.candidates.append(self._candidate_from_rule(
                        "R-S2-01", 1.2,
                        [Finding("user_input", EvidenceTag.MEASURED_HW.value,
                                 f"用户实测波特率为 {req.observed_baud:.0f} bps"
                                 f"（{req.observed_baud_source or '未注明来源'}）",
                                 "R-S2-01"),
                         Finding("infer_actual_fck", inf.evidence_tag, inf.note,
                                 "R-S2-01")],
                        [],
                    ))
        elif req.observed_baud:
            skip("infer_actual_fck",
                 "有实测波特率但源码中未提取到目标波特率，无法做反向比对",
                 f"实测 {req.observed_baud} bps")

        # ---------- 组装候选 ----------
        for c in [x for x in all_checks if x.status == "violated"]:
            rule = self.rules.get(c.rule_id)
            score = 1.0
            # 症状匹配加权：用户报的现象与本条规则的症状一致
            if symptom and rule and rule.symptom and symptom in rule.symptom:
                score += 0.5
            # 源码外证据补充
            extra = [f for f in baud_findings if f.rule_id == c.rule_id]
            score += 0.3 * len(extra)
            supporting = [
                Finding("check_uart_config", c.tag, c.text, c.rule_id, c.ref)
                if c.rule_id not in ("R-S6-03", "R-S6-04")
                else Finding("analyze_isr_body", c.tag, c.text, c.rule_id, c.ref)
            ] + extra
            if not any(x.rule_id == c.rule_id for x in report.candidates):
                report.candidates.append(self._candidate_from_rule(
                    c.rule_id, score, supporting, [],
                ))

        # 波特率类发现若未被 check 覆盖，也单独成候选
        for f in baud_findings:
            if not any(c.rule_id == f.rule_id for c in report.candidates):
                report.candidates.append(self._candidate_from_rule(
                    f.rule_id, 0.9, [f], [],
                ))

        emit("hypothesis_update", {
            "hypotheses": [c.to_dict() for c in report.candidates],
        })

        # ---------- unknowns：知道自己不知道 ----------
        for c in all_checks:
            if c.status != "unknown":
                continue
            if any(u.get("rule_id") == c.rule_id for u in report.unknowns):
                continue
            rule = self.rules.get(c.rule_id)
            report.unknowns.append({
                "rule_id": c.rule_id,
                "cause": rule.candidate_cause if rule else "",
                "text": c.text,
                "hint": c.hint or "",
                "tag": c.tag,
            })

        # ---------- ⑦ search_reference ----------
        query_parts = [req.symptom_text or ""]
        for c in report.candidates[:3]:
            rule = self.rules.get(c.rule_id)
            if rule:
                query_parts.append(rule.candidate_cause)
        query = " ".join(p for p in query_parts if p)
        if query.strip():
            run_r, hits_bm25 = call("search_reference", "检索自建知识卡片",
                                    query[:60], query=query, top_k=5)
            if run_r.ok() and hits_bm25:
                report.references = [h.to_dict() for h in hits_bm25]
                emit("retrieval_done", {"hits": report.references})
        else:
            skip("search_reference", "无有效查询串")

        # ---------- followups：下一步需要什么 ----------
        if symptom == "S2" and not any(
                f.rule_id in ("R-S2-01", "R-S2-02") for f in baud_findings):
            report.followups.append(
                "现象为乱码：请提供实测波特率（串口助手实测或示波器位宽）"
                "与实际总线时钟，以便做反向比对定位时钟来源"
            )
        if report.unknowns and not report.followups:
            report.followups.append(
                "仍有无法静态判定的项，按右侧「需补充信息」逐条提供后可再次诊断"
            )
        if report.followups:
            emit("followups", {"items": report.followups})

        # ---------- 现象驱动的候选重排（Phase 5）----------
        # 依据：实测现象与案例预期是否一致。注意这是 [推断] 层面的加权——
        # 预期现象表本身未实测，"一致"只表示**未被证伪**，不是证实。
        if report.phenomenon_matches:
            by_rule = {m["rule_id"]: m for m in report.phenomenon_matches}
            for c in report.candidates:
                m = by_rule.get(c.rule_id)
                if not m:
                    continue
                if m["consistent"]:
                    c.score += 0.8
                    c.supporting.append(Finding(
                        "parse_serial_capture", TAG_INFERRED,
                        f"实测串口现象（{m['actual_tx']}）与该案例预期一致，"
                        f"未被证伪（一致 ≠ 已证实）",
                        m["rule_id"], "docs/corpus/phenomena_map.json"))
                else:
                    c.score = max(round(c.score - 0.4, 3), 0.1)
                    c.refuting.append(Finding(
                        "parse_serial_capture", TAG_INFERRED,
                        f"实测串口现象（{m['actual_tx']}）与该案例预期"
                        f"（{m['expected_tx']}）不符，该候选与实测现象存在矛盾，需复核",
                        m["rule_id"], "docs/corpus/phenomena_map.json"))

        # 无任何 violated 但实测现象异常 → 明确告知"代码没查出问题，但板子确实不对"
        if (report.phenomenon and report.phenomenon.get("verdict") == EMPTY
                and not any(c.rule_id != "R-X-02" for c in report.candidates)):
            report.followups.append(
                "代码未检出配置违规，但实测串口无任何输出——"
                "请依次排查：串口助手是否选对 COM 口、TX/RX 是否交叉接线、"
                "USB-TTL 与开发板是否共地、MCU 供电是否正常"
            )

        report.candidates.sort(key=lambda c: c.score, reverse=True)
        for i, c in enumerate(report.candidates):
            c.rank = i + 1

        # ---------- ⑧ llm_assist（模型解读，可选）----------
        # 三条纪律（对应 AGENTS.md 第四节与解禁批次⑦）：
        #   1. 模型**没有诊断权**——只解释已查出的结论、补充用户可执行的验证建议，
        #      不新增候选根因、不改评分，因此放在排序**之后**执行。
        #   2. 输出恒为 [推断]，且必须先过证据纪律守卫。
        #   3. 不可用/超时/被守卫拒绝 → 降级为规则层结论，绝不影响诊断本身。
        if req.use_llm:
            from .registry import register_llm_tool   # 局部导入：默认路径不加载模型层
            if self.llm is None:
                from ..llm.service import LlmService
                self.llm = LlmService()
            register_llm_tool(self.registry, self.llm)
            cand_dicts = [{
                "rule_id": c.rule_id,
                "cause": c.cause,
                "score": round(c.score, 3),
                "evidences": [
                    {"item": f.text, "tag": f.tag, "detail": f.ref or ""}
                    for f in c.supporting[:3]
                ],
            } for c in report.candidates[:3]]
            run_l, nar = call(
                "llm_assist", "用本地模型解释已查出的结论并补充验证建议",
                f"候选 {len(cand_dicts)} 个 / 无法判定 {len(report.unknowns)} 项",
                symptom_text=req.symptom_text,
                candidates=cand_dicts,
                unknowns=report.unknowns,
                references=report.references,
                service=self.llm,
            )
            if run_l.ok() and nar is not None:
                report.llm_narrative = nar.to_dict()
                emit("llm_narrative", report.llm_narrative)
                if nar.degraded:
                    # 被守卫拒绝也算"调用成功但输出不可用"——必须显式告知用户，
                    # 否则用户会以为这一段是系统结论。
                    report.limitations.append(
                        f"模型解读未采用：{nar.reason}"
                        f"（本轮结论全部来自规则层静态检查）"
                    )
            else:
                report.limitations.append(
                    f"模型解读步骤未产出可用输出（{run_l.error}），"
                    f"本轮结论仍为规则层静态检查"
                )
        else:
            skip("llm_assist",
                 "未启用模型解读（use_llm=False）；本轮为纯规则层诊断")

        emit("done", {"status": report.status,
                      "hypothesis_count": len(report.candidates),
                      "unknown_count": len(report.unknowns)})
        return report

    # ------------------------------------------------------------------
    def _candidate_from_rule(
        self,
        rule_id: str,
        score: float,
        supporting: list[Finding],
        refuting: list[Finding],
    ) -> Candidate:
        rule = self.rules.get(rule_id)
        if rule is None:
            return Candidate(rule_id=rule_id, cause="（规则库中无此条目）",
                             score=score, supporting=supporting,
                             refuting=refuting, verification_steps=[])
        return Candidate(
            rule_id=rule.rule_id,
            cause=rule.candidate_cause,
            score=score,
            supporting=supporting,
            refuting=refuting,
            verification_steps=list(rule.verification_steps),
            ref=rule.source_ref,
            linked_case=rule.linked_case,
        )
