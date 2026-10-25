"""模型层测试（离线优先）。

分两部分：
- **离线用例**：用一个假 client 替身，不联网也能跑。覆盖守卫、
  降级链、字段白名单——这些是"模型可控性"的核心，必须每次回归都验。
- **在线用例**：本机 Ollama 可达时才跑（真实 qwen2.5:7b）。
  不可达时打印"未验证"，**绝不计入通过**。

运行：`python llm_test.py`
"""

from __future__ import annotations

import sys

from app.config import LLM_MODEL
from app.llm.client import LlmCallRecord, OllamaClient, OllamaUnavailable
from app.llm.guard import validate
from app.llm.service import LlmService

PASS = FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"✅ {name}")
    else:
        FAIL += 1
        print(f"❌ {name}  {detail}")


# ---------------------------------------------------------------- 离线替身

class FakeClient:
    """OllamaClient 替身。只实现 service 用到的三个方法。"""

    def __init__(self, response: str = "", healthy: bool = True,
                 raise_exc: Exception | None = None,
                 models: "list[str] | None" = None) -> None:
        self.model = LLM_MODEL
        self.host = "http://fake:11434"
        self._resp = response
        self._healthy = healthy
        self._exc = raise_exc
        self._models = models if models is not None else [LLM_MODEL]
        self.calls = 0

    def health(self, timeout_s: float = 3.0) -> bool:
        return self._healthy

    def list_models(self, timeout_s: float = 5.0) -> list[str]:
        return self._models

    def generate(self, prompt: str, system: str = "",
                 json_mode: bool = True) -> tuple[str, LlmCallRecord]:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        rec = LlmCallRecord(model=self.model, ok=True, duration_ms=12,
                            prompt_chars=len(prompt), response_chars=len(self._resp))
        return self._resp, rec


CAND = [{
    "rule_id": "R-S1-01", "cause": "USART1 外设时钟未使能", "score": 1.5,
    "evidences": [{"item": "RCC_APB2PeriphClockCmd 参数不含 RCC_APB2Periph_USART1",
                   "tag": "[静态分析]", "detail": "R-S1-01"}],
}]


def offline() -> None:
    print("—— 守卫（离线）——")
    cases = {
        "越权-实测标签": ('{"summary":"经实测确认[实测-硬件]"}', "rejected"),
        "越权-静态标签": ('{"summary":"这是[静态分析]结论"}', "rejected"),
        "越权-已验证": ('{"summary":"已验证该引脚配置无误"}', "rejected"),
        "越权-经实测": ('{"summary":"经实测该问题已解决"}', "rejected"),
        "越权-实测数值": ('{"summary":"实测波特率为9600"}', "rejected"),
        "越权-步骤内": ('{"summary":"需要检查时钟","extra_steps":["已实测波特率为115200"]}', "rejected"),
        "越权-多余字段": ('{"summary":"正常","evidence_tag":"[实测-硬件]"}', "rejected"),
        "非JSON": ('我觉得应该是时钟问题。', "rejected"),
        "空summary": ('{"summary":"   "}', "empty"),
        "合法": ('{"summary":"USART1 挂 APB2，未使能时钟时寄存器读写无效。",'
                 '"extra_steps":["在 RCC_APB2PeriphClockCmd 中加入 RCC_APB2Periph_USART1"],'
                 '"cautions":["USART1 与 USART2 分属不同总线"]}', "accepted"),
    }
    for name, (raw, want) in cases.items():
        r = validate(raw)
        check(f"守卫-{name}", r["verdict"] == want,
              f"got={r['verdict']} want={want}")

    print("—— 降级链（离线替身）——")
    good = '{"summary":"时钟未使能导致寄存器读写无效。","extra_steps":["补充使能后重新编译"],"cautions":[]}'
    s1 = LlmService(client=FakeClient(good))             # type: ignore[arg-type]
    n1 = s1.narrate("串口无输出", CAND, [], [])
    check("正常输出 → available 且非降级", n1.available and not n1.degraded,
          n1.reason)
    check("输出标签恒为 [推断]", n1.tag == "[推断]", n1.tag)
    check("字段解析正确", len(n1.extra_steps) == 1 and bool(n1.summary))

    s2 = LlmService(client=FakeClient('{"summary":"已实测确认无误"}'))  # type: ignore[arg-type]
    n2 = s2.narrate("串口无输出", CAND, [], [])
    check("越权输出 → 降级且不外泄", (not n2.available) and n2.degraded
          and "守卫" in n2.reason, n2.reason)
    check("越权输出不得进入 summary", n2.summary == "", n2.summary)

    s3 = LlmService(client=FakeClient("这不是 JSON"))     # type: ignore[arg-type]
    n3 = s3.narrate("串口无输出", CAND, [], [])
    check("非 JSON → 降级", n3.degraded and "结构" in n3.reason, n3.reason)

    s4 = LlmService(client=FakeClient("", raise_exc=OllamaUnavailable("连接被拒")))  # type: ignore[arg-type]
    n4 = s4.narrate("串口无输出", CAND, [], [])
    check("调用异常 → 降级为规则层", n4.degraded and "降级" in n4.reason, n4.reason)
    check("调用异常不抛给上层", True)

    s5 = LlmService(client=FakeClient(good, healthy=False))  # type: ignore[arg-type]
    n5 = s5.narrate("串口无输出", CAND, [], [])
    check("服务不可达 → 降级", n5.degraded and "不可达" in n5.reason, n5.reason)

    s6 = LlmService(client=FakeClient(good), enabled=False)  # type: ignore[arg-type]
    n6 = s6.narrate("串口无输出", CAND, [], [])
    check("未启用 → 降级且不调用模型", n6.degraded and "未启用" in n6.reason,
          n6.reason)
    check("未启用时不产生任何调用", s6.client.calls == 0, str(s6.client.calls))  # type: ignore[attr-defined]

    s7 = LlmService(client=FakeClient('{"summary":"   "}'))  # type: ignore[arg-type]
    n7 = s7.narrate("串口无输出", CAND, [], [])
    check("空解释 → 降级", n7.degraded, n7.reason)

    # 降级链顺序：云端未配置时不得假装接入
    s8 = LlmService(client=FakeClient(good))             # type: ignore[arg-type]
    check("云端未配置即视为未接入（不虚报）", s8._cloud_ready() is False)


def online() -> None:
    print("—— 在线（本机 Ollama）——")
    svc = LlmService()
    ok, why = svc.probe(force=True)
    if not ok:
        print(f"⚠️  本机模型不可用（{why}），在线用例**未验证**，不计入通过。")
        return
    print(f"   模型 {svc.client.model} 在线，开始真实调用…")
    n = svc.narrate("串口完全没有输出，但板载LED还在闪烁", CAND, [], [])
    check("真实调用产出解读", bool(n.summary), n.reason)
    check("真实调用标签为 [推断]", n.tag == "[推断]", n.tag)
    check("调用元数据完整", bool(n.call.get("model"))
          and n.call.get("duration_ms", 0) > 0, str(n.call)[:120])
    import re
    blob = " ".join([n.summary] + n.extra_steps + n.cautions)
    bad = re.findall(r"\[\s*(?:实测|静态分析|推断|假设|待验证)[^\[\]]*\]"
                     r"|(?:已|已经|已?经)(?:过)?(?:实测|上板|烧录)", blob)
    check("真实输出无越权表述", not bad, str(bad[:3]))


if __name__ == "__main__":
    offline()
    online()
    print()
    print(f"结果：通过 {PASS} · 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)
