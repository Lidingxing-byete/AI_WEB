"""一键回归测试入口。

用途：把散落的测试脚本聚成一条命令，拿到**可核对**的总结果。

    python run_all_tests.py

设计约束（与项目证据纪律一致）：
- 每个子脚本单独跑，退出码非 0 即判失败，不做任何"看起来差不多"的容错。
- 依赖后端服务的脚本（smoke_e2e）在后端不可达时判为 **SKIP**，
  **绝不计入通过**：跑不了就是没验证，不能算验证过。
- 全程不新增第三方依赖，只用标准库。

用法：
    python run_all_tests.py            # 跑全部
    python run_all_tests.py --no-e2e   # 跳过需要后端的端到端测试
"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable

# （脚本相对路径，是否需要后端，说明）
SUITES: list[tuple[str, bool, str]] = [
    ("tools_test.py", False, "诊断工具层（引擎单测）"),
    ("samples_test.py", False, "故障样例自检（故障版命中 + 对照版不误报）"),
    ("llm_test.py", False, "模型层守卫与降级链（离线替身 + 在线真实调用）"),
    ("eval/eval_run.py", False, "固定评测集（6 正例 + 3 负例）"),
    ("eval/edge_cases.py", False, "边界与安全（异常输入/注入/标签红线）"),
    ("eval/security_audit.py", False, "仓库合规审计（密钥/版权/XSS）"),
    ("smoke_e2e.py", True, "端到端 HTTP 冒烟（需后端 8787）"),
    ("llm_smoke.py", True, "模型解读链路（需后端 8787 + 本机 Ollama）"),
]

PASS, FAIL, SKIP = "✅", "❌", "⏭"
rows: list[tuple[str, str, str]] = []


def backend_alive() -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8787/api/health", timeout=3) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


def run_one(rel: str) -> tuple[int, str]:
    """跑一个子脚本，返回 (退出码, 汇总摘要)。

    摘要优先取含"通过"的汇总行（如 `结果：54/54 通过`），
    取不到再退化为最后一行非空输出——保证一眼能抄出数字，而不是一条分隔线。
    """
    p = subprocess.run(
        [PY, rel],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (p.stdout or "") + (p.stderr or "")
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    # 优先取带数字的汇总行（如 `通过 52 项，失败 0 项`），其次取任意含"通过"的行
    for ln in reversed(lines):
        if "通过" in ln and any(ch.isdigit() for ch in ln):
            return p.returncode, ln
    for ln in reversed(lines):
        if "通过" in ln and not ln.startswith("="):
            return p.returncode, ln
    return p.returncode, (lines[-1] if lines else "无输出")


def main() -> int:
    skip_e2e = "--no-e2e" in sys.argv
    alive = backend_alive()

    print("=" * 74)
    print("芯诊 AI · 一键回归测试")
    print(f"解释器：{PY}")
    print(f"后端 127.0.0.1:8787：{'已连接' if alive else '未响应'}")
    print("=" * 74)

    t0 = time.time()
    for rel, need_backend, desc in SUITES:
        if skip_e2e and need_backend:
            rows.append((SKIP, desc, "按 --no-e2e 跳过"))
            print(f"{SKIP} {desc}  — 按 --no-e2e 跳过")
            continue
        if need_backend and not alive:
            rows.append((SKIP, desc, "后端未启动，未验证（不计入通过）"))
            print(f"{SKIP} {desc}  — 后端未启动，未验证（不计入通过）")
            continue

        code, tail = run_one(rel)
        ok = code == 0
        rows.append((PASS if ok else FAIL, desc, tail))
        print(f"{PASS if ok else FAIL} {desc}  — {tail}")

    passed = sum(1 for r in rows if r[0] == PASS)
    failed = sum(1 for r in rows if r[0] == FAIL)
    skipped = sum(1 for r in rows if r[0] == SKIP)

    print("=" * 74)
    print(f"通过 {passed} · 失败 {failed} · 未验证 {skipped}"
          f" · 耗时 {time.time() - t0:.1f}s")
    if failed:
        print("\n失败项：")
        for mark, name, detail in rows:
            if mark == FAIL:
                print(f"  {FAIL} {name}  {detail}")
    if skipped:
        print("\n未验证项（这些**不算通过**，仅说明本次没有验证到）：")
        for mark, name, detail in rows:
            if mark == SKIP:
                print(f"  {SKIP} {name}  {detail}")
    print("=" * 74)

    if failed:
        return 1
    if skipped == len(rows):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
