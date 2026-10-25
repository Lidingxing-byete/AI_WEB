"""故障样例自检 —— 验证「注入的故障」与「检出的规则」一一对应。

为什么必须有这个测试：
样例是本项目评测与演示的**基准**。如果某个样例根本触发不了它声称的规则，
那么基于它做出的演示、评测和结论全部失效。因此每个样例都要跑一遍：
    - 故障版：必须命中 `expect_rules` 中的每一条
    - 对照版：必须**不命中**同案例故障版的规则（否则就是误报）

证据等级声明：本测试全部为 `[静态分析]`——它只证明"引擎能按规则检出注入的
配置缺陷"，**不证明**该缺陷在真实板卡上会产生预期现象。
现象层面的验证属 Phase 5，须由用户上板实测后回填。

运行：
    C:/Users/Zero/.workbuddy/binaries/python/envs/default/Scripts/python.exe samples_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine.orchestrator import DiagnosticEngine, DiagnosticRequest  # noqa: E402
from app.main import SAMPLES_META                                        # noqa: E402

SAMPLES = Path(__file__).resolve().parent / "samples"

# 个别案例的判定需要用户补充的信息（此处为测试输入，非实测数据）
EXTRA_INPUT = {
    "c02_hse_mismatch": {"actual_hse_hz": 8_000_000,
                         "symptom_text": "串口输出全是乱码"},
    "c02_fixed": {"actual_hse_hz": 8_000_000,
                  "symptom_text": "想确认波特率相关配置"},
}

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def rules_of(rep) -> list[str]:
    return [c.rule_id for c in rep.candidates]


def main() -> int:
    eng = DiagnosticEngine()
    print("=" * 68)
    print("故障样例自检（静态分析层级，不代表硬件实测）")
    print("=" * 68)

    for sid, meta in SAMPLES_META.items():
        path = SAMPLES / f"{sid}.c"
        if not path.exists():
            check(f"[{sid}] 样例文件存在", False, str(path))
            continue
        src = path.read_text(encoding="utf-8")
        extra = EXTRA_INPUT.get(sid, {})
        req = DiagnosticRequest(
            source_code=src,
            symptom_text=extra.get("symptom_text", meta.get("symptom", "")),
            pclk2_hz=72_000_000,
            actual_hse_hz=extra.get("actual_hse_hz"),
        )
        rep = eng.run(req)
        got = rules_of(rep)
        expect = meta.get("expect_rules", [])

        if meta.get("variant") == "fault":
            missing = [r for r in expect if r not in got]
            check(f"[{sid}] 命中预期规则 {expect}", not missing,
                  f"实际 {got}" + (f"；缺失 {missing}" if missing else ""))
        else:
            leaked = [r for r in expect if r in got] if expect else []
            # 对照版断言：不得命中同案例故障版的规则
            case = meta.get("case", "")
            fault_rules: list[str] = []
            for k, m in SAMPLES_META.items():
                if m.get("case") == case and m.get("variant") == "fault":
                    fault_rules = m.get("expect_rules", [])
            leaked = [r for r in fault_rules if r in got]
            check(f"[{sid}] 对照版不误报 {fault_rules}", not leaked,
                  f"实际 {got}" + (f"；误报 {leaked}" if leaked else ""))

        # 无论哪一版，都不允许出现工具执行失败
        errs = [t.tool for t in rep.trace if t.status == "error"]
        check(f"[{sid}] 无工具执行失败", not errs, str(errs))

    print("=" * 68)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
