"""模型输出守卫（AI 安全与可控性的核心）。

为什么必须有这一层
------------------
本项目最高红线是"禁止伪造实测结果"（AGENTS.md 第四节）。
模型天然会写出"经实测确认……""已验证该配置正确"这类句子——
一旦直接进入证据链，就等于**用模型的语言流畅度伪造了硬件实测**，
这正是 PROJECT_PLAN 四·核心流程里明令禁止的行为。

因此模型输出**在离开本模块之前不算证据**，必须先过四道闸：

    ① 结构化闸：必须是 JSON，字段必须在白名单内
    ② 证据越权闸：出现证据标签或"已实测/已验证"类表述 → 整段作废
    ③ 事实锚定闸：解释必须能落到本次真实查出的检查项上
    ④ 长度闸：防止把长篇输出塞进证据链

任一闸不通过 → 返回 `verdict="rejected"` 并写明原因，
调用方**降级为规则层输出**，而不是把可疑内容包装成结论。

本模块**不做**的事：不判断技术内容是否正确（那需要人工/硬件），
只判断它有没有越过证据纪律的边界。
"""

from __future__ import annotations

import json
import re

from ..config import LLM_MAX_STEP_CHARS, LLM_MAX_STEPS, LLM_MAX_SUMMARY_CHARS

VERDICT_OK = "accepted"
VERDICT_REJECTED = "rejected"
VERDICT_EMPTY = "empty"

# ① 允许出现的字段白名单。模型不得塞入其它字段（尤其不得塞证据标签）
ALLOWED_KEYS = {"summary", "extra_steps", "cautions"}

# ② 证据越权表述。命中即整段作废 —— 这些词意味着模型在替系统下证据结论。
# 说明：早期版本给"已验证/已确认"加了后缀白名单（通过|成功|正确），
# 实测下来"已验证该引脚配置无误"这类写法能绕过，因此改为不限后缀——
# 正常解释几乎不会以"已验证""已确认"开头，宁可严格。
_FORBIDDEN_PATTERNS: list[tuple[str, str]] = [
    (r"\[\s*实测[^\[\]]*\]", "擅自使用证据标签（实测类）"),
    (r"\[\s*静态分析\s*\]", "擅自使用证据标签（静态分析）"),
    (r"\[\s*推断\s*\]", "擅自使用证据标签（推断）"),
    (r"\[\s*假设\s*\]", "擅自使用证据标签（假设）"),
    (r"\[\s*待验证\s*\]", "擅自使用证据标签（待验证）"),
    # "经实测""已经实测""已上板"都要拦住，不能只认"已实测"
    (r"(?:已|已经|已?经)(?:过)?(?:实测|上板|烧录|下载验证)", "声称已完成硬件实测"),
    (r"(?:经过|通过)(?:实测|上板|硬件验证)", "声称已完成硬件实测"),
    (r"已(?:经)?验证", "声称已验证"),
    (r"已(?:经)?确认", "声称已确认"),
    (r"实测[^，。；]{0,8}(?:为|是|约|等于)\s*[\d.]", "引用不存在的实测数值"),
    (r"实测(?:结果|数据|表明|显示)", "引用不存在的实测数据"),
    (r"示波器(?:已|实测)?显示", "引用不存在的仪器读数"),
    (r"我已经(?:测试|验证|实测|上板)", "声称 Agent 完成过硬件操作"),
]

# 需要软处理：这些只是"建议用户去做"，本身不越权，但需提醒用户去执行
_HINT_MUST_BE_ACTION = ("请", "建议", "可以", "尝试", "检查", "测量", "确认")

_WHITESPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "")).strip()


def scan_forbidden(text: str) -> list[str]:
    """扫描越权表述，返回命中原因列表（空列表 = 未越权）。"""
    hits: list[str] = []
    for pat, why in _FORBIDDEN_PATTERNS:
        if re.search(pat, text or ""):
            hits.append(why)
    return hits


def extract_json(text: str) -> tuple[dict | None, str]:
    """从模型输出里尽力取出一个 JSON 对象。

    7B 模型常在 JSON 前后加解释文字，因此不能直接 `json.loads` 整个输出。
    策略：先整体试；失败则从第一个 `{` 到最后一个 `}` 截取再试。
    仍然失败就返回 (None, 原因) —— **不猜、不补、不臆造字段**。
    """
    raw = (text or "").strip()
    if not raw:
        return None, "模型输出为空"
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj, ""
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start:end + 1])
            if isinstance(obj, dict):
                return obj, ""
        except json.JSONDecodeError as exc:
            return None, f"截断后仍不是合法 JSON：{exc}"
    return None, "输出中找不到 JSON 对象"


def _clean_steps(value: object) -> tuple[list[str], list[str]]:
    """清洗步骤/提醒列表，返回 (保留的条目, 因越权被丢弃的条目)。

    被丢弃的条目**必须回传给调用方**：静默丢掉一段越权内容，
    等于把"模型说了越权的话"这件事瞒下来——这本身就违反证据纪律。
    """
    if not isinstance(value, list):
        return [], []
    kept: list[str] = []
    dropped: list[str] = []
    for item in value[:LLM_MAX_STEPS]:
        if not isinstance(item, str):
            continue
        s = _norm(item)
        if not s:
            continue
        if len(s) > LLM_MAX_STEP_CHARS:
            s = s[:LLM_MAX_STEP_CHARS] + "…"
        if scan_forbidden(s):
            dropped.append(s)
            continue
        kept.append(s)
    return kept, dropped


def validate(raw_text: str) -> dict:
    """校验模型输出，返回统一结构。

    返回字段：
        verdict      accepted / rejected / empty
        reason       不通过的原因（accepted 时为空串）
        summary      解释文本（accepted 时非空）
        extra_steps  补充验证步骤（可能为空列表）
        cautions     注意事项（可能为空列表）
        violations   命中的越权原因
    """
    result = {
        "verdict": VERDICT_REJECTED,
        "reason": "",
        "summary": "",
        "extra_steps": [],
        "cautions": [],
        "violations": [],
    }

    obj, err = extract_json(raw_text)
    if obj is None:
        result["verdict"] = VERDICT_REJECTED
        result["reason"] = f"输出结构不合法：{err}"
        return result

    unknown = set(obj) - ALLOWED_KEYS
    if unknown:
        # 出现白名单外字段：不静默丢弃，而是**拒绝整段**——
        # 模型擅自加字段往往意味着它在越权输出证据等级或未授权结论。
        result["verdict"] = VERDICT_REJECTED
        result["reason"] = f"输出含未授权字段：{sorted(unknown)}"
        return result

    summary = _norm(str(obj.get("summary") or ""))
    steps, dropped_steps = _clean_steps(obj.get("extra_steps"))
    cautions, dropped_caut = _clean_steps(obj.get("cautions"))

    # 任何一段越权内容都让**整段输出作废**，而不是偷偷删掉那一句了事
    dropped = dropped_steps + dropped_caut
    if dropped:
        why = scan_forbidden(" ".join(dropped))
        result["verdict"] = VERDICT_REJECTED
        result["violations"] = why or ["列表项越权"]
        result["reason"] = ("输出中的列表项触及证据纪律红线，已整段作废："
                            + "；".join(why or ["列表项越权"]))
        return result

    blob = " ".join([summary] + steps + cautions)
    violations = scan_forbidden(blob)
    if violations:
        result["verdict"] = VERDICT_REJECTED
        result["violations"] = violations
        result["reason"] = "输出触及证据纪律红线：" + "；".join(violations)
        return result

    if not summary:
        result["verdict"] = VERDICT_EMPTY
        result["reason"] = "模型未给出有效解释（summary 为空）"
        return result

    if len(summary) > LLM_MAX_SUMMARY_CHARS:
        summary = summary[:LLM_MAX_SUMMARY_CHARS] + "…"

    result.update({
        "verdict": VERDICT_OK,
        "summary": summary,
        "extra_steps": steps,
        "cautions": cautions,
    })
    return result
