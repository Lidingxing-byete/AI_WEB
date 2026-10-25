"""工具层回归测试（Phase 4）。

验证 PROJECT_PLAN Phase 4 验收项中的三条：
1. 工具调用可重复测试     —— 每个工具脱离编排层单独断言
2. Agent 不会把未执行的操作说成已完成 —— 工具失败必须留痕且不伪造结果
3. 错误工具结果不会直接被包装成确定结论 —— error 状态不得产出 violated 结论

运行：
    C:/Users/Zero/.workbuddy/binaries/python/envs/default/Scripts/python.exe tools_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine.baud import calc_baud_error, infer_actual_fck          # noqa: E402
from app.engine.cparse import analyze_c_code                           # noqa: E402
from app.engine.isr_check import analyze_isr_body                      # noqa: E402
from app.engine.registry import ToolRegistry, build_default_registry   # noqa: E402
from app.engine.uart_check import check_uart_config                    # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  -- {detail}" if detail else ""))


# ---------------------------------------------------------------- 测试素材

ISR_GOOD = r"""
#include "stm32f10x.h"

void USART1_IRQHandler(void)
{
    if (USART_GetITStatus(USART1, USART_IT_RXNE) == SET)
    {
        uint8_t d = USART_ReceiveData(USART1);
        USART_SendData(USART1, d);
    }
    if (USART_GetFlagStatus(USART1, USART_FLAG_ORE) == SET)
    {
        USART_ReceiveData(USART1);
    }
}
"""

ISR_BAD = r"""
#include "stm32f10x.h"

void USART1_IRQHandler(void)
{
    USART_SendData(USART1, 0x41);
}
"""

ISR_WRITE_SR = r"""
#include "stm32f10x.h"

void USART1_IRQHandler(void)
{
    USART1->SR = 0;
}
"""


# ---------------------------------------------------------------- 1. 注册表
print("\n=== 1. 工具注册表 ===")
reg = build_default_registry()
names = reg.names()
check("注册表含 7 个内置工具", len(names) == 7, str(names))
check("工具均有描述", all(d["description"] for d in reg.describe()))

# 工具抛异常 -> status=error，返回 None，不向上抛
def _boom(**kw):
    raise RuntimeError("工具内部炸了")


reg2 = ToolRegistry()
reg2.register("boom", _boom, description="故意失败的工具")
run, res = reg2.invoke("boom", seq=1, reason="测试异常路径", x=1)
check("工具异常被捕获且不外抛", run.status == "error" and res is None, run.error or "")
check("异常原因被记录", run.error is not None and "RuntimeError" in run.error)

run_skip = reg2.skip("boom", seq=2, reason="条件不足")
check("跳过的状态是 skipped 而非 called", run_skip.status == "skipped")
check("跳过必须写明原因", bool(run_skip.reason))


# ---------------------------------------------------------------- 2. ISR 解析
print("\n=== 2. ISR 函数体提取与分析（工具 T-06）===")
facts_good = analyze_c_code(ISR_GOOD, "isr_good.c")
check("提取到 ISR 函数体", len(facts_good.isr_bodies) == 1,
      str([b.name for b in facts_good.isr_bodies]))
if facts_good.isr_bodies:
    body = facts_good.isr_bodies[0].body
    check("函数体包含 USART_ReceiveData", "USART_ReceiveData" in body)

r_good = analyze_isr_body(facts_good, "USART1")
c603 = next((c for c in r_good.checks if c.rule_id == "R-S6-03"), None)
c604 = next((c for c in r_good.checks if c.rule_id == "R-S6-04"), None)
check("R-S6-03 正确实现判为 ok", c603 is not None and c603.status == "ok",
      c603.text[:40] if c603 else "无")
check("R-S6-04 处理 ORE 判为 ok", c604 is not None and c604.status == "ok")

facts_bad = analyze_c_code(ISR_BAD, "isr_bad.c")
r_bad = analyze_isr_body(facts_bad, "USART1")
c603b = next((c for c in r_bad.checks if c.rule_id == "R-S6-03"), None)
c604b = next((c for c in r_bad.checks if c.rule_id == "R-S6-04"), None)
check("R-S6-03 未读 DR 判为 violated",
      c603b is not None and c603b.status == "violated")
check("R-S6-04 未处理 ORE 判为 unknown（不冒充确定结论）",
      c604b is not None and c604b.status == "unknown")

facts_sr = analyze_c_code(ISR_WRITE_SR, "isr_sr.c")
r_sr = analyze_isr_body(facts_sr, "USART1")
c603s = next((c for c in r_sr.checks if c.rule_id == "R-S6-03"), None)
check("R-S6-03 直接写 SR 判为 violated",
      c603s is not None and c603s.status == "violated")

# 不含任何 ISR 的源码：工具应明确"不适用"，checks 为空而非伪造 ok
facts_none = analyze_c_code("int main(void){return 0;}", "none.c")
r_none = analyze_isr_body(facts_none, "USART1")
check("无 ISR 时产出空检查且声明不适用",
      r_none.checks == [] and bool(r_none.limitations))

# ---- 场景前置判定（Phase 6 新增）----
# 材料中没有任何 USART 证据时，不得按"未找到使能"判违规。
# 这是 N-01（空材料）与 N-03（提交 I2C 代码）抓出的真实缺陷。
rep_scope = check_uart_config(facts_bad, actual_hse_hz=None)
check("无 USART 证据时 UART 规则一律 unknown（没看到 ≠ 有错误）",
      bool(rep_scope.checks)
      and all(c.status == "unknown" for c in rep_scope.checks)
      and any("超出支持范围" in m for m in rep_scope.limitations))

# 下面这条断言的前提是"材料属于 UART 场景"，故先补上 USART 证据
# （使能 RXNE 中断），再验证 R-S6-03 不再由本工具硬编码 unknown。
facts_bad.usart_it.append(("USART1", "RXNE"))
rep_plain = check_uart_config(facts_bad, actual_hse_hz=None)
has_603_unknown = any(c.rule_id == "R-S6-03" for c in rep_plain.checks)
check("有 USART 证据时 check_uart_config 不再硬编码 R-S6-03 unknown",
      not has_603_unknown)


# ---------------------------------------------------------------- 3. 波特率
print("\n=== 3. 波特率正/反向计算（工具 T-04 / T-04r）===")
b = calc_baud_error(72_000_000, 115200, "USART1", "APB2")
# 语料 card-03 记 0x271，工具按 16 位寄存器补零输出 0x0271，二者同值
check("72MHz/115200 → BRR=0x271", int(b.brr_hex, 16) == 0x271, b.brr_hex)
check("72MHz/115200 误差在容差内", b.within_tolerance, f"{b.error_pct:.4f}%")

b2 = calc_baud_error(72_000_000, 9600, "USART1", "APB2")
check("72MHz/9600 → BRR=0x1D4C", b2.brr_hex == "0x1D4C", b2.brr_hex)

# 反向：固件按 72MHz 配 BRR，实际跑在 8MHz -> 实测波特率应为目标的 1/9
observed = 115200 * 8_000_000 / 72_000_000
inf = infer_actual_fck(115200, 72_000_000, observed, "USART1", "APB2")
implied_ok = abs(inf.implied_fck_hz - 8_000_000) < 1000
check("反向推断还原出实际 8MHz 总线时钟", implied_ok,
      f"implied={inf.implied_fck_hz:,.0f} Hz, ratio={inf.ratio:.4f}")
check("反向推断结论标 [推断] 而非实测", inf.evidence_tag == "[推断]", inf.evidence_tag)
check("反向推断列出时钟树候选", any("8MHz" in c for c in inf.tree_candidates),
      str(inf.tree_candidates))
check("反向推断声明局限性", len(inf.limitations) >= 2)

# 名义=实际时不该乱报
inf2 = infer_actual_fck(115200, 72_000_000, 115200, "USART1", "APB2")
check("名义与实测一致时 ratio≈1", abs(inf2.ratio - 1.0) < 0.01, f"{inf2.ratio:.4f}")

try:
    infer_actual_fck(0, 72_000_000, 115200)
    check("非法入参应抛错", False)
except ValueError:
    check("非法入参应抛错", True)


# ---------------------------------------------------------------- 4. 编排层
print("\n=== 4. 编排层多步诊断 ===")
from app.engine.orchestrator import DiagnosticEngine, DiagnosticRequest  # noqa: E402

SAMPLES = Path(__file__).resolve().parent / "samples"
eng = DiagnosticEngine()

src_c1 = (SAMPLES / "c01_no_usart1_clock.c").read_text(encoding="utf-8")
rep1 = eng.run(DiagnosticRequest(
    source_code=src_c1, symptom_text="串口完全没有输出，但板载LED还在闪烁"))
check("C1 检出 R-S1-01", any(c.rule_id == "R-S1-01" for c in rep1.candidates),
      str([c.rule_id for c in rep1.candidates]))
check("trace 至少 4 步", len(rep1.trace) >= 4,
      str([(t.seq, t.tool, t.status) for t in rep1.trace]))
check("每一步都写明原因", all(t.reason for t in rep1.trace))
check("本轮无工具执行失败", all(t.status != "error" for t in rep1.trace))
check("跳过步骤标 skipped 而非 called",
      all(t.status == "skipped" for t in rep1.trace if t.tool == "parse_build_log"))

# 触发反向推断：用户给出实测波特率
rep2 = eng.run(DiagnosticRequest(
    source_code=src_c1, symptom_text="串口输出乱码",
    pclk2_hz=72_000_000, observed_baud=12800.0,
    observed_baud_source="串口助手实测"))
called_tools = [t.tool for t in rep2.trace if t.status == "called"]
check("触发反向推断工具", "infer_actual_fck" in called_tools, str(called_tools))
check("反向推断产出 [推断] 证据",
      any(f.tag == "[推断]" for c in rep2.candidates for f in c.supporting))
check("用户实测观测本身标 [实测-硬件]",
      any(f.tag == "[实测-硬件]" for c in rep2.candidates for f in c.supporting))

# 正确版不得误报
src_ok = (SAMPLES / "c01_fixed.c").read_text(encoding="utf-8")
rep3 = eng.run(DiagnosticRequest(source_code=src_ok, symptom_text="想确认配置有无问题"))
bad = [c.rule_id for c in rep3.candidates if c.rule_id == "R-S1-01"]
check("正确版不误报 R-S1-01", not bad, str(bad))

# 空材料
rep4 = eng.run(DiagnosticRequest())
check("空材料判 insufficient", rep4.status == "insufficient")

# 工具失败不得掀翻整轮
broken = ToolRegistry()
eng_broken = DiagnosticEngine(registry=broken)
rep5 = eng_broken.run(DiagnosticRequest(source_code=src_c1, symptom_text="无输出"))
err_steps = [t for t in rep5.trace if t.status == "error"]
check("未注册工具记为 error 而非崩溃", len(err_steps) > 0, str(len(err_steps)))
check("工具全失败时不产出候选根因", rep5.candidates == [])


# ---------------------------------------------------------------- T-07
from app.engine.serialcap import (EMPTY, GARBLED, NORMAL,
                                  findings_from_capture, match_phenomena,
                                  parse_serial_capture)

cap_empty = ""
f_empty = parse_serial_capture(cap_empty)
check("空抓帧判 EMPTY", f_empty.verdict == EMPTY, f_empty.verdict)

cap_ok = "\r\n".join([f"tick {i}: hello STM32\r" for i in range(20)])
f_ok = parse_serial_capture(cap_ok)
check("正常文本判 NORMAL", f_ok.verdict == NORMAL,
      f"{f_ok.verdict} / {f_ok.nonprint_ratio:.2f}")
check("正常文本统计出行数", f_ok.lines == 20, str(f_ok.lines))

cap_bad = "\r\n".join(["\ufffd\ufffd\u00ff\u00fe\u0001\u0002" for _ in range(20)])
f_bad = parse_serial_capture(cap_bad)
check("乱码文本判 GARBLED", f_bad.verdict == GARBLED,
      f"{f_bad.verdict} / {f_bad.nonprint_ratio:.2f}")

f_tiny = parse_serial_capture("ok")
check("过小样本判 UNKNOWN 而非硬判",
      f_tiny.verdict == "UNKNOWN", f_tiny.verdict)

# 标签纪律：判读 [实测-硬件]，成因方向 [推断]
fs_bad = findings_from_capture(f_bad)
check("抓帧判读标 [实测-硬件]",
      any(f.tag == "[实测-硬件]" for f in fs_bad))
check("成因方向标 [推断]（不得冒充实测）",
      any(f.tag == "[推断]" for f in fs_bad))

# 现象 ↔ 预期比对：C2 预期 GARBLED；C1 预期 EMPTY
ms = match_phenomena(f_bad)
c2 = next((m for m in ms if m.case_id == "C2"), None)
c1 = next((m for m in ms if m.case_id == "C1"), None)
check("乱码现象与 C2 预期一致", c2 is not None and c2.consistent)
check("乱码现象与 C1 预期不一致（可作排除依据）",
      c1 is not None and not c1.consistent)
check("比对结论一律标 [推断]（不得因实测而升为实测）",
      all(m.evidence_tag == "[推断]" for m in ms))

# 编排层：抓帧参与诊断并影响排序
rep6 = eng.run(DiagnosticRequest(
    source_code=src_c1, symptom_text="串口没有任何输出",
    pclk2_hz=72_000_000, serial_capture="", serial_capture_source=""))
check("未提供抓帧时工具记 skipped",
      any(t.tool == "parse_serial_capture" and t.status == "skipped"
          for t in rep6.trace))
check("未提供抓帧时明确声明无实测证据",
      any("静态分析" in l for l in rep6.limitations))

rep7 = eng.run(DiagnosticRequest(
    source_code=src_c1, symptom_text="串口没有任何输出",
    pclk2_hz=72_000_000,
    serial_capture="", serial_capture_source="unused"))
rep8 = eng.run(DiagnosticRequest(
    source_code=src_c1, symptom_text="串口没有任何输出",
    pclk2_hz=72_000_000,
    serial_capture=cap_ok, serial_capture_source="Zero 采集（样例 c01）"))
c1_score = next((c.score for c in rep7.candidates if c.rule_id == "R-S1-01"), None)
c1_score8 = next((c.score for c in rep8.candidates if c.rule_id == "R-S1-01"), None)
check("实测现象参与候选重排（NORMAL 与 C1 预期 EMPTY 不符 → 降权）",
      c1_score is not None and c1_score8 is not None and c1_score8 < c1_score,
      f"{c1_score} -> {c1_score8}")
check("实测现象进入报告", rep8.phenomenon is not None
      and rep8.phenomenon.get("verdict") == NORMAL)
check("比对结果进入报告", len(rep8.phenomenon_matches) > 0)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 60)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：" + ", ".join(FAIL))
    sys.exit(1)
print("全部通过")
