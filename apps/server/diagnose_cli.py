#!/usr/bin/env python3
"""Phase 3 第一批验收入口：以命令行跑通一次端到端诊断。

用法：
    python diagnose_cli.py --source samples/c01_no_usart1_clock.c \
                           --symptom "串口完全没有输出，但板载 LED 还在闪烁"

    # 输出完整 JSON（供前端 / 测试集消费）
    python diagnose_cli.py --source ... --symptom ... --json

说明：本入口只做「内核可跑通」的验证，不是最终交付形态。
      Web 界面与 SSE 属第二批。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# Windows 控制台默认 GBK，强制 UTF-8 以免中文输出乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.engine.orchestrator import DiagnosticEngine, DiagnosticRequest  # noqa: E402

TAG_COLORS = {
    "[实测-硬件]": "实测",
    "[实测-仿真]": "实测",
    "[静态分析]": "静态",
    "[推断]": "推断",
    "[假设]": "假设",
    "[待验证]": "待验证",
}


def render(report) -> None:
    print("=" * 72)
    print("芯诊 AI · 诊断结果")
    print("=" * 72)

    if report.status == "insufficient":
        print("\n⚠ 材料不足，无法定位")
        print(f"  原因：{report.insufficient_reason}")

    if report.symptom:
        print(f"\n【症状判定】{report.symptom}（命中关键词：{', '.join(report.symptom_hits)}）")
        print("  证据标签：[推断] —— 由现象描述关键词匹配得出，非实测")
    else:
        print("\n【症状判定】未从描述中识别出明确症状类别")

    print("\n【执行轨迹】")
    for t in report.trace:
        mark = "✓" if t.status == "called" else "⊘"
        print(f"  {mark} {t.seq}. {t.tool:<20} {t.status:<8} {t.reason}")
        if t.summary:
            print(f"        └─ {t.summary}")

    print(f"\n【候选根因】共 {len(report.candidates)} 项，按证据支持度排序")
    if not report.candidates:
        print("  （无）")
    for i, c in enumerate(report.candidates, 1):
        print(f"\n  {i}. [{c.rule_id}] {c.cause}")
        print(f"     支持度 {c.score:.2f}   关联案例 {c.linked_case or '—'}")
        if c.ref:
            print(f"     依据：{c.ref}")
        if c.supporting:
            print("     支持证据：")
            for f in c.supporting:
                print(f"       · {f.tag} {f.text}")
        if c.verification_steps:
            print("     建议验证步骤：")
            for j, s in enumerate(c.verification_steps, 1):
                print(f"       {j}) {s}")

    print(f"\n【无法判定项】共 {len(report.unknowns)} 项（静态层面确实判不了，需补充信息）")
    for u in report.unknowns:
        print(f"  · [{u['rule_id']}] {u['cause']}")
        print(f"      {u['text']}")
        if u.get("hint"):
            print(f"      → 需要：{u['hint']}")

    print(f"\n【知识引用】共 {len(report.references)} 条")
    for r in report.references:
        print(f"  · {r['card_id']} {r['title']}  (score {r['score']})")
        print(f"      命中词：{', '.join(r['matched_terms'][:8])}")
        if r["source_ref"]:
            print(f"      出处：{r['source_ref']}")

    if report.limitations:
        print("\n【本次分析的局限】")
        for l in report.limitations:
            print(f"  · {l}")

    print("\n" + "=" * 72)
    print(f"整体证据等级：{report.overall_tag}")
    print("说明：本轮为纯规则层诊断，未调用任何大模型。")
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description="芯诊 AI 诊断内核命令行入口")
    ap.add_argument("--source", help="C 源码文件路径")
    ap.add_argument("--symptom", default="", help="现象描述")
    ap.add_argument("--build-log", dest="build_log", help="编译日志文件路径")
    ap.add_argument("--hse", type=int, help="实际外部晶振频率（Hz），如 8000000")
    ap.add_argument("--pclk2", type=int, help="PCLK2 频率（Hz），USART1 所在总线")
    ap.add_argument("--pclk1", type=int, help="PCLK1 频率（Hz），USART2/3 所在总线")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非可读文本")
    args = ap.parse_args()

    source = ""
    path = ""
    if args.source:
        p = Path(args.source)
        source = io.open(p, encoding="utf-8", errors="ignore").read()
        path = p.name

    build_log = ""
    if args.build_log:
        build_log = io.open(args.build_log, encoding="utf-8", errors="ignore").read()

    req = DiagnosticRequest(
        source_code=source,
        source_path=path,
        symptom_text=args.symptom,
        build_log=build_log,
        actual_hse_hz=args.hse,
        pclk2_hz=args.pclk2,
        pclk1_hz=args.pclk1,
    )

    report = DiagnosticEngine().run(req)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        render(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
