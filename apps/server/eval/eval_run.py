"""Phase 6 评测执行器。

跑法（不依赖网络、不依赖模型、不依赖数据库）：

    C:/Users/Zero/.workbuddy/binaries/python/envs/default/Scripts/python.exe eval/eval_run.py

做的事：
1. 逐用例构造 `DiagnosticRequest`，调用**与线上完全相同的** `DiagnosticEngine`。
2. 每用例重复 `REPEAT` 次（指标文档要求 ≥3 次，用于观察输出稳定性）。
3. 用 `metrics.py` 计算指标，逐条判定，失败原因**原样输出**。
4. 结果写入 `eval/results/eval_<时间戳>.json`，并打印控制台摘要。

红线：
- 不修改评测标准来让用例通过；失败就是失败，写进报告。
- 输出的是**原始计数**（命中 X/6），不是百分比宣传口径。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

from app.engine.orchestrator import DiagnosticEngine, DiagnosticRequest  # noqa: E402
from eval.evalset import CASES                                           # noqa: E402
from eval.metrics import assess, case_passed                             # noqa: E402

REPEAT = 3
SAMPLES = SERVER_ROOT / "samples"
RESULTS = Path(__file__).resolve().parent / "results"


def build_source(case) -> str:
    parts: list[str] = []
    for name in case.sample_files:
        p = SAMPLES / name
        if not p.exists():
            raise FileNotFoundError(f"样例缺失：{p}")
        parts.append(p.read_text(encoding="utf-8"))
    if case.inline_source:
        parts.append(case.inline_source)
    return "\n".join(parts)


def run_case(eng: DiagnosticEngine, case, repeat: int = REPEAT) -> dict:
    source = build_source(case)
    runs: list[dict] = []
    top1_seq: list[str | None] = []
    for i in range(repeat):
        req = DiagnosticRequest(
            source_code=source,
            source_path=case.source_path,
            symptom_text=case.symptom_text,
            build_log=case.build_log,
            actual_hse_hz=case.actual_hse_hz,
            pclk2_hz=case.pclk2_hz,
            pclk1_hz=case.pclk1_hz,
        )
        t0 = time.perf_counter()
        rep = eng.run(req)
        dur_ms = int((time.perf_counter() - t0) * 1000)
        r = assess(rep, case, source)
        r["duration_ms"] = dur_ms
        r["run_index"] = i
        runs.append(r)
        top1_seq.append(r.get("top1"))

    passed, reasons = case_passed(runs[0], case)
    stable = len(set(top1_seq)) == 1
    return {
        "case_id": case.case_id,
        "title": case.title,
        "kind": case.kind,
        "passed": passed,
        "reasons": reasons,
        "stable": stable,
        "top1_seq": top1_seq,
        "runs": runs,
        "hardware_plan": case.hardware_plan,
        "trap": case.trap,
    }


def main() -> int:
    eng = DiagnosticEngine()
    print("=" * 72)
    print("Phase 6 固定评测集执行（证据等级：[静态分析]，不含任何硬件实测）")
    print(f"用例 {len(CASES)} 个 × 每例 {REPEAT} 次")
    print("=" * 72)

    results = [run_case(eng, c) for c in CASES]

    pos = [r for r in results if r["kind"] == "positive"]
    neg = [r for r in results if r["kind"] == "negative"]
    pos_pass = sum(1 for r in pos if r["passed"])
    neg_pass = sum(1 for r in neg if r["passed"])

    print()
    for r in results:
        flag = "PASS" if r["passed"] else "FAIL"
        print(f"[{flag}] {r['case_id']} {r['title']}")
        a = r["runs"][0]
        print(f"       Top-1={a.get('top1')}  候选={a.get('rule_ids')}")
        print(f"       证据 {a['evidence_total']} 条：有据 {a['evidence_grounded']}"
              f" / 待复核 {a['evidence_unverifiable']}"
              f" / 与材料矛盾 {a['evidence_contradiction']}")
        print(f"       工具调用 {a['tool_called']}/{a['tool_calls']}"
              f"  耗时 {a['duration_ms']}ms  稳定 {r['stable']}")
        if not r["passed"]:
            for reason in r["reasons"]:
                print(f"       ✗ {reason}")
        print()

    # ---- 汇总：只给原始计数，不给宣传口径百分比 ----
    tot_ev = sum(r["runs"][0]["evidence_total"] for r in results)
    tot_ground = sum(r["runs"][0]["evidence_grounded"] for r in results)
    tot_unver = sum(r["runs"][0]["evidence_unverifiable"] for r in results)
    tot_contra = sum(r["runs"][0]["evidence_contradiction"] for r in results)
    tot_call = sum(r["runs"][0]["tool_called"] for r in results)
    tot_calls = sum(r["runs"][0]["tool_calls"] for r in results)
    tot_trace_ok = sum(1 for r in results if r["runs"][0]["trace_complete"])
    tot_stable = sum(1 for r in results if r["stable"])
    tot_traceable_ok = sum(r["runs"][0]["traceable_ok"] for r in results)

    print("=" * 72)
    print(f"正例通过 {pos_pass}/{len(pos)}    负例通过 {neg_pass}/{len(neg)}")
    print(f"证据合计 {tot_ev} 条：有据 {tot_ground} / 待人工复核 {tot_unver}"
          f" / 与材料矛盾 {tot_contra}")
    print(f"证据可追溯 {tot_traceable_ok}/{tot_ev}")
    print(f"工具调用成功 {tot_call}/{tot_calls}")
    print(f"trace 完整 {tot_trace_ok}/{len(results)}")
    print(f"多次运行 Top-1 一致 {tot_stable}/{len(results)}")
    print("=" * 72)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS / f"eval_{stamp}.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repeat": REPEAT,
        "evidence_level": "STATIC",
        "summary": {
            "positive_total": len(pos), "positive_pass": pos_pass,
            "negative_total": len(neg), "negative_pass": neg_pass,
            "evidence_total": tot_ev, "evidence_grounded": tot_ground,
            "evidence_unverifiable": tot_unver,
            "evidence_contradiction": tot_contra,
            "traceable_ok": tot_traceable_ok,
            "tool_called": tot_call, "tool_calls": tot_calls,
            "trace_complete": tot_trace_ok, "stable": tot_stable,
        },
        "cases": results,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"结果已写入：{out}")
    return 0 if (pos_pass == len(pos) and neg_pass == len(neg)) else 1


if __name__ == "__main__":
    sys.exit(main())
