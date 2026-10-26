"""端到端冒烟测试。

用途：
1. Phase 3 第二批验收的**实际运行证据**（本文件所有结论均为真实 HTTP 调用结果）
2. Phase 6 回归测试的雏形

运行前需先启动后端：
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8787

用法：
    python smoke_e2e.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8787/api"
ROOT = Path(__file__).resolve().parent

# 本机环境可能配置了 HTTP 代理，127.0.0.1 走代理会得到 502。
# 所有请求显式绕过代理。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PASS, FAIL = "✅", "❌"
_results: list[tuple[bool, str, str]] = []


def _req(method: str, path: str, body: dict | None = None,
         extra_headers: dict | None = None) -> tuple[int, dict | list | str]:
    url = BASE + path
    data = None
    headers = dict(extra_headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener.open(r, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def check(ok: bool, name: str, detail: str = "") -> bool:
    _results.append((ok, name, detail))
    print(f"{PASS if ok else FAIL} {name}" + (f"  — {detail}" if detail else ""))
    return ok


def sse_events(task_id: str) -> list[tuple[str, dict]]:
    """同步读取完整 SSE 流。"""
    out: list[tuple[str, dict]] = []
    with _opener.open(f"{BASE}/tasks/{task_id}/diagnose", timeout=60) as resp:
        event = None
        for line in resp:
            line = line.decode("utf-8").rstrip("\r\n")
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                try:
                    out.append((event or "", json.loads(line[6:])))
                except json.JSONDecodeError:
                    out.append((event or "", {}))
    return out


def run_case(sample_id: str, title: str, symptom: str,
             expect_hypothesis: str | None) -> str:
    """跑一个完整案例，返回 task_id。"""
    _, task = _req("POST", "/tasks", {
        "title": title, "symptom_text": symptom, "pclk2_hz": 72000000,
    })
    tid = task["id"]

    src = (ROOT / "samples" / f"{sample_id}.c").read_text(encoding="utf-8")
    _req("POST", f"/tasks/{tid}/artifacts",
         {"kind": "source_code", "name": f"{sample_id}.c", "content": src})

    evs = sse_events(tid)
    types = [t for t, _ in evs]
    done = next((d for t, d in evs if t == "done"), {})
    err = next((d for t, d in evs if t == "error"), None)

    check(err is None, f"[{sample_id}] SSE 无引擎错误",
          "" if err is None else str(err.get("message")))
    check("hypothesis_update" in types,
          f"[{sample_id}] SSE 含 hypothesis_update 事件")
    check("retrieval_done" in types, f"[{sample_id}] SSE 含 retrieval_done 事件")

    _, hy = _req("GET", f"/tasks/{tid}/hypotheses")
    items = hy["items"]
    rules = [h["rule_id"] for h in items]

    if expect_hypothesis is None:
        check(len(items) == 0, f"[{sample_id}] 正确版不误报",
              f"实际检出 {rules}")
    else:
        check(expect_hypothesis in rules,
              f"[{sample_id}] 检出 {expect_hypothesis}",
              f"实际 {rules}")
        check(done.get("hypothesis_count", 0) >= 1,
              f"[{sample_id}] done 事件计数一致",
              f"hypothesis_count={done.get('hypothesis_count')}")

    return tid


def main() -> int:
    print("=" * 68)
    print("芯诊 AI · Phase 3 第二批端到端冒烟测试")
    print("=" * 68)

    # ---- 0. 健康检查 ----
    code, health = _req("GET", "/health")
    check(code == 200 and health.get("ok"), "健康检查通过",
          f"规则 {health['corpus']['rules']} 条 / 卡片 {health['corpus']['cards']} 条")

    # ---- 1. 故障版：应检出 R-S1-01 ----
    tid = run_case("c01_no_usart1_clock", "C1 故障版",
                   "串口完全没有输出，但板载LED还在闪烁", "R-S1-01")

    # ---- 2. 数据库层证据标签校验 ----
    _, hy = _req("GET", f"/tasks/{tid}/hypotheses")
    tags = {e["evidence_tag"] for h in hy["items"] for e in h["evidences"]}
    check(all(t in {"STATIC", "INFER", "UNVERIFIED", "HW_TEST", "SIM_TEST", "ASSUMPTION"}
              for t in tags),
          "所有证据标签合法", f"实际 {tags}")

    # ---- 3. unknown 是一等公民 ----
    _, unk = _req("GET", f"/tasks/{tid}/unknowns")
    check(len(unk["items"]) > 0, "产出 unknown 项（知道自己不知道）",
          f"{len(unk['items'])} 项")

    # ---- 4. 检索有命中且带来源 ----
    _, refs = _req("GET", f"/tasks/{tid}/references")
    check(len(refs["items"]) > 0 and all(r["source_ref"] for r in refs["items"]),
          "知识卡片命中且均带来源引用", f"{len(refs['items'])} 条")

    # ---- 5. 验证步骤已生成 ----
    _, vs = _req("GET", f"/tasks/{tid}/verifications")
    check(len(vs["items"]) > 0, "生成验证步骤", f"{len(vs['items'])} 步")

    # ---- 6. 红线：缺失 observed 必须 422 ----
    vid = vs["items"][0]["id"]
    code, _ = _req("POST", f"/tasks/{tid}/verifications/{vid}/result",
                   {"match": "match", "recorded_by": "smoke"})
    check(code == 422, "缺失 observed 时拒绝（422）", f"实际 {code}")

    # ---- 7. 正常回填 ----
    code, res = _req("POST", f"/tasks/{tid}/verifications/{vid}/result",
                     {"observed": "串口收到 [BASELINE] tick 3，无乱码",
                      "match": "match", "recorded_by": "smoke"})
    check(code == 200 and res.get("evidence_tag") == "HW_TEST",
          "回填成功且标签为 HW_TEST", f"HTTP {code}")

    # ---- 8. 正确版：不误报 ----
    run_case("c01_fixed", "C1 正确版", "想确认这套配置有没有问题", None)

    # ---- 9. 空材料：判 insufficient ----
    _, t3 = _req("POST", "/tasks", {"title": "空材料", "symptom_text": ""})
    evs = sse_events(t3["id"])
    done = next((d for t, d in evs if t == "done"), {})
    check(done.get("status") == "insufficient",
          "空材料判 insufficient（不硬给根因）", f"status={done.get('status')}")

    # ---- 10. 报告生成 ----
    _, rep = _req("GET", f"/tasks/{tid}/report?regenerate=true")
    md = rep.get("md_content", "")
    check("## 一、候选根因" in md and "## 二、无法判定的项" in md,
          "Markdown 报告结构完整",
          f"{len(md)} 字符")
    check(md.count("[") > 0 and "证据等级" in md, "报告含证据等级标注")

    # ==================================================================
    # Phase 4 新增能力
    # ==================================================================
    # ---- 11. 工具清单可解释 ----
    _, tools = _req("GET", "/tools")
    tnames = [t["name"] for t in tools["items"]]
    check(len(tnames) >= 6 and all(t["description"] for t in tools["items"]),
          "工具清单齐全且均有说明", f"{len(tnames)} 个")
    check("analyze_isr_body" in tnames and "infer_actual_fck" in tnames,
          "Phase 4 两个新工具已注册", str(tnames))

    # ---- 12. 工具调用全量留痕 ----
    _, runs = _req("GET", f"/tasks/{tid}/tool-runs")
    st = runs.get("stats", {})
    check(st.get("total", 0) >= 5, "工具调用留痕完整", str(st))
    check(all("duration_ms" in i and "input_sum" in i for i in runs["items"]),
          "留痕含耗时与入参摘要")
    check(st.get("skipped", 0) >= 1,
          "跳过步骤被如实记录为 skipped（不冒充已执行）",
          f"skipped={st.get('skipped')}")
    check(st.get("error", 0) == 0, "本轮无工具执行失败", f"error={st.get('error')}")

    # ---- 13. 补充实测波特率后触发反向比对（多步诊断）----
    _, t4 = _req("POST", "/tasks", {
        "title": "C2 乱码·反向比对", "symptom_text": "串口输出全是乱码",
        "pclk2_hz": 72000000})
    src4 = (ROOT / "samples" / "c01_fixed.c").read_text(encoding="utf-8")
    _req("POST", f"/tasks/{t4['id']}/artifacts",
         {"kind": "source_code", "name": "main.c", "content": src4})
    _req("PATCH", f"/tasks/{t4['id']}",
         {"observed_baud": 12800.0, "observed_baud_source": "串口助手实测"})
    code, rr = _req("POST", f"/tasks/{t4['id']}/reassess")
    ran = [r["tool"] for r in rr.get("tool_runs", [])]
    check(code == 200 and "infer_actual_fck" in ran,
          "补充实测波特率后触发反向比对", str(ran))
    _, hy4 = _req("GET", f"/tasks/{t4['id']}/hypotheses")
    tags4 = {e["evidence_tag"] for h in hy4["items"] for e in h["evidences"]}
    check("HW_TEST" in tags4,
          "用户实测观测标为 HW_TEST（不冒充静态分析）", str(tags4))
    check("INFER" in tags4, "推断结论单独标为 INFER", str(tags4))

    # ---- 14. followups：多步诊断的下一步入口 ----
    _, fu = _req("GET", f"/tasks/{t4['id']}/followups")
    check(isinstance(fu["items"], list), "多步诊断给出 followups",
          f"{len(fu['items'])} 条")

    # ---- 15. 报告含"下一步需要补充的信息" ----
    _, rep2 = _req("GET", f"/tasks/{t4['id']}/report?regenerate=true")
    check("下一步需要补充的信息" in rep2.get("md_content", ""),
          "报告含下一步补充信息段落")

    # ---- 16. 硬件实测记录：缺字段必须被拒 ----
    st_bad, _ = _req("POST", f"/tasks/{t4['id']}/hw-evidence",
                     {"kind": "serial_capture", "raw_text": "",
                      "recorded_by": "Zero"})
    check(st_bad == 422, "空抓帧被拒（422）", f"HTTP {st_bad}")

    st_no_rec, _ = _req("POST", f"/tasks/{t4['id']}/hw-evidence",
                        {"kind": "serial_capture", "raw_text": "abc"})
    check(st_no_rec == 422, "缺记录人被拒（422）", f"HTTP {st_no_rec}")

    st_bad_tag, _ = _req("POST", f"/tasks/{t4['id']}/hw-evidence",
                         {"kind": "serial_capture", "raw_text": "abc",
                          "recorded_by": "Zero", "evidence_tag": "STATIC"})
    check(st_bad_tag == 422, "静态分析标签不得混入实测表（422）",
          f"HTTP {st_bad_tag}")

    # ---- 17. 正常提交实测记录并自动判定 ----
    st_hw, hw = _req("POST", f"/tasks/{t4['id']}/hw-evidence",
                     {"kind": "serial_capture",
                      "raw_text": "\r\n".join(
                          [f"tick {i}: hello STM32" for i in range(20)]),
                      "recorded_by": "Zero", "case_id": "C1",
                      "preset": "c01_no_usart1_clock",
                      "note": "演示数据：本条为自造文本，非真实板卡抓帧"})
    check(st_hw == 200, "实测记录提交成功", f"HTTP {st_hw}")
    check(hw.get("verdict") == "NORMAL", "抓帧自动判定为 NORMAL",
          str(hw.get("verdict")))
    check(hw.get("evidence_tag") == "HW_TEST", "实测记录标签为 HW_TEST")

    st_hw2, hw2 = _req("POST", f"/tasks/{t4['id']}/hw-evidence",
                       {"kind": "serial_capture",
                        "raw_text": "\ufffd\ufffd\u00ff\u00fe" * 20,
                        "recorded_by": "Zero", "case_id": "C2"})
    check(hw2.get("verdict") == "GARBLED", "乱码抓帧判定为 GARBLED",
          str(hw2.get("verdict")))

    _, hwl = _req("GET", f"/tasks/{t4['id']}/hw-evidence")
    check(len(hwl["items"]) == 2, "实测记录可列表查询",
          str(len(hwl["items"])))

    _, one = _req("GET", f"/tasks/{t4['id']}/hw-evidence/{hw['id']}")
    check("tick 0" in one.get("raw_text", ""), "单条记录可取回原文")

    # ---- 18. 实测记录参与诊断（现象与代码互证）----
    st_re, re1 = _req("POST", f"/tasks/{t4['id']}/reassess")
    check(st_re == 200, "带实测记录重评估成功", f"HTTP {st_re}")
    tools_used = [t.get("tool") or t.get("tool_name")
                  for t in re1.get("tool_runs", [])]
    check("parse_serial_capture" in tools_used, "重评估调用了抓帧解析工具",
          str(tools_used))

    _, rep3 = _req("GET", f"/tasks/{t4['id']}/report?regenerate=true")
    md3 = rep3.get("md_content", "")
    check("## 六、硬件实测记录" in md3, "报告含硬件实测记录章")
    check("一致性比对" in md3, "报告含现象-预期一致性比对")
    check("全部为未实测的" in md3, "报告声明预期现象表未实测（不夸大）")

    # ---- 19. 误提交可删除 ----
    st_del, _ = _req("DELETE", f"/tasks/{t4['id']}/hw-evidence/{hw2['id']}")
    check(st_del == 200, "误提交记录可删除", f"HTTP {st_del}")

    # ---- 20. 实测回填在重新诊断后不丢失（Phase 7 修复项）----
    # 背景：_persist_report 会重建 verifications，若不迁移 test_results，
    # 用户做完实验回填的记录会被清空 —— 等于"系统忘了你做过的实验"。
    src = (ROOT / "samples" / "c05_rx_irq_no_nvic.c").read_text(encoding="utf-8")
    _, t5 = _req("POST", "/tasks", {"title": "[冒烟] 回填持久化",
                                    "symptom_text": "能发不能收，接收中断进不去",
                                    "pclk2_hz": 72000000})
    _req("POST", f"/tasks/{t5['id']}/artifacts",
         {"kind": "source_code", "name": "main.c", "content": src})
    _req("POST", f"/tasks/{t5['id']}/reassess")

    _, hy1 = _req("GET", f"/tasks/{t5['id']}/hypotheses")
    items5 = hy1.get("items", [])
    check(len(items5) > 0, "C5 检出候选根因", str(len(items5)))

    vid = None
    top_rule = None
    if items5:
        top_rule = items5[0]["rule_id"]
        vs = items5[0].get("verifications") or []
        if vs:
            vid = vs[0]["id"]
    check(vid is not None, "候选含可执行验证步骤")

    if vid:
        st_tr, tr = _req("POST", f"/tasks/{t5['id']}/verifications/{vid}/result",
                         {"observed": "实测：仍然收不到，中断未进",
                          "match": "mismatch", "recorded_by": "Zero"})
        check(st_tr == 200, "回填实测结果成功", f"HTTP {st_tr}")

        _, hy2 = _req("GET", f"/tasks/{t5['id']}/hypotheses")
        refuted = [h for h in hy2["items"] if h["rule_id"] == top_rule]
        check(bool(refuted) and refuted[0]["status"] == "refuted",
              "与预期不符的候选被标记为已排除",
              str(refuted[0]["status"]) if refuted else "无")

        # 关键：重新诊断后实测记录必须还在
        st_re2, _ = _req("POST", f"/tasks/{t5['id']}/reassess")
        check(st_re2 == 200, "回填后重新诊断不报错", f"HTTP {st_re2}")
        _, hy3 = _req("GET", f"/tasks/{t5['id']}/hypotheses")
        keep = [h for h in hy3["items"] if h["rule_id"] == top_rule]
        carried = []
        if keep:
            carried = [v for v in (keep[0].get("verifications") or [])
                       if v.get("observed")]
        check(bool(carried), "重新诊断后实测回填未丢失（关键回归）",
              f"沿用 {len(carried)} 条")
        check(bool(keep) and keep[0]["status"] == "refuted",
              "重新诊断后已排除状态被保留",
              str(keep[0]["status"]) if keep else "无")
        check(bool(keep) and keep[0]["rank"] == len(hy3["items"]),
              "已排除的候选沉到最后一位（重排生效）",
              f"rank={keep[0]['rank']}/{len(hy3['items'])}" if keep else "无")

        _, rep4 = _req("GET", f"/tasks/{t5['id']}/report?regenerate=true")
        check("实测回填" in rep4.get("md_content", ""),
              "报告含实测回填明细")
    else:
        check(False, "回填持久化用例", "未取得验证步骤 id，无法验证")

    # ---- 21. 演示路径：样例库分组与一键载入所需字段 ----
    _, sl = _req("GET", "/samples")
    sitems = sl.get("items", [])
    check(len(sitems) == 12, "样例库共 12 个（C1–C6 各故障版+对照版）",
          str(len(sitems)))
    cases = sorted({s.get("case") for s in sitems})
    check(cases == ["C1", "C2", "C3", "C4", "C5", "C6"],
          "样例覆盖 C1–C6 六个案例", str(cases))
    check(all(s.get("variant") in ("fault", "fixed") for s in sitems),
          "每个样例都标了故障版/对照版")
    check(all("expect_rules" in s for s in sitems),
          "每个样例都声明了预期命中规则（供预期 vs 实际对照）")

    _, one_s = _req("GET", "/samples/c05_rx_irq_no_nvic")
    check("USART1_IRQHandler" in one_s.get("content", ""),
          "样例子段可返回源码正文")
    check(one_s.get("expect_rules") == ["R-S6-01", "R-S6-03"],
          "C5 预期命中两条规则", str(one_s.get("expect_rules")))

    # ---- 22. 工具清单可解释 ----
    _, tl = _req("GET", "/tools")
    tnames = [t["name"] for t in tl.get("items", [])]
    check(len(tnames) >= 8, "工具清单含 8 个工具", str(len(tnames)))
    check(all(t.get("description") for t in tl.get("items", [])),
          "每个工具都有职责说明")

    # ---- 23. 健康检查/模型状态如实（2026-10-26 加固） ----
    _, hv = _req("GET", "/health")
    ml = hv.get("model_layer", {})
    check(str(ml.get("provider", "")).count("cloud") == 1
          and ml.get("fallback") == "rule",
          "健康检查如实报告三级降级链（云端→本地→规则层）",
          str(ml.get("provider")))
    check("Phase 3" not in str(ml.get("note", ""))
          and "不接模型" not in str(ml.get("note", "")),
          "健康检查不再残留'先纯规则层/不接模型'的过期说明")
    _, ms = _req("GET", "/models/status")
    lv = {x.get("level"): x for x in ms.get("levels", [])}
    check(lv.get("L3", {}).get("status") == "always_available",
          "模型状态区分'配置态'：规则层始终可用")
    check(lv.get("L1", {}).get("configured") is False,
          "未填密钥时云端 L1 如实标为未配置（不假装可用）")

    # ---- 24. 任务软删除与状态白名单 ----
    st_new, tmp_task = _req("POST", "/tasks", {"title": "删除用例临时任务"})
    tmp_id = tmp_task.get("id", "")
    check(st_new == 200 and bool(tmp_id), "创建临时任务（删除用例）")
    st_del, _ = _req("DELETE", f"/tasks/{tmp_id}")
    check(st_del == 200, "任务可软删除", f"HTTP {st_del}")
    st_gone, _ = _req("GET", f"/tasks/{tmp_id}")
    check(st_gone == 404, "已删除任务不可再直接访问", f"HTTP {st_gone}")
    _, lst2 = _req("GET", "/tasks")
    check(all(t.get("id") != tmp_id for t in lst2.get("items", [])),
          "已删除任务不再出现在任务列表")
    st_bad, _ = _req("PATCH", f"/tasks/{tid}", {"status": "hacked"})
    check(st_bad == 422, "非法任务状态被 422 拒绝（状态白名单）",
          f"HTTP {st_bad}")

    # ---- 25. 测试者自带云端密钥（只回掩码，服务端不保存） ----
    fake_key = "sk-smoke-test-0123456789abcdef"
    st_v, vr = _req("POST", "/llm/verify-key", {"api_key": fake_key})
    check(st_v == 200 and vr.get("ok") is False,
          "无效密钥校验结果为不可用（不误报可用）", str(vr.get("reason"))[:40])
    check(fake_key not in json.dumps(vr, ensure_ascii=False)
          and "…" in str(vr.get("masked")),
          "校验响应只回掩码，不含明文密钥", str(vr.get("masked")))
    check(vr.get("storage") == "none",
          "校验接口明确声明服务端不保存密钥")
    code_s, stt = _req("GET", "/llm/status", extra_headers={"X-Cloud-Key": fake_key})
    cloud = (stt or {}).get("cloud", {}) if isinstance(stt, dict) else {}
    check(code_s == 200 and cloud.get("key_source") == "session"
          and cloud.get("configured") is True,
          "请求头携带的密钥被识别为本次会话密钥", str(cloud.get("key_source")))
    check(fake_key not in json.dumps(stt, ensure_ascii=False),
          "状态响应不含明文密钥")

    # ---- 汇总 ----
    ok = sum(1 for r in _results if r[0])
    total = len(_results)
    print("=" * 68)
    print(f"结果：{ok}/{total} 通过")
    if ok < total:
        print("\n未通过项：")
        for good, name, detail in _results:
            if not good:
                print(f"  {FAIL} {name}  {detail}")
    print("=" * 68)
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(main())
