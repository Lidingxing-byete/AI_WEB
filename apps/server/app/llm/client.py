"""本地 Ollama 客户端（仅标准库，不引入任何第三方依赖）。

为什么自己写而不用 SDK
----------------------
1. 赛事要求"须能解释全部代码逻辑"——Ollama 的 HTTP API 本身很简单，
   用 stdlib 直连比多一层 SDK 更容易讲清楚，也少一个依赖。
2. 依赖白名单限制：本项目不新增 pip 包，`urllib.request` 足够。

两个必须处理的工程细节
----------------------
1. **系统代理**：本机有 HTTP 代理环境变量，`urllib` 默认会走代理去连
   `127.0.0.1`，直接返回 502。这里显式用 `ProxyHandler({})` 绕过——
   这个坑在 Phase 3 联调时已经踩过一次。
2. **冷启动**：7B Q4 权重约 4.7GB，首次调用要把模型载入显存，耗时可达
   数十秒。因此超时默认 120s，并且**超时/失败一律降级**，不掀翻诊断。

本模块只负责"把请求发出去、把原始文本拿回来"。
**它不判断内容对错**——内容是否越权由 `guard.py` 判定。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..config import (LLM_MAX_TOKENS, LLM_MODEL, LLM_TEMPERATURE,
                      LLM_TIMEOUT_S, OLLAMA_HOST)

# 绕过系统代理直连本机（否则 urllib 会把 127.0.0.1 也发给代理）
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PROVIDER_LOCAL = "ollama-local"


class OllamaUnavailable(RuntimeError):
    """服务不可达 / 模型不存在 / 超时 / 返回非 200。

    调用方必须捕获并降级，绝不能让它冒到编排层。
    """


@dataclass
class LlmCallRecord:
    """一次模型调用的元数据。

    PROJECT_PLAN 第六节要求"记录模型名称、关键参数、调用错误和必要的
    诊断过程元数据"，本结构就是落库对象（`llm_calls` 表）。
    """

    provider: str = PROVIDER_LOCAL
    model: str = LLM_MODEL
    ok: bool = False
    duration_ms: int = 0
    error: str = ""
    prompt_chars: int = 0
    response_chars: int = 0
    eval_count: int | None = None      # Ollama 返回的生成 token 数
    temperature: float = LLM_TEMPERATURE
    num_predict: int = LLM_MAX_TOKENS
    raw_response: str = ""

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "ok": self.ok,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "prompt_chars": self.prompt_chars,
            "response_chars": self.response_chars,
            "eval_count": self.eval_count,
            "temperature": self.temperature,
            "num_predict": self.num_predict,
        }


class OllamaClient:
    """Ollama HTTP 客户端（`/api/generate` 非流式）。"""

    def __init__(
        self,
        host: str = OLLAMA_HOST,
        model: str = LLM_MODEL,
        timeout_s: float = LLM_TIMEOUT_S,
        temperature: float = LLM_TEMPERATURE,
        num_predict: int = LLM_MAX_TOKENS,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.num_predict = num_predict

    # ------------------------------------------------------------------
    def health(self, timeout_s: float = 3.0) -> bool:
        """服务是否可达。**轻量探测**，不加载模型。"""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with _NO_PROXY.open(req, timeout=timeout_s) as resp:
                return resp.status == 200
        except Exception:                                  # noqa: BLE001
            return False

    def list_models(self, timeout_s: float = 5.0) -> list[str]:
        """返回已拉取的模型名。**只读**，不下载任何模型。"""
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with _NO_PROXY.open(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception:                                  # noqa: BLE001
            return []

    # ------------------------------------------------------------------
    def generate(
        self,
        prompt: str,
        system: str = "",
        json_mode: bool = True,
    ) -> tuple[str, LlmCallRecord]:
        """调用 /api/generate，返回 (原始文本, 调用记录)。

        任何异常都会被包装成 `OllamaUnavailable` 抛出，同时记录填入
        `LlmCallRecord.error`——**调用方据此降级**。
        """
        rec = LlmCallRecord(model=self.model, temperature=self.temperature,
                            num_predict=self.num_predict, prompt_chars=len(prompt))
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_predict": self.num_predict,
            },
        }
        if system:
            payload["system"] = system
        if json_mode:
            # 让 Ollama 在解码阶段约束输出，提高结构化率；
            # 但不保证一定合法，因此 guard 侧仍必须校验。
            payload["format"] = "json"

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t0 = time.perf_counter()
        try:
            with _NO_PROXY.open(req, timeout=self.timeout_s) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            rec.duration_ms = int((time.perf_counter() - t0) * 1000)
            rec.error = f"HTTP {exc.code}: {exc.reason}"
            raise OllamaUnavailable(rec.error) from exc
        except Exception as exc:                           # noqa: BLE001
            rec.duration_ms = int((time.perf_counter() - t0) * 1000)
            rec.error = f"{type(exc).__name__}: {exc}"
            raise OllamaUnavailable(rec.error) from exc
        rec.duration_ms = int((time.perf_counter() - t0) * 1000)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            rec.error = f"响应非 JSON：{raw[:120]}"
            raise OllamaUnavailable(rec.error) from exc

        if data.get("error"):
            rec.error = str(data["error"])[:300]
            raise OllamaUnavailable(rec.error)

        text = str(data.get("response") or "")
        rec.ok = True
        rec.response_chars = len(text)
        rec.raw_response = text
        if isinstance(data.get("eval_count"), int):
            rec.eval_count = data["eval_count"]
        return text, rec
