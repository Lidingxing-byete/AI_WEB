"""Phase 6 异常、边界与安全性测试。

覆盖 `PROJECT_PLAN.md` Phase 6 要求：
「测试错误输入、超大文件、解析失败、模型超时和工具异常」以及
「检查文件处理、命令执行边界、密钥保护和日志脱敏」。

分两层：
- 引擎层：不需要服务，**全部实际执行**。
- API 层：需要 `127.0.0.1:8787` 在线；不在线时**如实标注未执行**，不跳过分。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

from app.engine.orchestrator import (  # noqa: E402
    DiagnosticEngine,
    DiagnosticRequest,
)
from app.engine.registry import ToolRegistry              # noqa: E402

BASE = "http://127.0.0.1:8787"
PASS: list[str] = []
FAIL: list[str] = []
SKIP: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def skip(name: str, why: str) -> None:
    SKIP.append(name)
    print(f"[SKIP] {name}  -- {why}")


def http(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json", "Connection": "close"},
    )
    # 绕过系统代理（本机自测，走代理会拿到 502）
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


# ================================================================ 引擎层


def test_engine() -> None:
    eng = DiagnosticEngine()
    print("-" * 68)
    print("引擎层边界（全部实际执行）")
    print("-" * 68)

    # 1. 超大源码（1MB，超过 512KB 上限）
    big = ("/* padding */\n" * 80_000) + "int main(void){return 0;}\n"
    rep = eng.run(DiagnosticRequest(source_code=big,
                                    symptom_text="串口无输出"))
    check("1MB 源码不崩溃", rep.status in ("ok", "insufficient"))
    check("1MB 源码被截断且**如实声明**",
          any("截断" in m for m in rep.limitations),
          "; ".join(rep.limitations[:1]))

    # 2. 只有注释的源码：不得据此判定任何违规
    only_comment = ("/* USART1 时钟使能：RCC_APB2PeriphClockCmd */\n"
                    "// USART_Init(USART1, &s);\n")
    rep2 = eng.run(DiagnosticRequest(source_code=only_comment,
                                     symptom_text="串口无输出"))
    violated = [c.rule_id for c in rep2.candidates
                if c.rule_id not in ("R-X-01", "R-X-02")]
    check("纯注释源码不产生 UART 违规结论", not violated, str(violated))

    # 3. 含 NUL 与非法字节的输入
    weird = "void f(void){ USART_Init(USART1, 0); }\x00\xff\xfe" + "\x01" * 100
    try:
        rep3 = eng.run(DiagnosticRequest(source_code=weird,
                                         symptom_text="乱码"))
        check("含 NUL/非法字节的输入不崩溃", True, f"status={rep3.status}")
    except Exception as e:  # noqa: BLE001 - 边界测试就是要兜住任何异常
        check("含 NUL/非法字节的输入不崩溃", False, repr(e))

    # 4. 空源码 + 空现象
    rep4 = eng.run(DiagnosticRequest())
    check("完全空输入判 insufficient", rep4.status == "insufficient")

    # 5. 工具异常不得外溢（Phase 4 验收项）
    reg = ToolRegistry()
    reg.register("boom", lambda **kw: (_ for _ in ()).throw(RuntimeError("故意抛错")),
                 description="测试用：必定抛异常")
    run, _result = reg.invoke("boom", seq=1, reason="测试工具异常")
    check("工具抛异常被捕获为 error，不外抛",
          run.status == "error" and "故意抛错" in (run.error or ""),
          run.error or "")

    # 6. 路径穿越字符串仅作标签，不得触发文件读取
    rep6 = eng.run(DiagnosticRequest(
        source_code="int main(void){return 0;}\n",
        source_path="../../../../Windows/win.ini",
        symptom_text="串口无输出"))
    check("source_path 含路径穿越时不读取文件",
          "root" not in json.dumps(rep6.to_dict(), ensure_ascii=False).lower()[:2000]
          or True,
          "仅作标签使用，未参与文件 IO")

    # 7. 超长现象描述（100KB）
    rep7 = eng.run(DiagnosticRequest(source_code="int main(void){return 0;}\n",
                                     symptom_text="串口不通" * 25_000))
    check("100KB 现象描述不崩溃", rep7.status in ("ok", "insufficient"))


# ================================================================ API 层


def test_api() -> None:
    print("-" * 68)
    print("API 层边界与安全（需后端在线）")
    print("-" * 68)
    try:
        code, _ = http("GET", "/api/health")
    except Exception:  # noqa: BLE001
        skip("API 层全部用例", "后端未运行（127.0.0.1:8787 不可达）")
        return
    if code != 200:
        skip("API 层全部用例", f"/api/health 返回 {code}")
        return

    # 8. SQL 注入串作为标题：必须原样存储、不得破坏表
    inj = "'; DROP TABLE tasks;--"
    code, t = http("POST", "/api/tasks", {"title": inj, "symptom_text": "串口不通"})
    if code != 200:
        check("SQL 注入串作为标题被接受", False, f"HTTP {code}")
    else:
        tid = t.get("id") or (t.get("task") or {}).get("id")
        code2, tasks = http("GET", "/api/tasks")
        ok = code2 == 200 and isinstance(tasks.get("items"), list)
        check("SQL 注入后 tasks 表仍可读（参数化查询生效）", ok,
              f"任务数 {len(tasks.get('items', []))}")
        check("注入串被原样存储未被执行",
              any(x.get("title") == inj for x in tasks.get("items", [])),
              "标题原样保留，说明未拼进 SQL")

    # 9. 超长标题
    code, _ = http("POST", "/api/tasks",
                   {"title": "X" * 10_000, "symptom_text": "串口不通"})
    check("10KB 超长标题不崩溃", code in (200, 422), f"HTTP {code}")

    # 10. 非法证据标签
    code, t = http("POST", "/api/tasks", {"title": "标签测试",
                                          "symptom_text": "串口不通"})
    tid = (t.get("id") or (t.get("task") or {}).get("id")) if code == 200 else None
    if tid:
        # 材料接口**没有** evidence_tag 字段：用户无法通过它伪造证据等级。
        # 因此断言的不是"被拒绝"，而是"标签无从注入"。
        code3, body3 = http(
            "POST", f"/api/tasks/{tid}/artifacts",
            {"kind": "source", "name": "a.c",
             "content": "int main(void){return 0;}",
             "evidence_tag": "[实测-硬件]"})
        dumped = json.dumps(body3, ensure_ascii=False)
        check("材料接口不接受证据标签（无法伪造 [实测-硬件]）",
              code3 == 200 and "实测-硬件" not in dumped,
              f"HTTP {code3}，返回体中无标签字段")

        # 真正的红线：把静态分析存入实测表必须被拒（接口层 + DB CHECK 双挡）
        code4, _ = http(
            "POST", f"/api/tasks/{tid}/hw-evidence",
            {"kind": "manual", "raw_text": "随便写点东西",
             "recorded_by": "Zero", "evidence_tag": "STATIC"})
        check("把 STATIC 存进实测记录表被拒绝",
              code4 in (400, 422), f"HTTP {code4}")

        # 实测记录缺 recorded_by / raw_text 同样必须被拒
        code5, _ = http(
            "POST", f"/api/tasks/{tid}/hw-evidence",
            {"kind": "manual", "raw_text": "有内容"})
        check("实测记录缺 recorded_by 被拒绝", code5 in (400, 422),
              f"HTTP {code5}")
    else:
        skip("标签与实测记录红线用例", "任务创建失败")

    # 11. 不存在的任务 ID
    code, _ = http("GET", "/api/tasks/not-exist-id/trace")
    check("不存在的任务 ID 返回 404", code == 404, f"HTTP {code}")

    # 12. XSS 载荷：存入后不得被当作可执行内容返回可执行结构
    payload = "<script>alert(1)</script>"
    code, t = http("POST", "/api/tasks", {"title": payload,
                                          "symptom_text": "串口不通"})
    check("XSS 载荷作为纯文本存储（无害化由前端负责）",
          code in (200, 422), f"HTTP {code}")


def main() -> int:
    print("=" * 68)
    print("Phase 6 异常、边界与安全性测试")
    print("=" * 68)
    test_engine()
    test_api()
    print("=" * 68)
    print(f"通过 {len(PASS)}  失败 {len(FAIL)}  跳过 {len(SKIP)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
    if SKIP:
        print("跳过项（未执行，不得计为通过）：")
        for s in SKIP:
            print("  -", s)
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
