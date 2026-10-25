"""Phase 6 消融与下界基线。

目的（对应 `docs/Phase2_评测指标定义.md` 第三节）：说明系统各组件**实际贡献了什么**，
而不是只报一个总分。

诚实边界（务必看清，答辩会问）：
- 本文件跑的是**消融**与**下界基线**，两者都不需要模型，因此可以实测。
- **纯 LLM 基线本次未执行**——Phase 4 决策不接模型、Ollama 服务未运行
  （风险 R15）。这里**不**用任何方式替代它、也不估算它的分数。
  待接入模型后用 `llm_baseline.py`（预留）按同一测试集跑，再补入报告。

各实验组：
    A0 完整引擎                    —— 参照组
    A1 去掉 RAG 检索               —— 看检索对结论/可追溯性的影响
    A2 去掉症状加权（detect_symptom）—— 看现象描述对排序的贡献
    A3 随机基线                    —— 下界：证明任务不是"随便猜也能中"
    A4 关键词朴素基线              —— 下界：模拟"只看关键词不看结构"的做法
                                      （自撰启发式，**不是** LLM，不得冒充）
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

from app.engine.orchestrator import (  # noqa: E402
    DiagnosticEngine,
    DiagnosticRequest,
)
import app.engine.orchestrator as orc                      # noqa: E402
from app.corpus.loader import load_rules                   # noqa: E402
from eval.evalset import CASES                             # noqa: E402
from eval.eval_run import build_source, SAMPLES            # noqa: E402
from eval.metrics import assess                            # noqa: E402

REPEAT = 3


def _request(case) -> DiagnosticRequest:
    return DiagnosticRequest(
        source_code=build_source(case),
        source_path=case.source_path,
        symptom_text=case.symptom_text,
        build_log=case.build_log,
        actual_hse_hz=case.actual_hse_hz,
        pclk2_hz=case.pclk2_hz,
        pclk1_hz=case.pclk1_hz,
    )


def run_engine(eng: DiagnosticEngine, case) -> dict:
    rep = eng.run(_request(case))
    return assess(rep, case, build_source(case))


# ---------------------------------------------------------------- 下界基线


def random_baseline(case, seed: int) -> dict:
    """随机基线：从规则库里随机挑一条作为唯一根因。"""
    rules = list(load_rules())
    rnd = random.Random(seed)
    pick = rnd.choice(rules).rule_id
    res = {
        "status": "ok", "rule_ids": [pick], "top1": pick,
        "top1_hit": pick == case.expect_top1,
        "missed": pick != case.expect_top1,
        "must_not_top1_violated": pick in case.must_not_top1,
        "forbidden_present": [r for r in [pick] if r in case.forbid_rules],
        "expect_rule_missing": [
            r for r in case.expect_rule_present if r != pick],
        "evidence_total": 0, "evidence_grounded": 0,
        "evidence_unverifiable": 0, "evidence_contradiction": 0,
        "traceable_ok": 0, "traceable_total": 0,
        "candidate_count": 1, "substantive_candidate_count": 1,
    }
    return res


def keyword_baseline(case) -> dict:
    """关键词朴素基线（自撰）：只看字符串是否出现，不做结构化检查。

    这套规则刻意写得"像人凭经验猜"：看到 printf 就说重定向、看到
    引脚配置就说模式问题。它的作用是给出**非结构化方法的下界**。
    """
    src = build_source(case)
    if "USART" not in src.upper():
        pick = "R-S1-01"          # 没看到 USART 就说是时钟问题（正是 N-03 的坑）
    elif "printf" in src and "fputc" not in src:
        pick = "R-S5-01"
    elif "IN_FLOATING" in src or "GPIO_Mode_Out" in src:
        pick = "R-S1-03"
    elif "HSE_VALUE" in src:
        pick = "R-S2-01"
    elif "ITConfig" in src or "RXNE" in src:
        pick = "R-S6-01"
    else:
        pick = "R-S2-02"
    res = {
        "status": "ok", "rule_ids": [pick], "top1": pick,
        "top1_hit": pick == case.expect_top1,
        "missed": pick != case.expect_top1,
        "must_not_top1_violated": pick in case.must_not_top1,
        "forbidden_present": [r for r in [pick] if r in case.forbid_rules],
        "expect_rule_missing": [
            r for r in case.expect_rule_present if r != pick],
        "evidence_total": 0, "evidence_grounded": 0,
        "evidence_unverifiable": 0, "evidence_contradiction": 0,
        "traceable_ok": 0, "traceable_total": 0,
        "candidate_count": 1, "substantive_candidate_count": 1,
    }
    return res


# ---------------------------------------------------------------- 主流程


def main() -> int:
    pos = [c for c in CASES if c.kind == "positive"]
    neg = [c for c in CASES if c.kind == "negative"]

    groups: dict[str, dict] = {}

    # ---- A0 完整引擎 ----
    eng0 = DiagnosticEngine()
    groups["A0 完整引擎"] = {c.case_id: run_engine(eng0, c) for c in CASES}

    # ---- A1 去掉 RAG 检索 ----
    eng1 = DiagnosticEngine()
    eng1.registry.unregister("search_reference")   # 受控替换，非绕过校验
    eng1.registry.register(
        "search_reference",
        lambda query="", top_k=5: [],
        description="（消融用）关闭检索",
        summarize=lambda hits: "命中 0 条（消融：检索已关闭）",
    )
    groups["A1 去 RAG 检索"] = {c.case_id: run_engine(eng1, c) for c in CASES}

    # ---- A2 去掉症状加权 ----
    eng2 = DiagnosticEngine()
    orig_detect = orc.detect_symptom
    orc.detect_symptom = lambda text: (None, [])          # noqa: E731
    try:
        groups["A2 去症状加权"] = {
            c.case_id: run_engine(eng2, c) for c in CASES}
    finally:
        orc.detect_symptom = orig_detect

    # ---- A3 随机基线（下界）----
    groups["A3 随机基线"] = {
        c.case_id: random_baseline(c, seed=20261025) for c in CASES}

    # ---- A4 关键词朴素基线（下界）----
    groups["A4 关键词朴素基线"] = {
        c.case_id: keyword_baseline(c) for c in CASES}

    # ---- 汇总（只给原始计数）----
    print("=" * 78)
    print("Phase 6 消融与下界基线（证据等级：[静态分析]）")
    print(f"正例 {len(pos)} 个，负例 {len(neg)} 个；每例 {REPEAT} 次取首轮")
    print("=" * 78)
    header = (f"{'实验组':<22}{'正例Top1命中':>14}{'负例误报':>10}"
              f"{'证据有据':>10}{'参考条目':>10}")
    print(header)
    print("-" * 78)
    for name, data in groups.items():
        pos_hit = sum(1 for c in pos if data[c.case_id].get("top1_hit"))
        neg_bad = sum(1 for c in neg
                      if data[c.case_id].get("forbidden_present"))
        grounded = sum(data[c.case_id].get("evidence_grounded", 0)
                       for c in CASES)
        total_ev = sum(data[c.case_id].get("evidence_total", 0)
                       for c in CASES)
        refs = sum(data[c.case_id].get("has_references", 0) for c in CASES)
        print(f"{name:<22}{pos_hit:>10}/{len(pos)}"
              f"{neg_bad:>10}{grounded:>7}/{total_ev}{refs:>10}")

    print("-" * 78)
    print("负例误报 = 给出了不该给出的 UART 根因的用例数（越少越好）")
    print()
    print("纯 LLM 基线：**未执行** —— Phase 4 决策未接模型，Ollama 未运行。")
    print("              本表不得被解读为「与 LLM 的对比结果」。")
    print("=" * 78)

    # 逐用例列出 A0 与 A2 的排序差异（症状加权的实际影响）
    diff = [(c.case_id, groups["A0 完整引擎"][c.case_id]["rule_ids"],
             groups["A2 去症状加权"][c.case_id]["rule_ids"])
            for c in CASES
            if groups["A0 完整引擎"][c.case_id]["rule_ids"]
            != groups["A2 去症状加权"][c.case_id]["rule_ids"]]
    print(f"\n症状加权（A2）改变候选排序的用例：{len(diff)}/{len(CASES)}")
    for cid, a, b in diff:
        print(f"  {cid}: A0={a}  A2={b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
