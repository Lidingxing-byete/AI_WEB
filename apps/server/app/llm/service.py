"""模型服务：三级降级链的统一入口。

降级链（Phase 1 ADR-03）
-----------------------
    ① 云端 provider（可配置，默认不启用，需环境变量提供密钥）
    ② 本地 Ollama（默认，127.0.0.1:11434，用已拉取模型）
    ③ 规则层兜底（模型不可用时，系统行为与接入前**完全一致**）

关键设计
--------
1. **`narrate()` 永不抛异常**。任何失败都返回 `degraded=True` 的对象，
   并写明降级原因。模型出问题不应该让诊断失败——这正是"模型是增强
   而非依赖"的体现，也是答辩时可以现场拔网线演示的一点。
2. **模型没有诊断权**。输出经 `guard.validate` 后只取
   `summary / extra_steps / cautions` 三个字段，模型无法新增候选根因、
   无法改写评分、无法标注证据等级。
3. **输出恒标 `[推断]`**。模型说的话永远不是实测，也不是静态检查结论。

`baseline()` 仅用于评测（纯 LLM 基线对比），不进主流程。
"""

from __future__ import annotations

import json
import time

from ..config import (CLOUD_API_KEY, CLOUD_BASE_URL, CLOUD_MODEL,
                      CLOUD_PROVIDER, LLM_ENABLED, LLM_HEALTH_CACHE_S,
                      LLM_MODEL)
from ..engine.evidence import EvidenceTag
from .client import PROVIDER_LOCAL, OllamaClient, OllamaUnavailable
from .guard import VERDICT_OK, validate
from .prompts import (BASELINE_SYSTEM, NARRATE_SYSTEM,
                      build_baseline_prompt, build_narrate_prompt)

TAG_INFERRED = EvidenceTag.INFERRED.value


class LlmNarrative:
    """模型解读结果。**无论如何都会返回一个实例**，失败时 degraded=True。"""

    def __init__(
        self,
        available: bool = False,
        degraded: bool = True,
        reason: str = "未调用",
        provider: str = "",
        model: str = "",
        duration_ms: int = 0,
        summary: str = "",
        extra_steps: "list[str] | None" = None,
        cautions: "list[str] | None" = None,
        call: dict | None = None,
    ) -> None:
        self.available = available
        self.degraded = degraded
        self.reason = reason
        self.provider = provider
        self.model = model
        self.duration_ms = duration_ms
        self.summary = summary
        self.extra_steps = extra_steps or []
        self.cautions = cautions or []
        self.call = call or {}
        # 模型输出**恒为推断**：它既不是实测，也不是静态检查结论
        self.tag = TAG_INFERRED

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "degraded": self.degraded,
            "reason": self.reason,
            "provider": self.provider,
            "model": self.model,
            "duration_ms": self.duration_ms,
            "summary": self.summary,
            "extra_steps": self.extra_steps,
            "cautions": self.cautions,
            "call": self.call,
            "tag": self.tag,
        }


class LlmService:
    """模型服务。构造极轻，不自动联网。"""

    def __init__(self, client: OllamaClient | None = None,
                 enabled: bool | None = None) -> None:
        self.enabled = LLM_ENABLED if enabled is None else enabled
        self.client = client or OllamaClient()
        self._health_at = 0.0
        self._health_ok = False
        self._models: list[str] = []

    # ------------------------------------------------------------------
    def _cloud_ready(self) -> bool:
        """云端是否**配置完整**。缺任意一项即视为未启用（不猜、不半配）。"""
        return bool(CLOUD_PROVIDER and CLOUD_BASE_URL and CLOUD_MODEL
                    and CLOUD_API_KEY)

    def probe(self, force: bool = False) -> tuple[bool, str]:
        """探测模型可用性，带短期缓存（避免每轮诊断都打一次 /api/tags）。"""
        if not self.enabled:
            return False, "模型未启用（XZ_LLM_ENABLED=0）"
        now = time.time()
        if not force and now - self._health_at < LLM_HEALTH_CACHE_S:
            return self._health_ok, ("" if self._health_ok
                                     else "本地模型服务不可达")
        ok = self.client.health()
        self._health_at = now
        self._health_ok = ok
        if not ok:
            return False, f"本地模型服务不可达（{self.client.host}）"
        models = self.client.list_models()
        self._models = models
        if models and self.client.model not in models:
            # 只提示、不自动下载（下载模型需单独批准）
            return False, (f"未找到模型 {self.client.model}；"
                           f"本机已拉取：{', '.join(models[:6])}")
        return True, ""

    # ------------------------------------------------------------------
    def narrate(
        self,
        symptom_text: str,
        candidates: list[dict],
        unknowns: list[dict],
        references: list[dict],
    ) -> LlmNarrative:
        """对已查出的结论做解释，并补充验证建议。

        失败一律降级：返回 degraded 对象并写明原因，绝不抛异常。
        """
        ok, why = self.probe()
        if not ok:
            return LlmNarrative(False, True, why, model=self.client.model)

        prompt = build_narrate_prompt(symptom_text, candidates,
                                      unknowns, references)
        t0 = time.perf_counter()
        try:
            raw, rec = self.client.generate(prompt, system=NARRATE_SYSTEM,
                                            json_mode=True)
        except OllamaUnavailable as exc:
            return LlmNarrative(
                False, True, f"模型调用失败，已降级为规则层输出：{exc}",
                provider=PROVIDER_LOCAL, model=self.client.model,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                call={"ok": False, "error": str(exc)[:200]},
            )
        except Exception as exc:                          # noqa: BLE001
            return LlmNarrative(
                False, True, f"模型调用异常，已降级为规则层输出："
                             f"{type(exc).__name__}: {exc}",
                provider=PROVIDER_LOCAL, model=self.client.model,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        checked = validate(raw)
        if checked["verdict"] != VERDICT_OK:
            # 守卫拒绝：这**不是**"模型出错"，而是"模型输出越界"。
            # 如实记录原因，降级显示规则层结论。
            return LlmNarrative(
                False, True,
                f"模型输出未通过证据纪律守卫（{checked['reason']}），"
                f"已丢弃该段输出，仅展示规则层结论",
                provider=PROVIDER_LOCAL, model=self.client.model,
                duration_ms=rec.duration_ms, call=rec.to_dict(),
            )

        return LlmNarrative(
            True, False, "", provider=PROVIDER_LOCAL,
            model=self.client.model, duration_ms=rec.duration_ms,
            summary=checked["summary"],
            extra_steps=checked["extra_steps"],
            cautions=checked["cautions"],
            call=rec.to_dict(),
        )

    # ------------------------------------------------------------------
    def baseline(self, source_code: str, symptom_text: str,
                 rule_catalog: list[dict], build_log: str = "") -> dict:
        """纯 LLM 基线（评测专用，不进主流程）。

        返回 {ok, rule_id, reason, error, duration_ms, model}。
        模型不可用或输出非法时 `ok=False` 并写明原因——**不猜、不补**。
        """
        ok, why = self.probe()
        if not ok:
            return {"ok": False, "rule_id": None, "reason": "", "error": why,
                    "duration_ms": 0, "model": self.client.model}
        # 编译日志必须与规则层一样可见，否则对比不公平
        prompt = build_baseline_prompt(source_code, symptom_text,
                                       rule_catalog, build_log=build_log)
        try:
            raw, rec = self.client.generate(prompt, system=BASELINE_SYSTEM,
                                            json_mode=True)
        except Exception as exc:                          # noqa: BLE001
            return {"ok": False, "rule_id": None, "reason": "",
                    "error": str(exc)[:200], "duration_ms": 0,
                    "model": self.client.model}

        from .guard import extract_json                   # 局部导入避免循环
        obj, err = extract_json(raw)
        if obj is None:
            return {"ok": False, "rule_id": None, "reason": raw[:200],
                    "error": err, "duration_ms": rec.duration_ms,
                    "model": self.client.model}
        rid = str(obj.get("rule_id") or "").strip()
        allowed = {r["rule_id"] for r in rule_catalog} | {"INSUFFICIENT"}
        if rid not in allowed:
            # 模型编了一个不存在的编号 → 记为失败，不做模糊匹配挽救
            return {"ok": False, "rule_id": rid, "reason": str(obj.get("reason") or ""),
                    "error": f"输出了无效的 rule_id：{rid}",
                    "duration_ms": rec.duration_ms, "model": self.client.model}
        return {"ok": True, "rule_id": rid,
                "reason": str(obj.get("reason") or "")[:400],
                "error": "", "duration_ms": rec.duration_ms,
                "model": self.client.model}


def default_service() -> LlmService:
    return LlmService()
