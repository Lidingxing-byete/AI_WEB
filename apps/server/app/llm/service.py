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
                      LLM_MODEL, LLM_PREFERRED, OLLAMA_HOST)
from ..engine.evidence import EvidenceTag
from . import providers as provider_catalog
from .client import (PROVIDER_CLOUD, PROVIDER_LOCAL, CloudClient,
                     CloudUnavailable, OllamaClient, OllamaUnavailable, mask_key)
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
    """模型服务。构造极轻，不自动联网。

    `preferred` 决定来源偏好（与前端选择器一致）：
      - `local`：固定本地 Ollama；
      - `cloud`：固定云端 DeepSeek（失败自动降级本地）；
      - `auto`：云端就绪（配置完整）优先，否则本地。
    任何来源失败都会继续尝试下一个，最终不可用才降级规则层。
    """

    def __init__(self, client: OllamaClient | None = None,
                 cloud_client: CloudClient | None = None,
                 enabled: bool | None = None,
                 preferred: str | None = None,
                 cloud_key: str = "",
                 cloud_model: str = "",
                 cloud_provider: str = "") -> None:
        self.enabled = LLM_ENABLED if enabled is None else enabled
        self.client = client or OllamaClient()
        self.preferred = (preferred or LLM_PREFERRED).strip().lower()
        # 测试者自带的密钥：**只在本次请求内有效**，不落盘、不缓存到下次请求。
        self._key_override = (cloud_key or "").strip()
        self._model_override = (cloud_model or "").strip()
        self._provider_override = (cloud_provider or "").strip().lower()
        self._cloud_client = cloud_client
        self._cloud_injected = cloud_client is not None
        self._cloud_cfg_sig = ""
        self._health_at = 0.0
        self._health_ok = False
        self._cloud_health_at = 0.0
        self._cloud_health_ok = False
        self._models: list[str] = []

    # ------------------------------------------------------------------
    def cloud_config(self) -> dict:
        """当前生效的云端配置。优先级：**测试者自带的本次请求密钥** > 服务端环境变量。

        只在本进程内使用；`api_key` 是明文，调用方绝对不得外传
        （对外一律用 `masked`）。
        """
        api_key = self._key_override or CLOUD_API_KEY
        # 服务商：请求头 > 环境变量 > 默认（deepseek）。
        # 未知 id 一律回落到默认服务商，绝不把任意字符串当地址用。
        requested = self._provider_override or CLOUD_PROVIDER
        prov = provider_catalog.resolve(requested) or provider_catalog.default_provider()
        # base_url 只能来自服务端白名单（或管理员在 .env 里显式指定的镜像），
        # 绝不接受请求方传入 —— 否则本服务会变成任意地址的代理（SSRF）。
        base_url = prov.base_url
        if CLOUD_BASE_URL and requested in ("", CLOUD_PROVIDER) and prov.id == CLOUD_PROVIDER:
            base_url = CLOUD_BASE_URL
        model = self._model_override or (
            CLOUD_MODEL if prov.id == CLOUD_PROVIDER and CLOUD_MODEL else prov.default_model
        )
        if self._key_override:
            source = "session"
        elif CLOUD_API_KEY:
            source = "env"
        else:
            source = "none"
        return {
            "provider": prov.id,
            "provider_label": prov.label,
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "source": source,
            "masked": mask_key(api_key),
            "updated_at": None,
        }

    def _cloud_ready(self) -> bool:
        """云端是否可用：显式注入的客户端视为就绪（测试用）；
        运行时默认路径要求**配置完整**——缺任意一项即视为未启用（不猜、不半配）。"""
        if self._cloud_injected:
            return True
        cfg = self.cloud_config()
        return bool(cfg["provider"] and cfg["base_url"] and cfg["model"]
                    and cfg["api_key"])

    def _get_cloud_client(self) -> CloudClient | None:
        """惰性构造云端客户端：只有就绪才创建（不配置绝不联网）。

        配置签名变了（手动改/删密钥）就重建客户端，避免拿着旧密钥
        一直失败——手工改完密钥应该立刻生效，不该要求重启服务。
        """
        if self._cloud_injected:
            return self._cloud_client
        if not self._cloud_ready():
            # 密钥被删除：清掉旧客户端，立刻回到"未配置"
            self._cloud_client = None
            self._cloud_cfg_sig = ""
            return None
        cfg = self.cloud_config()
        sig = f"{cfg['base_url']}|{cfg['model']}|{cfg['api_key']}"
        if self._cloud_client is not None and self._cloud_cfg_sig == sig:
            return self._cloud_client
        self._cloud_client = CloudClient(
            base_url=cfg["base_url"], api_key=cfg["api_key"], model=cfg["model"],
            provider_id=cfg["provider"])
        self._cloud_cfg_sig = sig
        return self._cloud_client

    def _order(self) -> list[str]:
        """按偏好计算尝试顺序：['cloud'|'local', ...]，最终必含 'local'。"""
        cloud_ok = self._cloud_ready()
        if self.preferred == "cloud":
            return ["cloud", "local"]
        if self.preferred == "local":
            return ["local"]
        return ["cloud", "local"] if cloud_ok else ["local"]

    # ------------------------------------------------------------------
    def probe(self, force: bool = False) -> tuple[bool, str]:
        """本地模型可用性探测，带短期缓存（避免每轮诊断都打一次 /api/tags）。"""
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

    def cloud_probe(self, force: bool = False) -> tuple[bool, str]:
        """云端可用性探测（鉴权 + 可达，GET /models，不触发计费推理）。"""
        if not self._cloud_ready():
            return False, "云端未配置（XZ_CLOUD_API_KEY 留空）"
        now = time.time()
        if not force and now - self._cloud_health_at < LLM_HEALTH_CACHE_S:
            return self._cloud_health_ok, ("" if self._cloud_health_ok
                                           else "云端服务不可达")
        cc = self._get_cloud_client()
        ok = bool(cc and cc.health())
        self._cloud_health_at = now
        self._cloud_health_ok = ok
        if not ok:
            return False, f"云端服务不可达（{CLOUD_BASE_URL}）"
        return True, ""

    # ------------------------------------------------------------------
    def _narrate_via(
        self,
        client: OllamaClient | CloudClient,
        provider_label: str,
        symptom_text: str,
        candidates: list[dict],
        unknowns: list[dict],
        references: list[dict],
    ) -> LlmNarrative:
        """用指定客户端执行一次模型解读（同一 prompt / 同一守卫）。"""
        prompt = build_narrate_prompt(symptom_text, candidates,
                                      unknowns, references)
        t0 = time.perf_counter()
        try:
            raw, rec = client.generate(prompt, system=NARRATE_SYSTEM,
                                       json_mode=True)
        except (OllamaUnavailable, CloudUnavailable) as exc:
            return LlmNarrative(
                False, True, f"{provider_label} 调用失败：{exc}",
                provider=provider_label, model=client.model,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                call={"ok": False, "error": str(exc)[:200]},
            )
        except Exception as exc:                          # noqa: BLE001
            return LlmNarrative(
                False, True, f"{provider_label} 调用异常："
                             f"{type(exc).__name__}: {exc}",
                provider=provider_label, model=client.model,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        checked = validate(raw)
        if checked["verdict"] != VERDICT_OK:
            # 守卫拒绝：这**不是**"模型出错"，而是"模型输出越界"。
            # 如实记录原因，降级显示规则层结论。
            return LlmNarrative(
                False, True,
                f"{provider_label} 输出未通过证据纪律守卫"
                f"（{checked['reason']}），已丢弃该段输出，仅展示规则层结论",
                provider=provider_label, model=rec.model,
                duration_ms=rec.duration_ms, call=rec.to_dict(),
            )

        return LlmNarrative(
            True, False, "", provider=provider_label,
            model=rec.model, duration_ms=rec.duration_ms,
            summary=checked["summary"],
            extra_steps=checked["extra_steps"],
            cautions=checked["cautions"],
            call=rec.to_dict(),
        )

    def narrate(
        self,
        symptom_text: str,
        candidates: list[dict],
        unknowns: list[dict],
        references: list[dict],
    ) -> LlmNarrative:
        """对已查出的结论做解释，并补充验证建议。

        按偏好顺序尝试（云端→本地），**失败一律降级**：
        返回 degraded 对象并写明原因，绝不抛异常。
        """
        if not self.enabled:
            return LlmNarrative(False, True, "模型未启用（XZ_LLM_ENABLED=0）")

        attempts: list[str] = []
        for p in self._order():
            if p == "cloud":
                cc = self._get_cloud_client()
                if cc is None:
                    attempts.append("云端未配置（跳过）")
                    continue
                out = self._narrate_via(cc, PROVIDER_CLOUD, symptom_text,
                                        candidates, unknowns, references)
            else:
                ok, why = self.probe()
                if not ok:
                    attempts.append(why)
                    continue
                out = self._narrate_via(self.client, PROVIDER_LOCAL,
                                        symptom_text, candidates,
                                        unknowns, references)
            if out.available:
                return out
            attempts.append(out.reason or f"{out.provider} 无输出")

        return LlmNarrative(
            False, True, f"模型解读不可用，已降级为规则层输出："
                         f"{'；'.join(a for a in attempts if a)}",
        )

    def status(self, probe: bool = True) -> dict:
        """本地 + 云端双状态，供 /api/llm/status 与前端面板展示。

        **只回掩码**：`key_masked` 形如 `sk-12…ab90`，明文密钥永远不出这个进程。
        """
        l_ok, l_why = self.probe(force=probe)
        cfg = self.cloud_config()
        cloud_ready = self._cloud_ready()
        c_ok, c_why = self.cloud_probe(force=probe) if cloud_ready \
            else (False, ("云端未配置（密钥未填写）" if cfg["source"] == "none"
                          else "云端配置不完整"))
        return {
            "enabled": self.enabled,
            "provider": PROVIDER_LOCAL,
            "host": OLLAMA_HOST,
            "model": LLM_MODEL,
            "available": l_ok,
            "reason": l_why,
            "models": self.client.list_models() if l_ok else [],
            "preferred": self.preferred,
            "active_hint": ("cloud" if (self.preferred in ("cloud", "auto")
                                        and cloud_ready and c_ok) else "local"),
            "cloud_provider": cfg["provider"],
            "cloud_provider_label": cfg["provider_label"],
            "cloud": {
                "provider": cfg["provider"],
                "base_url": cfg["base_url"],
                "model": cfg["model"],
                "configured": cloud_ready,
                "available": c_ok,
                "reason": c_why,
                # 手动添加 / 环境变量 / 未配置——前端据此显示来源
                "key_source": cfg["source"],
                "key_masked": cfg["masked"],
                "updated_at": cfg["updated_at"],
            },
            "note": ("模型仅用于解释已查出的结论与补充验证建议，"
                     "输出恒为 [推断]，不参与根因判定；不可用时自动降级"),
        }

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


def default_service(preferred: str | None = None) -> LlmService:
    return LlmService(preferred=preferred)
