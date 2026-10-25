"""`parse_build_log` —— 编译/链接日志解析（工具 T-01）。

为什么要有这个工具：
大量"串口不通"其实是**编译或链接失败**，程序根本没跑起来。
若不在最前面拦截，后面的配置检查全是在对一份没跑起来的程序做分析。

触发条件（Phase 1 约定）：仅当提交材料含编译日志时才调用，否则跳过并记录原因。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .evidence import EvidenceTag

TAG_STATIC = EvidenceTag.STATIC.value

# Keil ARMCC / ARMCLANG 常见形态
#   main.c(15): error:  #20: identifier "USART_Init" is undefined
#   main.c(15): warning:  #1-D: last line of file ends without a newline
RE_KEIL = re.compile(
    r"^(?P<file>[^\s()]+\.[chpsCHPS]+)\s*\((?P<line>\d+)\)\s*:\s*"
    r"(?P<kind>error|warning)\s*:\s*(?P<msg>.*)$",
    re.M,
)

# Keil 链接器
#   .\Objects\proj.axf: Error: L6218E: Undefined symbol USART_Init (referred from main.o).
RE_KEIL_LINK = re.compile(
    r"Error:\s*(?P<code>L\d+\w?)\s*:\s*(?P<msg>.*)$", re.M
)

# GCC / arm-none-eabi-gcc
#   main.c:15:3: error: 'x' undeclared
RE_GCC = re.compile(
    r"^(?P<file>[^\s:]+\.[chpsCHPS]+):(?P<line>\d+):(?P<col>\d+)?\s*:\s*"
    r"(?P<kind>error|warning)\s*:\s*(?P<msg>.*)$",
    re.M,
)


@dataclass
class BuildIssue:
    kind: str                 # error / warning
    file: str | None
    line: int | None
    code: str | None
    message: str
    raw: str
    tag: str = TAG_STATIC

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "code": self.code,
            "message": self.message,
            "raw": self.raw,
            "tag": self.tag,
        }


@dataclass
class BuildLogReport:
    issues: list[BuildIssue] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    has_link_error: bool = False
    tag: str = TAG_STATIC

    def to_dict(self) -> dict:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "has_link_error": self.has_link_error,
            "tag": self.tag,
        }


def parse_build_log(log: str) -> BuildLogReport:
    """解析编译日志，返回结构化问题列表。纯文本处理，不执行任何命令。"""
    report = BuildLogReport()
    if not log or not log.strip():
        return report

    for m in RE_KEIL.finditer(log):
        report.issues.append(BuildIssue(
            kind=m.group("kind").lower(),
            file=m.group("file"),
            line=int(m.group("line")),
            code=None,
            message=m.group("msg").strip(),
            raw=m.group(0),
        ))

    for m in RE_GCC.finditer(log):
        report.issues.append(BuildIssue(
            kind=m.group("kind").lower(),
            file=m.group("file"),
            line=int(m.group("line")),
            code=None,
            message=m.group("msg").strip(),
            raw=m.group(0),
        ))

    for m in RE_KEIL_LINK.finditer(log):
        report.has_link_error = True
        report.issues.append(BuildIssue(
            kind="error",
            file=None,
            line=None,
            code=m.group("code"),
            message=m.group("msg").strip(),
            raw=m.group(0),
        ))

    report.error_count = sum(1 for i in report.issues if i.kind == "error")
    report.warning_count = sum(1 for i in report.issues if i.kind == "warning")
    return report
