"""模型层测试（离线优先）。

分两部分：
- **离线用例**：用一个假 client 替身，不联网也能跑。覆盖守卫、
  降级链、字段白名单——这些是"模型可控性"的核心，必须每次回归都验。
- **在线用例**：本机 Ollama 可达时才跑（真实默认模型，`XZ_LLM_MODEL`）。
  不可达时打印"未验证"，**绝不计入通过**。

运行：`python llm_test.py`
"""

from __future__ import annotations

import json
import sys

from app.config import LLM_MODEL
from app.llm.client import CloudUnavailable, LlmCallRecord, OllamaClient, OllamaUnavailable
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


class FakeCloudClient:
    """DeepSeek 云端客户端替身。仅测试注入用（注入视为就绪）。"""

    def __init__(self, response: str = "", healthy: bool = True,
                 raise_exc: Exception | None = None,
                 model: str = "deepseek-chat") -> None:
        self.model = model
        self.base_url = "https://fake.deepseek"
        self._resp = response
        self._healthy = healthy
        self._exc = raise_exc
        self.calls = 0

    def health(self, timeout_s: float = 5.0) -> bool:
        return self._healthy

    def generate(self, prompt: str, system: str = "",
                 json_mode: bool = True) -> tuple[str, LlmCallRecord]:
        self.calls += 1
        if self._exc is not None:
            raise self._exc
        rec = LlmCallRecord(provider="deepseek-cloud", model=self.model,
                            ok=True, duration_ms=15, prompt_chars=len(prompt),
                            response_chars=len(self._resp))
        return self._resp, rec


def cloud() -> None:
    """云端（DeepSeek）切换与降级链（离线替身）。"""
    print("—— 云端切换与降级（离线替身）——")
    good = '{"summary":"云端：时钟未使能导致寄存器读写无效。","extra_steps":[],"cautions":[]}'
    bad = "不是 JSON"

    c1 = LlmService(client=FakeClient(good),                    # type: ignore[arg-type]
                    cloud_client=FakeCloudClient(good),         # type: ignore[arg-type]
                    preferred="auto")
    n1 = c1.narrate("串口无输出", CAND, [], [])
    check("auto+云端就绪 → 走云端", n1.available and n1.provider == "deepseek-cloud",
          n1.provider)
    check("云端输出恒为 [推断]", n1.tag == "[推断]", n1.tag)

    c2 = LlmService(client=FakeClient(good),                    # type: ignore[arg-type]
                    cloud_client=FakeCloudClient(raise_exc=CloudUnavailable("401 Unauthorized")),
                    preferred="auto")                            # type: ignore[arg-type]
    n2 = c2.narrate("串口无输出", CAND, [], [])
    check("云端失败 → 自动降级本地", n2.available and n2.provider == "ollama-local",
          n2.provider)
    check("云端确实被尝试过", c2._cloud_client.calls == 1,   # type: ignore[union-attr]
          str(c2._cloud_client.calls))                       # type: ignore[union-attr]

    c3 = LlmService(client=FakeClient(good),                    # type: ignore[arg-type]
                    cloud_client=FakeCloudClient(good),         # type: ignore[arg-type]
                    preferred="local")
    n3 = c3.narrate("串口无输出", CAND, [], [])
    check("preferred=local → 只用本地", n3.provider == "ollama-local",
          n3.provider)
    check("local 时不碰云端", c3._cloud_client.calls == 0,  # type: ignore[union-attr]
          str(c3._cloud_client.calls))                       # type: ignore[union-attr]

    c4 = LlmService(client=FakeClient("", raise_exc=OllamaUnavailable("本地拒绝")),  # type: ignore[arg-type]
                    cloud_client=FakeCloudClient(raise_exc=CloudUnavailable("云端超时")),
                    preferred="cloud")                        # type: ignore[arg-type]
    n4 = c4.narrate("串口无输出", CAND, [], [])
    check("双端失败 → 降级规则层且记录两端原因", n4.degraded
          and "云端超时" in n4.reason and "本地拒绝" in n4.reason, n4.reason)

    c5 = LlmService(client=FakeClient(good),                    # type: ignore[arg-type]
                    cloud_client=FakeCloudClient(good),         # type: ignore[arg-type]
                    preferred="auto")
    st = c5.status(probe=True)
    check("status 含云端配置与可用态", st["cloud"]["configured"]
          and st["cloud"]["available"] and st["active_hint"] == "cloud",
          str(st["cloud"]))
    check("status 含偏好字段", st["preferred"] == "auto", st["preferred"])

    c6 = LlmService(client=FakeClient(good),                    # type: ignore[arg-type]
                    cloud_client=FakeCloudClient(good),         # type: ignore[arg-type]
                    preferred="local")
    check("preferred=local → active_hint=local",
          c6.status(probe=True)["active_hint"] == "local",
          c6.status(probe=True)["active_hint"])

    c7 = LlmService(client=FakeClient(good),                    # type: ignore[arg-type]
                    cloud_client=FakeCloudClient(healthy=False),
                    preferred="auto")                           # type: ignore[arg-type]
    ok, why = c7.cloud_probe(force=True)
    check("云端探测失败如实报告", (not ok) and "不可达" in why, why)


def keys() -> None:
    """测试者自带密钥（离线）：只认本次请求、对外只回掩码、绝不落盘。"""
    print("—— 测试者自带密钥 ——")
    from app.llm.client import mask_key
    check("掩码只露首尾", mask_key("sk-unit-test-abcdef123456") == "sk-u…3456",
          mask_key("sk-unit-test-abcdef123456"))
    check("空密钥掩码为空", mask_key("") == "")

    svc = LlmService(cloud_key="sk-unit-test-abcdef123456",
                     cloud_model="deepseek-chat")
    c = svc.cloud_config()
    check("随请求密钥来源标 session",
          c["source"] == "session" and c["model"] == "deepseek-chat",
          str(c["source"]))
    check("配置齐全时云端视为就绪", svc._cloud_ready() is True)

    # 不真的打网络：把探测替换掉，只验证“status 不泄露明文”
    svc.cloud_probe = lambda force=False: (False, "离线用例跳过探测")  # type: ignore[method-assign]
    st = svc.status(probe=False)
    check("status 中不含明文密钥",
          "sk-unit-test-abcdef123456" not in json.dumps(st, ensure_ascii=False),
          str(st.get("cloud", {}).get("key_masked")))
    check("status 标出密钥来源 session",
          st.get("cloud", {}).get("key_source") == "session",
          str(st.get("cloud", {}).get("key_source")))

    check("不传密钥时来源不是 session",
          LlmService().cloud_config()["source"] != "session")

    # ---- 多服务商（2026-10-26）----
    from app.llm import providers as pc
    check("服务商目录至少 6 家", len(pc.catalog()) >= 6, str(len(pc.catalog())))
    check("未知服务商解析为 None（不猜）", pc.resolve("no-such-provider") is None)

    svc_o = LlmService(cloud_key="sk-unit-test-abcdef123456",
                       cloud_provider="openai")
    c_o = svc_o.cloud_config()
    check("切换服务商后 base_url 来自白名单",
          c_o["provider"] == "openai" and "api.openai.com" in c_o["base_url"],
          c_o["base_url"])
    check("模型默认值随服务商", c_o["model"] == "gpt-4o-mini", c_o["model"])

    svc_o.cloud_probe = lambda force=False: (False, "离线用例跳过探测")  # type: ignore[method-assign]
    st_o = svc_o.status(probe=False)
    check("status 标注服务商 id 与名称",
          st_o["cloud_provider"] == "openai"
          and "OpenAI" in str(st_o.get("cloud_provider_label")),
          f"{st_o['cloud_provider']} / {st_o.get('cloud_provider_label')}")


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
    cloud()
    keys()
    online()
    print()
    print(f"结果：通过 {PASS} · 失败 {FAIL}")
    sys.exit(1 if FAIL else 0)
