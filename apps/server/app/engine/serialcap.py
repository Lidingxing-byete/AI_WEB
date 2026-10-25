"""`parse_serial_capture` —— 串口实测抓帧解析（工具 T-07，Phase 5）。

为什么必须做成工具
------------------
Phase 3/4 的全部结论都是 `[静态分析]`：**只看代码，没看过板子**。
Phase 5 的核心任务是把"用户在真实板卡上抓到的串口现象"接进证据链，
否则"诊断建议与实际现象可以对照"（PROJECT_PLAN Phase 5 验收项）无从谈起。

本工具**只做两件事**，绝不越界：

1. 对用户提供的原始抓帧做**确定性统计**（字节数、可打印率、乱码率），
   得出 EMPTY / GARBLED / NORMAL 三态判定。
2. 把该判定与 `docs/corpus/phenomena_map.json` 中的**预期现象**做一致性比对。

证据纪律（本模块最需要守的地方）
--------------------------------
- 抓帧数据**由用户在硬件上采集**，因此对数据的统计与判读标 `[实测-硬件]`。
- "乱码意味着波特率不符 / 时钟不符"是**推断链**，标 `[推断]`。
- 预期现象对照表本身是**未实测的推断**（`[推断]`），因此比对结论也只能是 `[推断]`，
  **永远不得**因为"实测现象与预期一致"就把结论升级成 `[实测-硬件]`——
  一致只说明没被证伪，不等于根因已被硬件证实。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import (GARBLE_MIN_BYTES, GARBLE_NONPRINT_RATIO,
                      PHENOMENA_PATH)
from .evidence import Finding

# 现象判定取值
EMPTY = "EMPTY"          # 抓到 0 字节有效数据
GARBLED = "GARBLED"      # 抓到数据但不可读
NORMAL = "NORMAL"        # 抓到可读数据
UNKNOWN = "UNKNOWN"      # 数据不足以判定（例如用户只填了"有输出"四个字）

_TAG_HW = "[实测-硬件]"
_TAG_INFER = "[推断]"

# 允许出现在串口文本中的控制字符（不计入乱码）
_ALLOWED_CTRL = {"\r", "\n", "\t"}


@dataclass
class SerialCapFacts:
    """串口抓帧的确定性统计结果。"""

    lines: int = 0                  # 非空行数
    bytes_total: int = 0            # 有效字节数（去尾部空白后）
    printable_ratio: float = 0.0    # 可打印字符占比
    nonprint_ratio: float = 0.0     # 非可打印（疑似乱码）占比
    verdict: str = UNKNOWN          # EMPTY / GARBLED / NORMAL / UNKNOWN
    sample_lines: list[str] = field(default_factory=list)
    evidence_tag: str = _TAG_HW
    note: str = ""
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "lines": self.lines,
            "bytes_total": self.bytes_total,
            "printable_ratio": round(self.printable_ratio, 4),
            "nonprint_ratio": round(self.nonprint_ratio, 4),
            "verdict": self.verdict,
            "sample_lines": list(self.sample_lines[:5]),
            "evidence_tag": self.evidence_tag,
            "note": self.note,
            "limitations": list(self.limitations),
        }


def parse_serial_capture(raw_text: str, max_samples: int = 5) -> SerialCapFacts:
    """解析用户从串口助手 / Web Serial 页面抓到的原始帧文本。

    Args:
        raw_text: 用户粘贴的原始抓帧内容（**必须来自真实采集**，
            系统不提供任何自动生成抓帧的接口）
        max_samples: 结果中回显的样本行数

    Returns:
        SerialCapFacts。统计与判读标 `[实测-硬件]`——这是对实测数据的直接加工，
        不含推断链。

    判定规则（全部为确定性算术，不交给模型）：
    - 去空行后 0 行 → EMPTY
    - 字节数 < GARBLE_MIN_BYTES → UNKNOWN（样本太小，比例不可靠）
    - 非可打印占比 > GARBLE_NONPRINT_RATIO → GARBLED
    - 否则 → NORMAL
    """
    text = (raw_text or "").replace("\x00", "")
    lines = [l for l in text.splitlines() if l.strip()]
    facts = SerialCapFacts()

    if not lines:
        facts.verdict = EMPTY
        facts.note = "抓帧为空：串口未收到任何有效数据"
        facts.limitations = [
            "空抓帧可能来自：外设时钟未使能、TX 引脚模式错误、未调用发送函数、"
            "接线错误或串口助手选错 COM 口——**不能仅凭空抓帧确认根因**",
            "若串口助手本身未打开或未选对端口，也会表现为空抓帧，请先排除",
        ]
        return facts

    body = "\n".join(lines)
    facts.lines = len(lines)
    facts.bytes_total = len(body.encode("utf-8", errors="ignore"))
    facts.sample_lines = [l.strip()[:120] for l in lines[:max_samples]]

    total_chars = len(body)
    if total_chars == 0:
        facts.verdict = EMPTY
        return facts

    bad = 0
    for ch in body:
        if ch in _ALLOWED_CTRL:
            continue
        # 可打印 ASCII + 常用中文 + 替换字符之外的，计为疑似乱码
        o = ord(ch)
        if 0x20 <= o <= 0x7E:
            continue
        if 0x3000 <= o <= 0x9FFF:      # CJK 常用区，正常波特率下不会乱码
            continue
        bad += 1
        if o == 0xFFFD:                # Unicode 替换字符，解码失败的强信号
            bad += 3                   # 加权：一个替换字符顶四个可疑字节

    bad = min(bad, total_chars)
    facts.nonprint_ratio = bad / total_chars
    facts.printable_ratio = 1.0 - facts.nonprint_ratio

    if facts.bytes_total < GARBLE_MIN_BYTES:
        facts.verdict = UNKNOWN
        facts.note = (
            f"仅 {facts.bytes_total} 字节，样本过小，无法按比例判定是否乱码"
        )
        facts.limitations = [
            "小样本下比例阈值不可靠，需采集更多字节后重新判定",
        ]
        return facts

    if facts.nonprint_ratio > GARBLE_NONPRINT_RATIO:
        facts.verdict = GARBLED
        facts.note = (
            f"抓到 {facts.bytes_total} 字节，疑似乱码占比 "
            f"{facts.nonprint_ratio:.1%}（阈值 {GARBLE_NONPRINT_RATIO:.0%}）"
        )
        facts.limitations = [
            "乱码最常见的成因是收发双方波特率不一致，"
            "但电平标准不符（RS232 电平直连）与晶振偏差同样会造成乱码 —— "
            "需结合波特率误差工具与接线确认，不得直接认定根因",
            "串口助手自身的编码设置（如按 GBK 解析 UTF-8 文本）也会产生"
            "「看起来像乱码」的正常数据，请先确认助手编码",
        ]
    else:
        facts.verdict = NORMAL
        facts.note = (
            f"抓到 {facts.bytes_total} 字节 {facts.lines} 行，"
            f"可打印占比 {facts.printable_ratio:.1%}"
        )
        facts.limitations = [
            "「可打印」只说明字符可读，不证明内容正确——"
            "需用户确认文本与固件预期发送内容一致",
        ]
    return facts


def findings_from_capture(facts: SerialCapFacts) -> list[Finding]:
    """把抓帧统计转成证据条目。

    标签纪律：
        - 统计与判读本身 → `[实测-硬件]`（数据来自真实采集）
        - 由判读引出的成因方向 → `[推断]`（未证实）
    """
    out: list[Finding] = []
    if facts.verdict == UNKNOWN:
        return out

    out.append(Finding(
        source="parse_serial_capture",
        tag=_TAG_HW,
        text=f"串口实测现象判定为 {facts.verdict}：{facts.note}",
        ref="用户提供的串口抓帧（原始记录已存档）",
    ))

    if facts.verdict == EMPTY:
        out.append(Finding(
            source="parse_serial_capture",
            tag=_TAG_INFER,
            text=("无任何输出，指向「发送链路未工作」方向："
                  "外设时钟未使能 / TX 引脚模式错误 / 未调用发送函数 / printf 未重定向 / 接线错误"),
            ref="docs/corpus/phenomena_map.json（预期现象表，[推断]）",
        ))
    elif facts.verdict == GARBLED:
        out.append(Finding(
            source="parse_serial_capture",
            tag=_TAG_INFER,
            text=("有输出但乱码，指向「波特率与时钟不符」方向："
                  "建议用示波器测一位宽度换算出实际波特率，或用"
                  " `infer_actual_fck` 由实测波特率反推真实总线时钟"),
            ref="docs/corpus/phenomena_map.json（预期现象表，[推断]）",
        ))
    return out


# ----------------------------------------------------------------------
# 现象 ↔ 预期一致性比对
# ----------------------------------------------------------------------

@dataclass
class PhenomMatch:
    """实测现象与某案例预期现象的比对结果。"""

    case_id: str
    rule_id: str
    cause: str
    expected_tx: str
    actual_tx: str
    consistent: bool              # True=一致 / False=不一致 / None=无法比对时用 False + note
    evidence_tag: str = _TAG_INFER
    note: str = ""
    discriminator: str = ""

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "rule_id": self.rule_id,
            "cause": self.cause,
            "expected_tx": self.expected_tx,
            "actual_tx": self.actual_tx,
            "consistent": self.consistent,
            "evidence_tag": self.evidence_tag,
            "note": self.note,
            "discriminator": self.discriminator,
        }


def _load_phenomena() -> list[dict]:
    """加载预期现象表。文件缺失时返回空表——**不臆造预期**。"""
    import json

    if not PHENOMENA_PATH.exists():
        return []
    data = json.loads(PHENOMENA_PATH.read_text(encoding="utf-8"))
    return list(data.get("items", []))


def match_phenomena(facts: SerialCapFacts) -> list[PhenomMatch]:
    """把实测串口现象与各案例的预期现象做一致性比对。

    返回按"一致优先"排序的列表。标签恒为 `[推断]`：
    预期表本身未实测，且"现象一致"只表示**未被证伪**，不等于根因已证实。
    """
    if facts.verdict in (UNKNOWN, ""):
        return []

    out: list[PhenomMatch] = []
    for item in _load_phenomena():
        exp = item.get("expected_tx", "")
        ok = (exp == facts.verdict)
        out.append(PhenomMatch(
            case_id=item.get("case_id", ""),
            rule_id=item.get("rule_id", ""),
            cause=item.get("cause", ""),
            expected_tx=exp,
            actual_tx=facts.verdict,
            consistent=ok,
            evidence_tag=_TAG_INFER,
            note=("实测现象与该案例预期一致（一致不等于证实）"
                  if ok else
                  f"实测现象（{facts.verdict}）与该案例预期（{exp}）不一致，"
                  f"可作**排除依据**"),
            discriminator=item.get("discriminator", ""),
        ))
    out.sort(key=lambda m: (not m.consistent, m.case_id))
    return out
