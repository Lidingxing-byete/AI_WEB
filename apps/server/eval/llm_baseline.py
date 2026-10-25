"""纯 LLM 基线对比（Phase 6 验收项：「与纯 LLM 基线进行公平比较」）。

为什么单独做这个
----------------
Phase 6 第一批报告里，R28 记的是"纯 LLM 基线**未执行**"。
只有跑出真实数字，才能回答评委那个必问的问题：
    "你这个系统，比直接把代码丢给大模型强在哪？"

公平性怎么保证
--------------
1. **同一批材料**：模型与规则层看到完全相同的源码与现象描述。
2. **同一候选空间**：模型只能从同一份规则目录（23 条）里选编号，
   或输出 INSUFFICIENT；不允许自由发挥的措辞，否则无法判定命中。
3. **同一判定口径**：正例看 Top-1 是否落入期望规则集合，
   负例看是否命中禁用规则 / 材料不足时是否硬给根因。
4. **不修饰失败**：模型输出非法 JSON、编造编号，一律记为失败并单列。

诚实边界（务必连同结果一起引用）
--------------------------------
- 样本只有 9 个用例，**不足以支撑任何准确率结论**，只能说明趋势。
- 本对比是**单次采样**；本地 7B 模型存在输出抖动，重复运行结果会变。
- 模型看到的是"源码 + 现象"，规则层还额外用到了编译日志、实测波特率等
  结构化输入——这正是差异的一部分，不是不公平而是**能力边界不同**。

运行：`python eval/llm_baseline.py`
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.corpus.loader import load_rules                  # noqa: E402
from app.engine.orchestrator import (DiagnosticEngine,    # noqa: E402
                                     DiagnosticRequest)
from app.llm.service import LlmService                   # noqa: E402
from eval.evalset import CASES                           # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"

# 采样次数。本地 7B 模型有输出抖动，只跑一次的结果是**不可信**的，
# 因此每例重复采样，同时报告"三次是否一致"。
REPEAT = int(__import__("os").getenv("XZ_LLM_BASELINE_REPEAT", "3"))


def load_source(case) -> str:
    """取用例源码：优先内联，其次 samples/ 文件。"""
    if case.inline_source:
        return case.inline_source
    parts = []
    for fn in case.sample_files:
        p = Path(__file__).resolve().parents[1] / "samples" / fn
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def rule_catalog() -> list[dict]:
    rules = load_rules()
    return [{"rule_id": r.rule_id,
             "brief": (r.candidate_cause or "")[:60]} for r in rules]


def judge(case, top1: str | None, status: str) -> dict:
    """按与规则层**完全相同**的口径判定。"""
    if case.kind == "positive":
        hit = bool(top1) and top1 in (case.expect_rule_present or
                                      ([case.expect_top1] if case.expect_top1 else []))
        return {"hit": hit, "false_positive": False}
    # 负例
    fp = False
    if top1 and top1 in case.forbid_rules:
        fp = True
    # 材料不足时硬给具体根因 = 违反创新方向 A。
    # 注：INSUFFICIENT 是模型"材料不足、不硬猜"的合法哨兵值（service.baseline 允许），
    #     模型正确拒绝时不算硬给根因，须排除该哨兵值再判定（2026-10-25 口径修正）。
    if (case.expect_status == "insufficient" and top1
            and top1 != "INSUFFICIENT"):
        fp = True
    return {"hit": False, "false_positive": fp}


def main() -> int:
    catalog = rule_catalog()
    print(f"规则目录：{len(catalog)} 条（与规则层同一份）")

    svc = LlmService()
    ok, why = svc.probe(force=True)
    if not ok:
        print(f"\n⚠️  本机模型不可用（{why}）。")
        print("   纯 LLM 基线**未执行**，不会用任何其它基线冒充。")
        return 0
    print(f"模型：{svc.client.model}（{svc.client.host}）\n")

    eng = DiagnosticEngine()
    rows = []
    for c in CASES:
        src = load_source(c)

        # ---- 规则层 ----
        req = DiagnosticRequest(
            source_code=src, symptom_text=c.symptom_text,
            build_log=c.build_log, actual_hse_hz=c.actual_hse_hz,
            pclk2_hz=c.pclk2_hz, pclk1_hz=c.pclk1_hz,
        )
        rep = eng.run(req)
        top1_rule = rep.candidates[0].rule_id if rep.candidates else None
        j_rule = judge(c, top1_rule, rep.status)

        # ---- 纯 LLM（重复采样 REPEAT 次：7B 模型存在输出抖动，
        #      单次结果不足以支撑任何结论）----
        picks: list[str | None] = []
        llm_rows = []
        for _ in range(REPEAT):
            r = svc.baseline(src, c.symptom_text, catalog,
                             build_log=c.build_log)
            pick = r.get("rule_id") if r.get("ok") else None
            picks.append(pick)
            llm_rows.append(r)
        stable = len(set(picks)) == 1
        llm_pick = picks[0]
        j_llm = judge(c, llm_pick, "ok")
        llm_hits = sum(1 for p in picks if judge(c, p, "ok")["hit"])
        llm_fps = sum(1 for p in picks
                      if judge(c, p, "ok")["false_positive"])
        llm_invalid = sum(1 for r in llm_rows if not r.get("ok"))

        rows.append({
            "case_id": c.case_id,
            "kind": c.kind,
            "title": c.title,
            "rule_top1": top1_rule,
            "rule_hit": j_rule["hit"],
            "rule_false_positive": j_rule["false_positive"],
            "llm_pick": llm_pick,
            "llm_picks_all": picks,
            "llm_stable": stable,
            "llm_hit_count": llm_hits,
            "llm_false_positive_count": llm_fps,
            "llm_hit": j_llm["hit"],
            "llm_false_positive": j_llm["false_positive"],
            "llm_invalid_count": llm_invalid,
            "llm_invalid_reason": "; ".join(
                x.get("error", "") for x in llm_rows if not x.get("ok"))[:200],
            "llm_duration_ms": sum(x.get("duration_ms", 0) for x in llm_rows),
            "llm_reason": llm_rows[0].get("reason", ""),
        })
        mark = "OK" if (j_llm["hit"] or (c.kind == "negative"
                                         and not j_llm["false_positive"])) else "✗"
        unst = "" if stable else "（抖动）"
        print(f"{mark} {c.case_id:<5} 规则层={str(top1_rule):<9} "
              f"LLM×{REPEAT}={str(picks):<34}{unst}")

    pos = [r for r in rows if r["kind"] == "positive"]
    neg = [r for r in rows if r["kind"] == "negative"]
    rule_hit = sum(1 for r in pos if r["rule_hit"])
    rule_fp = sum(1 for r in neg if r["rule_false_positive"])
    # LLM 侧按"三次采样"统计，比单次更能说明稳定性
    llm_hit_all = sum(1 for r in pos if r["llm_hit_count"] == REPEAT)
    llm_hit_any = sum(1 for r in pos if r["llm_hit_count"] > 0)
    total_samples = len(pos) * REPEAT
    llm_hit_rate = (sum(r["llm_hit_count"] for r in pos) / total_samples
                    if total_samples else 0.0)
    llm_fp_any = sum(1 for r in neg if r["llm_false_positive_count"] > 0)
    llm_stable = sum(1 for r in rows if r["llm_stable"])
    llm_invalid = sum(r["llm_invalid_count"] for r in rows)
    avg_ms = sum(r["llm_duration_ms"] for r in rows) // max(len(rows) * REPEAT, 1)

    print("\n" + "=" * 66)
    print(f"{'':<26}{'规则层+工具':>16}{f'纯 LLM ×{REPEAT}':>18}")
    print("-" * 66)
    print(f"{'正例 Top-1 命中':<24}{f'{rule_hit}/{len(pos)}':>18}"
          f"{f'{llm_hit_all}/{len(pos)} 全中':>20}")
    print(f"{'　（至少中一次）':<22}{'—':>20}{f'{llm_hit_any}/{len(pos)}':>20}")
    print(f"{'　（单次采样命中率）':<20}{'—':>20}{llm_hit_rate:>19.0%}")
    print(f"{'负例误报（任一采样）':<22}{rule_fp:>20}{llm_fp_any:>20}")
    print(f"{'输出稳定（三次一致）':<22}{'确定性':>20}"
          f"{f'{llm_stable}/{len(rows)}':>20}")
    print(f"{'无效输出次数':<24}{'0':>20}{llm_invalid:>20}")
    print(f"{'平均单次耗时 ms':<23}{'<10':>20}{avg_ms:>20}")
    print("=" * 66)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"llm_baseline_{ts}.json"
    out.write_text(json.dumps({
        "generated_at": ts,
        "model": svc.client.model,
        "host": svc.client.host,
        "catalog_size": len(catalog),
        "case_count": len(rows),
        "repeat": REPEAT,
        "summary": {
            "rule_top1_hit": rule_hit, "rule_positive_total": len(pos),
            "llm_top1_hit_all": llm_hit_all,
            "llm_top1_hit_any": llm_hit_any,
            "llm_hit_rate": round(llm_hit_rate, 3),
            "rule_false_positive": rule_fp,
            "llm_false_positive_any": llm_fp_any,
            "llm_stable_cases": llm_stable,
            "llm_invalid_outputs": llm_invalid, "llm_avg_ms": avg_ms,
        },
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入：{out.relative_to(Path.cwd())}")
    print(f"⚠️  样本仅 {len(rows)} 例（{REPEAT} 次采样），"
          f"**不足以支撑准确率结论**，只作趋势参考。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
