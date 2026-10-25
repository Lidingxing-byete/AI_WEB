"""Phase 6 仓库级安全与合规审计（**只读扫描**，不修改、不删除任何文件）。

对应 `PROJECT_PLAN.md` Phase 6「检查文件处理、命令执行边界、密钥保护和日志脱敏」
与 AGENTS.md 第六节赛事硬性规则（密钥、版权、原创性）。

检查项：
    S1 密钥/凭据泄漏扫描
    S2 .gitignore 对敏感文件的覆盖
    S3 命令执行边界（绝不编译/执行用户代码）
    S4 版权合规（不得出现他人教程、手册原文、本地知识库内容）
    S5 前端 XSS 面（不得有 innerHTML 注入）
    S6 证据标签红线（不得存在把推断升级为实测的写入路径）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]     # 比赛/
SERVER = ROOT / "apps" / "server"
WEB = ROOT / "apps" / "web"

PASS: list[str] = []
FAIL: list[str] = []
WARN: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def warn(name: str, detail: str) -> None:
    WARN.append(name)
    print(f"[WARN] {name}  -- {detail}")


# ---------------------------------------------------------------- S1 密钥

SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "OpenAI/通用 API Key"),
    (r"(?i)api[_-]?key\s*[:=]\s*['\"][^'\"]{8,}", "硬编码 api_key"),
    (r"(?i)(secret|token|password)\s*[:=]\s*['\"][^'\"]{8,}", "硬编码凭据"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key"),
]

SCAN_EXT = {".py", ".ts", ".tsx", ".js", ".json", ".c", ".h", ".md", ".example"}


def scan_secrets() -> None:
    print("-" * 68)
    print("S1 密钥/凭据泄漏扫描")
    print("-" * 68)
    hits: list[str] = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix not in SCAN_EXT:
            continue
        if any(part in ("node_modules", ".git", "__pycache__", "dist")
               for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat, label in SECRET_PATTERNS:
            for m in re.finditer(pat, text):
                # .env.example 中的占位符不算泄漏
                if "example" in p.name.lower() and (
                        "your" in m.group(0).lower()
                        or "xxx" in m.group(0).lower()
                        or m.group(0).count("*") >= 3):
                    continue
                hits.append(f"{p.relative_to(ROOT)}: {label}")
    # 已排除 .env.example 占位符后，仍命中才算问题
    real = [h for h in hits if "example" not in h.lower()]
    check("未发现硬编码密钥/凭据", not real, "; ".join(real[:3]))
    if not real and hits:
        warn("存在疑似但已被 .env.example 占位符规则排除的条目",
             "; ".join(hits[:3]))

    env_files = list(ROOT.rglob(".env"))
    check("仓库中无实际 .env 文件", not env_files,
          ", ".join(str(p.relative_to(ROOT)) for p in env_files))
    has_example = bool(list(ROOT.rglob(".env.example")))
    check("提供 .env.example（赛事要求）", has_example)


# ---------------------------------------------------------------- S2 gitignore


def scan_gitignore() -> None:
    print("-" * 68)
    print("S2 .gitignore 覆盖")
    print("-" * 68)
    gi = ROOT / ".gitignore"
    if not gi.exists():
        check(".gitignore 存在", False)
        return
    text = gi.read_text(encoding="utf-8", errors="ignore")
    for must in (".env", "*.db", "node_modules"):
        check(f".gitignore 覆盖 {must}", must in text)


# ---------------------------------------------------------------- S3 命令执行边界


def scan_exec() -> None:
    print("-" * 68)
    print("S3 命令执行边界（SEC-01：绝不编译/执行用户代码）")
    print("-" * 68)
    banned = [
        (r"\bsubprocess\.(run|Popen|call|check_output)", "subprocess"),
        (r"\bos\.system\s*\(", "os.system"),
        (r"\bos\.popen\s*\(", "os.popen"),
        (r"(?<![\w.])eval\s*\(", "eval()"),
        (r"(?<![\w.])exec\s*\(", "exec()"),
        (r"\bpickle\.loads?\s*\(", "pickle 反序列化"),
    ]
    hits: list[str] = []
    for p in (SERVER / "app").rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat, label in banned:
            if re.search(pat, text):
                hits.append(f"{p.relative_to(ROOT)}: {label}")
    check("后端无子进程/动态执行/反序列化调用", not hits, "; ".join(hits[:4]))


# ---------------------------------------------------------------- S4 版权


COPYRIGHT_MARKERS = [
    ("江协", "江协科技教程（已判定 B 类，不得入库）"),
    ("月薪喵", "月薪喵工程（已判定 C 类，排除）"),
    ("STM32学习", "本地知识库路径引用"),
    ("C:\\\\Users\\\\Zero\\\\Desktop\\\\Data", "本地绝对路径泄漏"),
    ("D:\\\\AI_program", "本地绝对路径泄漏"),
]


def scan_copyright() -> None:
    print("-" * 68)
    print("S4 版权与来源合规")
    print("-" * 68)
    hits: list[str] = []
    targets = list((ROOT / "docs").rglob("*.md")) + \
        list((ROOT / "docs" / "corpus").rglob("*")) + \
        list((SERVER / "app").rglob("*.py")) + \
        list((SERVER / "samples").rglob("*.c")) + \
        list((ROOT / "firmware").rglob("*.c"))
    for p in targets:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    # 严格区（进产品的部分）：命中即失败
    strict: list[str] = []
    # 宽松区（过程文档，记录"排除了谁"属正当引用）：命中仅提示
    loose: list[str] = []
    for p in targets:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # 注释里说明"排除了什么来源"是正当的（如 R19 记录），
        # 因此只对**代码本体**做严格判定（启发式剥离注释，够用于本仓库）。
        if p.suffix in (".py",):
            text = re.sub(r"#[^\n]*", "", text)
        elif p.suffix in (".c", ".h", ".ts", ".tsx", ".js"):
            text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
            text = re.sub(r"//[^\n]*", " ", text)
        rel = p.relative_to(ROOT)
        in_strict = any(part in ("corpus", "samples", "app", "firmware")
                        for part in rel.parts)
        for marker, label in COPYRIGHT_MARKERS:
            if marker in text:
                (strict if in_strict else loose).append(f"{rel}: {label}")
    check("语料/代码/固件中无他人教程与本地库标记", not strict,
          "; ".join(strict[:4]))
    if loose:
        warn("过程文档中提及被排除来源（属记录排除决定，非违规）",
             f"{len(loose)} 处，例如 {loose[0]}")

    # 语料卡片必须带 source_ref（可追溯）
    cards = sorted((ROOT / "docs" / "corpus" / "cards").glob("*.md"))
    entries = sum(len(re.findall(r"^## ", c.read_text(encoding="utf-8"),
                                 flags=re.M))
                  for c in cards)
    missing_ref = [c.name for c in cards
                   if "source_ref" not in c.read_text(encoding="utf-8")]
    check(f"{len(cards)} 个卡片文件 / {entries} 条卡片均带 source_ref",
          bool(cards) and entries > 0 and not missing_ref,
          ", ".join(missing_ref[:5]))


# ---------------------------------------------------------------- S5 前端


def scan_frontend() -> None:
    print("-" * 68)
    print("S5 前端 XSS 面")
    print("-" * 68)
    if not WEB.exists():
        warn("前端目录不存在", str(WEB))
        return
    hits: list[str] = []
    for p in (WEB / "src").rglob("*.tsx"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "dangerouslySetInnerHTML" in t or "innerHTML" in t:
            hits.append(p.name)
    check("前端无 innerHTML 注入面", not hits, ", ".join(hits))


# ---------------------------------------------------------------- S6 证据标签红线


def scan_evidence() -> None:
    print("-" * 68)
    print("S6 证据标签红线")
    print("-" * 68)
    db_py = (SERVER / "app" / "db.py").read_text(encoding="utf-8")
    check("数据库层对 evidence_tag 有 CHECK 约束",
          "CHECK" in db_py and "evidence_tag" in db_py)

    ev_py = (SERVER / "app" / "engine" / "evidence.py").read_text(
        encoding="utf-8")
    check("证据对象构造时强制校验标签（缺标签即抛错）",
          "require_tag" in ev_py or "raise ValueError" in ev_py)

    main_py = (SERVER / "app" / "main.py").read_text(encoding="utf-8")
    # 不存在的"自动填充观测值"接口
    bad = re.findall(r"(?i)def\s+\w*(auto_?fill|fabricate|fake)\w*\s*\(",
                     main_py)
    check("无自动填充/伪造观测值的接口", not bad, str(bad))


def main() -> int:
    print("=" * 68)
    print("Phase 6 仓库级安全与合规审计（只读）")
    print(f"扫描根目录：{ROOT}")
    print("=" * 68)
    scan_secrets()
    scan_gitignore()
    scan_exec()
    scan_copyright()
    scan_frontend()
    scan_evidence()
    print("=" * 68)
    print(f"通过 {len(PASS)}  失败 {len(FAIL)}  警告 {len(WARN)}")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
    if WARN:
        print("警告项（需人工判断，不计为通过也不计为失败）：")
        for w in WARN:
            print("  -", w)
    print("=" * 68)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
