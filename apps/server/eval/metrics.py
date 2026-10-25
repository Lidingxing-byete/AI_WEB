"""Phase 6 指标计算。

对应 `docs/Phase2_评测指标定义.md` 的 5 类指标。凡是**无法由机器判定**的指标
（如"验证步骤可执行率""证据是否正确"），本模块只做**可核查的代理统计**，
并明确标注需人工复核，绝不冒充已测量。

关于「幻觉证据率」：这是本项目**关键指标**（指标文档第二节）。
机器无法判定一句自然语言证据的真伪，因此这里做的是**启发式自动筛查**：
把证据文本中出现的固件标识符与实际输入源码做比对，分成三类
    - `grounded`       证据引用的标识符确实出现在材料中（有据）
    - `contradiction`  证据说"未配置/未找到 X"，但 X 明明在源码里（**硬错误**）
    - `unverifiable`   证据断言了 X，但材料中找不到 X（**疑似幻觉，须人工复核**）
`unverifiable` 不是"已确认幻觉"，报告必须如此表述。
"""

from __future__ import annotations

import re

from app.engine.evidence import Finding
from app.engine.orchestrator import DiagnosticReport

# 固件中可枚举的标识符形态（标准外设库命名）
IDENT_RE = re.compile(
    r"\b(?:RCC_APB2?Periph_\w+|RCC_\w+|GPIO_Mode_\w+|GPIO_Pin_\w+|GPIO_Speed_\w+"
    # USART1_IRQn / USART_ReceiveData 两种写法都要能抓到
    r"|GPIO_Remap_\w+|USART\d*_\w+|NVIC_\w+|I2C_\w+|AFIO_\w+)\b"
)

# 行号引用："第 N 行" / "line N"
LINE_RE = re.compile(r"(?:第\s*(\d+)\s*行|line\s*(\d+))", re.IGNORECASE)

# 缺失型措辞：证据在说"某物不存在"，此时标识符**不应**出现在源码里
MISSING_CUES = (
    "未包含", "未找到", "未检测", "未使能", "未配置", "未实现", "未调用",
    "未开启", "未设置", "未挂接", "未启用", "未读", "未写", "未清除",
    "缺少", "缺失", "没有找到", "既未", "也未",
    "找不到", "不存在", "无 fputc", "无 NVIC", "未发现",
)

# 结构性根因（材料不足 / 编译错误）不计入"唯一根因误判"
STRUCTURAL_RULES = ("R-X-01", "R-X-02")


def _idents(text: str) -> list[str]:
    return IDENT_RE.findall(text or "")


def _strip_comments(src: str) -> str:
    """剥离 C 注释后再做标识符比对。

    为什么必须剥离：注释里提到的宏名（例如样例文件头说明"此处删去了
    RCC_APB2Periph_USART1"）**不代表代码里真的配置了它**。若不剥离注释，
    "未找到 X 的使能调用"这类正确结论会被误判成"与材料矛盾"。
    首轮评测就踩到了这个坑（P-01、P-05），故在此修正。
    """
    src = re.sub(r"/\*.*?\*/", " ", src or "", flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    return src


# 期望值措辞：这些词**后面**出现的标识符是"正确做法"，它当然不存在于故障代码里，
# 不能因此判为幻觉（首轮评测 P-03 就踩了这个坑）。
EXPECT_CUES = ("不是", "期望", "应为", "应该是", "要求", "需为",
               "改为", "改成", "应改为", "应为", "而非", "而不是")


def _in_src(token: str, src: str) -> bool:
    """词边界匹配。

    为什么不能用子串：`NVIC_Init` 会被 `NVIC_InitTypeDef` 命中，导致
    "未调用 NVIC_Init" 这类**正确**结论被误判成"与材料矛盾"（P-05 已踩坑）。
    """
    return re.search(r"\b" + re.escape(token) + r"\b", src) is not None


def _is_expected_context(token: str, text: str) -> bool:
    """该标识符是否出现在"期望值/正确做法"语境中。"""
    for m in re.finditer(re.escape(token), text):
        before = text[max(0, m.start() - 14): m.start()]
        if any(cue in before for cue in EXPECT_CUES):
            return True
    return False


def _claimed_missing(text: str) -> list[str]:
    """仅在"缺失型"证据中，提取**被声称缺失**的标识符。

    为什么不能取句中全部标识符：像"已调用 USART_ITConfig，但 NVIC_Init 中
    未配置 USART1_IRQn"这种混合句，前半句是成立的断言，只有"未配置"之后的
    部分才是缺失声明。取全句会把成立的部分也算进去，产生误判。
    """
    out: list[str] = []
    for cue in MISSING_CUES:
        for m in re.finditer(re.escape(cue), text):
            out.extend(_idents(text[m.end(): m.end() + 40]))
    return list(dict.fromkeys(out))


def screen_findings(
    findings: list[Finding],
    source_text: str,
) -> list[dict]:
    """对一组证据做幻觉启发式筛查。"""
    out: list[dict] = []
    src = _strip_comments(source_text)
    for f in findings:
        text = f.text or ""
        idents = _idents(text)

        # 行号核查
        line_bad: list[int] = []
        for m in LINE_RE.finditer(text):
            n = int(m.group(1) or m.group(2))
            lines = (source_text or "").splitlines()
            if n < 1 or n > len(lines):
                line_bad.append(n)

        if _claimed_missing(text):
            # 缺失型：被声称缺失的标识符若真的在源码里，这条断言与材料矛盾
            contradict = [t for t in _claimed_missing(text)
                          if _in_src(t, src)]
            verdict = "contradiction" if contradict else "grounded"
            detail = (f"证据声称缺失，但源码中确实存在：{contradict}"
                      if contradict else "")
        else:
            checked = [t for t in idents if not _is_expected_context(t, text)]
            unver = [t for t in checked if not _in_src(t, src)]
            if unver:
                verdict = "unverifiable"
                detail = f"引用的标识符未在输入材料中出现：{unver}"
            else:
                verdict = "grounded"
                detail = ""
        out.append({
            "source": f.source, "tag": f.tag, "rule_id": f.rule_id,
            "text": text[:160], "verdict": verdict,
            "detail": detail, "line_bad": line_bad,
        })
    return out


def traceable_rate(findings: list[Finding]) -> tuple[int, int]:
    """可追溯率：证据带来源工具名 且（规则号或技术引用 至少一个非空）。"""
    total = len(findings)
    ok = sum(1 for f in findings
             if f.source and (f.rule_id or f.ref))
    return ok, total


def assess(
    report: DiagnosticReport,
    case,                       # EvalCase
    source_text: str,
) -> dict:
    """对单次诊断输出做全部机器可判定的评估。"""
    cands = report.candidates
    rule_ids = [c.rule_id for c in cands]
    substantive = [c for c in cands if c.rule_id not in STRUCTURAL_RULES]

    res: dict = {
        "status": report.status,
        "rule_ids": rule_ids,
        "top1": rule_ids[0] if rule_ids else None,
    }

    # ---- 指标 1：根因命中 ----
    if case.expect_top1:
        res["top1_hit"] = bool(rule_ids) and rule_ids[0] == case.expect_top1
        res["top3_hit"] = case.expect_top1 in rule_ids[:3]
        res["missed"] = case.expect_top1 not in rule_ids
    res["forbidden_present"] = [r for r in rule_ids if r in case.forbid_rules]
    res["must_not_top1_violated"] = (
        bool(rule_ids) and rule_ids[0] in case.must_not_top1)
    res["expect_rule_missing"] = [
        r for r in case.expect_rule_present if r not in rule_ids]

    # ---- 指标 2：证据质量 ----
    all_findings: list[Finding] = []
    for c in cands:
        all_findings.extend(c.supporting)
        all_findings.extend(c.refuting)
    screened = screen_findings(all_findings, source_text)
    res["evidence_total"] = len(all_findings)
    res["evidence_grounded"] = sum(1 for s in screened
                                   if s["verdict"] == "grounded")
    res["evidence_unverifiable"] = sum(1 for s in screened
                                       if s["verdict"] == "unverifiable")
    res["evidence_contradiction"] = sum(1 for s in screened
                                        if s["verdict"] == "contradiction")
    res["hallucination_samples"] = [
        s for s in screened if s["verdict"] != "grounded"]
    ok, total = traceable_rate(all_findings)
    res["traceable_ok"], res["traceable_total"] = ok, total
    # 反对证据呈现：候选显式列出 refuting，或报告中出现"未见反对证据"声明
    res["refuting_declared"] = sum(
        1 for c in cands if c.refuting) + (
        1 if any("未见" in s and "反对" in s
                 for s in report.limitations) else 0)
    res["candidate_count"] = len(cands)
    res["substantive_candidate_count"] = len(substantive)

    # ---- 指标 3：验证步骤质量（可核查的代理项）----
    steps: list[str] = []
    for c in cands[:1]:
        steps.extend(c.verification_steps)
    res["top1_steps"] = len(steps)
    # 代理判别：步骤中含「预期/应/观察/测量/复测」等可判定词
    res["steps_actionable"] = sum(
        1 for s in steps
        if any(k in s for k in ("观察", "测量", "复测", "检查", "确认", "改为",
                                "确认", "对比", "试", "设置")))
    res["steps_with_expectation"] = sum(
        1 for s in steps
        if any(k in s for k in ("应", "预期", "若", "则", "正常", "恢复")))

    # ---- 指标 4：不确定性校准 ----
    res["status_ok"] = report.status == case.expect_status
    if case.kind == "negative" and case.case_id == "N-01":
        # N-01：不得给出实质性唯一根因
        res["calibration_ok"] = len(substantive) == 0
    else:
        res["calibration_ok"] = not res["forbidden_present"]
    fu_text = " ".join(report.followups)
    res["followup_keywords_hit"] = [
        k for k in case.expect_followup_keywords if k in fu_text]
    res["followup_keywords_missed"] = [
        k for k in case.expect_followup_keywords if k not in fu_text]

    # ---- 指标 5：工程与稳定性 ----
    total_runs = len(report.trace)
    ok_runs = sum(1 for t in report.trace if t.status == "called")
    err_runs = [t.tool for t in report.trace if t.status == "error"]
    res["tool_calls"] = total_runs
    res["tool_called"] = ok_runs
    res["tool_errors"] = err_runs
    res["trace_complete"] = total_runs > 0 and all(
        t.reason for t in report.trace)
    res["has_unknowns"] = len(report.unknowns)
    res["has_limitations"] = len(report.limitations)
    res["has_references"] = len(report.references)
    return res


def case_passed(res: dict, case) -> tuple[bool, list[str]]:
    """判定一个用例是否通过。返回的失败原因用于报告，不静默。"""
    reasons: list[str] = []
    if case.kind == "positive":
        if not res.get("top1_hit"):
            reasons.append(
                f"Top-1 为 {res.get('top1')}，期望 {case.expect_top1}")
        if res.get("missed"):
            reasons.append(f"期望规则 {case.expect_top1} 完全未出现")
        if res.get("must_not_top1_violated"):
            reasons.append(f"禁止项 {res.get('top1')} 排在 Top-1")
    else:
        if res.get("forbidden_present"):
            reasons.append(f"出现了禁止的规则 {res['forbidden_present']}")
        if not res.get("calibration_ok"):
            reasons.append("不确定性校准失败（给出了不应给出的根因）")
    if res.get("expect_rule_missing"):
        reasons.append(f"必须出现的规则缺失：{res['expect_rule_missing']}")
    if not res.get("status_ok"):
        reasons.append(
            f"状态为 {res.get('status')}，期望 {case.expect_status}")
    if res.get("followup_keywords_missed"):
        reasons.append(
            f"缺少引导性提示关键词：{res['followup_keywords_missed']}")
    if res.get("evidence_contradiction"):
        reasons.append(
            f"存在 {res['evidence_contradiction']} 条与材料矛盾的证据（硬错误）")
    return (not reasons), reasons
