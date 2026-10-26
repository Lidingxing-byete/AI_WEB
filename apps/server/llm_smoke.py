"""模型解读链路的 API 级端到端测试（需要后端在跑）。

与 `tools_test.py` 等纯离线脚本不同，本脚本**依赖后端与 Ollama**：
- 后端不可达 → SKIP（退出码 0，但明确打印"未验证"，绝不计为通过）
- 后端可达但模型不可用 → 降级路径必须生效，并**单独断言**

覆盖的关键性质：
1. 模型在线时：诊断流推送真实 `llm_narrative` 事件，标签恒为 `[推断]`。
2. 模型**不参与评分**：开/关模型的两次诊断，候选规则号与分值必须一致。
3. 输出不越权：模型文本中不得出现证据标签与"已实测/已验证"类表述。
4. 调用元数据落库可查：`/api/tasks/{tid}/llm-calls`。
5. 报告第七章如实记录：可用则记内容，降级则记原因。

运行：`python llm_smoke.py`
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8787"
# 绕过系统代理直连本机（本机有 HTTP_PROXY 环境变量）
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"✅ {name}")
    else:
        FAIL += 1
        print(f"❌ {name}  {detail}")


def req(method: str, path: str, body: dict | None = None,
        timeout: float = 120.0) -> tuple[int, dict | str]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    r = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:                                # noqa: BLE001
        return 0, str(e)
    try:
        return 200, json.loads(raw)
    except json.JSONDecodeError:
        return 200, raw


def sse_events(path: str, timeout: float = 180.0) -> list[tuple[str, dict]]:
    """读取一次 SSE 流，返回全部 (event, data)。"""
    out: list[tuple[str, dict]] = []
    r = urllib.request.Request(f"{BASE}{path}", method="GET")
    try:
        with OPENER.open(r, timeout=timeout) as resp:
            ev, buf = "", ""
            while True:
                chunk = resp.read1(4096).decode("utf-8", "replace")
                if not chunk:
                    break
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    e, d = "", ""
                    for line in block.splitlines():
                        if line.startswith("event: "):
                            e = line[7:]
                        elif line.startswith("data: "):
                            d = line[6:]
                    if e:
                        try:
                            out.append((e, json.loads(d)))
                        except json.JSONDecodeError:
                            out.append((e, {"raw": d}))
                    if e in ("closed", "error"):
                        return out
    except Exception as e:                                # noqa: BLE001
        out.append(("stream_error", {"message": str(e)}))
    return out


def main() -> int:
    code, status = req("GET", "/api/health")
    if code != 200:
        print("⚠️  后端未运行，本测试**未验证**（不计入通过）。")
        return 0
    check("后端可达", True)

    code, st = req("GET", "/api/llm/status")
    check("模型状态端点可用", code == 200 and isinstance(st, dict), f"HTTP {code}")
    available = bool(st.get("available")) if isinstance(st, dict) else False
    print(f"   模型：{st.get('model')} @ {st.get('host')} → "
          f"{'在线' if available else '离线：' + str(st.get('reason'))}")

    # ---- 建任务并提交 C1 故障源码 ----
    code, t = req("POST", "/api/tasks", {"title": "[演示数据] 模型链路测试",
                                         "symptom_text": "串口完全没有输出，但板载LED还在闪烁"})
    if code != 200 or not isinstance(t, dict):
        check("创建任务", False, f"HTTP {code} {t}")
        return 1
    tid = t["id"]
    src = open("samples/c01_no_usart1_clock.c", encoding="utf-8").read()
    req("POST", f"/api/tasks/{tid}/artifacts",
        {"kind": "source_code", "name": "c01.c", "content": src})

    # ---- ① 纯规则层（use_llm=0）基线 ----
    code, r0 = req("POST", f"/api/tasks/{tid}/reassess?use_llm=0")
    base_rules = {}
    if code == 200 and isinstance(r0, dict):
        base_rules = {h["rule_id"]: h["score"] for h in r0.get("hypotheses", [])}
    check("规则层基线可跑", code == 200, f"HTTP {code}")

    # ---- ② 开启模型 ----
    evs = sse_events(f"/api/tasks/{tid}/diagnose?use_llm=1")
    types = [e for e, _ in evs]
    check("诊断流正常结束", "closed" in types or "done" in types, str(types[-3:]))
    check("流中无 error 事件", "error" not in types,
          str([d for e, d in evs if e == "error"]))

    nar = None
    for e, d in evs:
        if e == "llm_narrative":
            nar = d
    if available:
        check("推送了 llm_narrative 事件", nar is not None)
        if nar:
            check("模型输出标签恒为 [推断]", nar.get("tag") == "[推断]",
                  str(nar.get("tag")))
            check("模型解读非空", bool(nar.get("summary")), str(nar)[:120])
            # 越权扫描：模型文本不得出现证据标签或已实测表述
            import re
            blob = " ".join([nar.get("summary") or ""]
                            + nar.get("extra_steps", [])
                            + nar.get("cautions", []))
            bad = re.findall(r"\[\s*(?:实测|静态分析|推断|假设|待验证)[^\[\]]*\]"
                             r"|(?:已|已经|已?经)(?:过)?(?:实测|上板|烧录)"
                             r"|已(?:经)?(?:验证|确认)", blob)
            check("模型输出无越权表述", not bad, str(bad[:3]))
    else:
        print("⚠️  模型离线，改为断言**降级路径**")
        code, r1 = req("POST", f"/api/tasks/{tid}/reassess?use_llm=1")
        check("模型离线时诊断仍成功", code == 200, f"HTTP {code}")
        lim = " ".join(r1.get("limitations", [])) if isinstance(r1, dict) else ""
        check("离线原因被写入 limitations", "模型" in lim, lim[:120])

    # ---- ③ 模型不得改变根因判定 ----
    code, r2 = req("POST", f"/api/tasks/{tid}/reassess?use_llm=1")
    got = {h["rule_id"]: h["score"] for h in r2.get("hypotheses", [])} \
        if code == 200 and isinstance(r2, dict) else {}
    check("开启模型后候选与评分不变（模型无诊断权）",
          got == base_rules, f"基线 {base_rules} vs 开启后 {got}")

    # ---- ④ 调用元数据落库 ----
    code, lc = req("GET", f"/api/tasks/{tid}/llm-calls")
    items = lc.get("items", []) if isinstance(lc, dict) else []
    check("llm-calls 端点可查", code == 200, f"HTTP {code}")
    if available:
        check("记录了 1 条模型调用", len(items) >= 1, f"{len(items)} 条")
        if items:
            check("记录含模型名与耗时",
                  bool(items[0].get("model")) and items[0].get("duration_ms", 0) >= 0,
                  str(items[0])[:120])
            check("记录不含提示词原文字段（防源码入库）",
                  "prompt" not in items[0] and "prompt_text" not in items[0])
    else:
        check("离线时记录为降级（ok=0）",
              bool(items) and items[0].get("ok") == 0, str(items[:1]))

    # ---- ⑤ 报告第七章 ----
    code, rep = req("GET", f"/api/tasks/{tid}/report?regenerate=1")
    md = rep.get("md_content", "") if isinstance(rep, dict) else ""
    check("报告含第七章「模型解读」", "## 七、模型解读" in md)
    if available:
        check("第七章写明 [推断] 定位", "`[推断]`" in md)
    else:
        check("第七章如实记录降级", ("降级" in md) or ("未启用" in md))

    print()
    print(f"结果：通过 {PASS} · 失败 {FAIL}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
