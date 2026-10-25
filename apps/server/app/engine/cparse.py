"""`analyze_c_code` —— C 源码静态解析（工具 T-02）。

安全红线（SEC-01）：**绝不编译、绝不执行用户代码**。
本模块只做文本与正则层面的结构化提取，产出"可检查的事实"，不做任何求值。

解析能力边界（诚实声明）：
- 覆盖：标准外设库（SPL）风格的显式初始化调用与结构体字段赋值
- 不覆盖：跨翻译单元的宏展开、复杂条件编译、函数指针间接调用
  这些局限会在输出的 `limitations` 中如实列出，不假装解析完整。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import MAX_SOURCE_BYTES

# ------------------------------------------------------------------ 注释处理


def split_comments(src: str) -> tuple[str, list[str]]:
    """剥离注释，同时保留被注释掉的原文。

    用等长空白替换注释内容并保留换行，使后续正则匹配到的**行号仍然是原始行号**。
    返回 (去除注释后的源码, 注释原文列表)。

    注释原文用于检出"初始化调用被注释掉"这一类真实故障（案例 C1 的注入手法）。
    """
    out: list[str] = []
    comments: list[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            block = src[i:j]
            comments.append(block)
            out.append("".join("\n" if c == "\n" else " " for c in block))
            i = j
        elif ch == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            line = src[i:j]
            comments.append(line)
            out.append(" " * len(line))
            i = j
        elif ch in "\"'":
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                if src[i] == "\\":
                    out.append(src[i: i + 2])
                    i += 2
                    continue
                out.append(src[i])
                if src[i] == quote:
                    i += 1
                    break
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), comments


# ------------------------------------------------------------------ 事实容器


@dataclass
class GpioInit:
    """一次 GPIO_Init 调用解析结果。"""

    port: str
    pins: list[str]
    mode: str | None
    speed: str | None
    line: int


@dataclass
class UsartInit:
    """一次 USART_Init 调用解析结果。"""

    instance: str
    baudrate: int | None
    word_length: str | None
    stop_bits: str | None
    parity: str | None
    mode: str | None
    flow_control: str | None
    line: int


@dataclass
class NvicInit:
    """一次 NVIC_Init 调用解析结果。"""

    irq_channel: str | None
    cmd: str | None
    preemption: str | None
    subpriority: str | None
    line: int


@dataclass
class IsrBody:
    """一个中断服务函数的函数体（用于 R-S6-03 / R-S6-04 的函数体级分析）。"""

    name: str
    line: int
    body: str


@dataclass
class CodeFacts:
    """`analyze_c_code` 的输出：结构化事实集合。

    所有字段都可能为 None / 空——**没提取到就是没提取到**，不做猜测填充。
    """

    path: str = ""
    lines: int = 0
    # RCC
    apb2_enabled: list[str] = field(default_factory=list)
    apb1_enabled: list[str] = field(default_factory=list)
    rcc_register_writes: list[str] = field(default_factory=list)
    # GPIO
    gpio_inits: list[GpioInit] = field(default_factory=list)
    pin_remaps: list[str] = field(default_factory=list)
    # USART
    usart_inits: list[UsartInit] = field(default_factory=list)
    usart_cmd: list[str] = field(default_factory=list)          # USART_Cmd 的实例
    usart_it: list[tuple[str, str]] = field(default_factory=list)  # (实例, 中断源)
    # NVIC
    nvic_priority_group: str | None = None
    nvic_inits: list[NvicInit] = field(default_factory=list)
    # 输出重定向
    redirect_impl: list[str] = field(default_factory=list)      # fputc / _write / ...
    uses_printf: bool = False
    # 中断服务函数（用于校验与启动文件向量表是否一致）
    isr_functions: list[str] = field(default_factory=list)
    # 中断服务函数体（Phase 4：函数体级分析，用于 R-S6-03 / R-S6-04）
    isr_bodies: list[IsrBody] = field(default_factory=list)
    # 时钟
    hse_value: int | None = None
    # 被注释掉的初始化调用（真实故障信号）
    commented_apis: list[tuple[str, int]] = field(default_factory=list)
    # 解析局限（如实声明）
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "lines": self.lines,
            "apb2_enabled": self.apb2_enabled,
            "apb1_enabled": self.apb1_enabled,
            "rcc_register_writes": self.rcc_register_writes,
            "gpio_inits": [
                {"port": g.port, "pins": g.pins, "mode": g.mode,
                 "speed": g.speed, "line": g.line}
                for g in self.gpio_inits
            ],
            "pin_remaps": self.pin_remaps,
            "usart_inits": [
                {"instance": u.instance, "baudrate": u.baudrate,
                 "word_length": u.word_length, "stop_bits": u.stop_bits,
                 "parity": u.parity, "mode": u.mode,
                 "flow_control": u.flow_control, "line": u.line}
                for u in self.usart_inits
            ],
            "usart_cmd": self.usart_cmd,
            "usart_it": [list(x) for x in self.usart_it],
            "nvic_priority_group": self.nvic_priority_group,
            "nvic_inits": [
                {"irq_channel": v.irq_channel, "cmd": v.cmd,
                 "preemption": v.preemption, "subpriority": v.subpriority,
                 "line": v.line}
                for v in self.nvic_inits
            ],
        "redirect_impl": self.redirect_impl,
        "uses_printf": self.uses_printf,
        "isr_functions": self.isr_functions,
        "isr_bodies": [
            {"name": b.name, "line": b.line, "body": b.body} for b in self.isr_bodies
        ],
            "hse_value": self.hse_value,
            "commented_apis": [list(x) for x in self.commented_apis],
            "limitations": self.limitations,
        }


# ------------------------------------------------------------------ 结构体赋值追踪

_ASSIGN_RE = re.compile(r"\b(\w+)\s*\.\s*(\w+)\s*=\s*([^;]+);")


def _struct_values(src: str, var: str, before_line: int) -> dict[str, str]:
    """取结构体变量 `var` 在 `before_line` 行之前（含）的所有字段赋值。

    同一字段多次赋值时**取最后一次**，这与 C 的执行语义一致。
    这是极简的数据流追踪：只追踪"变量名.字段 = 值"这一形态，不追踪指针别名。
    """
    src_lines = src.splitlines()
    values: dict[str, str] = {}
    for idx, line in enumerate(src_lines, start=1):
        if idx > before_line:
            break
        for m in _ASSIGN_RE.finditer(line):
            if m.group(1) == var:
                values[m.group(2)] = m.group(3).strip()
    return values


def _line_of(src: str, pos: int) -> int:
    return src.count("\n", 0, pos) + 1


def _extract_brace_body(src: str, open_pos: int) -> str:
    """从 `{` 位置起做花括号配对，取出完整函数体。

    只做括号计数，不做语法解析——无法配对时返回到字符串末尾，
    由调用方按"可能不完整"处理（本项目的解析局限一律如实声明）。
    """
    if open_pos >= len(src) or src[open_pos] != "{":
        return ""
    depth = 0
    i, n = open_pos, len(src)
    while i < n:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[open_pos:i + 1]
        i += 1
    return src[open_pos:]


# ------------------------------------------------------------------ 主解析


def analyze_c_code(source: str, path: str = "") -> CodeFacts:
    """解析 C 源码，返回结构化事实。

    绝不执行、绝不编译入参源码（SEC-01）。
    """
    facts = CodeFacts(path=path)

    if len(source.encode("utf-8", errors="ignore")) > MAX_SOURCE_BYTES:
        facts.limitations.append(
            f"源码超过 {MAX_SOURCE_BYTES // 1024} KB 上限，已截断，结果可能不完整"
        )
        source = source[:MAX_SOURCE_BYTES]

    clean, comments = split_comments(source)
    facts.lines = clean.count("\n") + 1

    # ---- RCC 外设时钟使能 ----
    for m in re.finditer(r"RCC_(APB2|APB1)PeriphClockCmd\s*\(([^;]*?)\)\s*;", clean, re.S):
        bus, args = m.group(1), m.group(2)
        periphs = re.findall(r"RCC_(?:APB2|APB1)Periph_\w+", args)
        target = facts.apb2_enabled if bus == "APB2" else facts.apb1_enabled
        for p in periphs:
            if p not in target:
                target.append(p)

    # ---- 直接写寄存器（APB2ENR / APB1ENR）----
    for m in re.finditer(r"RCC\s*->\s*(APB2ENR|APB1ENR)", clean):
        if m.group(1) not in facts.rcc_register_writes:
            facts.rcc_register_writes.append(m.group(1))

    # ---- GPIO_Init ----
    for m in re.finditer(r"\bGPIO_Init\s*\(\s*(GPIO[A-G])\s*,\s*&\s*(\w+)\s*\)\s*;", clean):
        port, var = m.group(1), m.group(2)
        line = _line_of(clean, m.start())
        vals = _struct_values(clean, var, line)
        pins = re.findall(r"GPIO_Pin_\w+", vals.get("GPIO_Pin", ""))
        facts.gpio_inits.append(
            GpioInit(port=port, pins=pins,
                     mode=vals.get("GPIO_Mode"), speed=vals.get("GPIO_Speed"),
                     line=line)
        )

    # ---- 引脚重映射 ----
    for m in re.finditer(r"GPIO_PinRemapConfig\s*\(\s*([^,]+),", clean):
        remap = m.group(1).strip()
        if remap not in facts.pin_remaps:
            facts.pin_remaps.append(remap)

    # ---- USART_Init ----
    for m in re.finditer(r"\bUSART_Init\s*\(\s*(USART\d)\s*,\s*&\s*(\w+)\s*\)\s*;", clean):
        inst, var = m.group(1), m.group(2)
        line = _line_of(clean, m.start())
        vals = _struct_values(clean, var, line)
        baud = None
        raw_baud = vals.get("USART_BaudRate", "")
        digits = re.search(r"\d+", raw_baud)
        if digits:
            baud = int(digits.group(0))
        facts.usart_inits.append(
            UsartInit(instance=inst, baudrate=baud,
                      word_length=vals.get("USART_WordLength"),
                      stop_bits=vals.get("USART_StopBits"),
                      parity=vals.get("USART_Parity"),
                      mode=vals.get("USART_Mode"),
                      flow_control=vals.get("USART_HardwareFlowControl"),
                      line=line)
        )

    # ---- USART_Cmd ----
    for m in re.finditer(r"\bUSART_Cmd\s*\(\s*(USART\d)\s*,", clean):
        if m.group(1) not in facts.usart_cmd:
            facts.usart_cmd.append(m.group(1))

    # ---- USART_ITConfig ----
    for m in re.finditer(r"\bUSART_ITConfig\s*\(\s*(USART\d)\s*,\s*([^,]+),", clean):
        pair = (m.group(1), m.group(2).strip())
        if pair not in facts.usart_it:
            facts.usart_it.append(pair)

    # ---- NVIC ----
    pg = re.search(r"NVIC_PriorityGroupConfig\s*\(\s*([^)]+)\)", clean)
    if pg:
        facts.nvic_priority_group = pg.group(1).strip()
    for m in re.finditer(r"\bNVIC_Init\s*\(\s*&\s*(\w+)\s*\)\s*;", clean):
        var = m.group(1)
        line = _line_of(clean, m.start())
        vals = _struct_values(clean, var, line)
        facts.nvic_inits.append(
            NvicInit(irq_channel=vals.get("NVIC_IRQChannel"),
                     cmd=vals.get("NVIC_IRQChannelCmd"),
                     preemption=vals.get("NVIC_IRQChannelPreemptionPriority"),
                     subpriority=vals.get("NVIC_IRQChannelSubPriority"),
                     line=line)
        )

    # ---- printf 重定向 ----
    for pattern, name in (
        (r"\bint\s+fputc\s*\(", "fputc"),
        (r"\bint\s+_write\s*\(", "_write"),
        (r"\b__io_putchar\s*\(", "__io_putchar"),
        (r"\bint\s+fputs\s*\(", "fputs"),
    ):
        if re.search(pattern, clean) and name not in facts.redirect_impl:
            facts.redirect_impl.append(name)
    facts.uses_printf = bool(re.search(r"\bprintf\s*\(", clean))

    # ---- 中断服务函数定义 + 函数体 ----
    for m in re.finditer(r"\bvoid\s+(\w*IRQHandler)\s*\(\s*void\s*\)\s*\{", clean):
        name = m.group(1)
        if name not in facts.isr_functions:
            facts.isr_functions.append(name)
        body = _extract_brace_body(clean, m.end() - 1)
        facts.isr_bodies.append(
            IsrBody(name=name, line=_line_of(clean, m.start()), body=body)
        )

    # ---- HSE_VALUE ----
    # 实际工程里常见三种写法，都要能取到值：
    #   #define HSE_VALUE 8000000
    #   #define HSE_VALUE ((uint32_t)8000000)
    #   #define HSE_VALUE ((uint32_t)12000000)   <- 本正则必须覆盖
    # 因此先定位宏定义行，再取该行第一个数字，而不是要求数字紧跟宏名。
    hse_line = re.search(r"#define\s+HSE_VALUE\b[^\n]*", clean)
    if hse_line:
        # 必须先剥掉类型转换再取数字：`(uint32_t)8000000` 里的 "32"
        # 来自 uint32_t，直接取第一个数字会得到 32（真实踩过的坑）。
        line = re.sub(r"\(?\s*(?:uint|int)\d*_t\s*\)?", " ", hse_line.group(0))
        nums = re.findall(r"\d+", line)
        if nums:
            facts.hse_value = int(nums[0])

    # ---- 被注释掉的初始化调用（真实故障信号）----
    api_names = (
        "RCC_APB2PeriphClockCmd", "RCC_APB1PeriphClockCmd",
        "GPIO_Init", "USART_Init", "USART_Cmd", "USART_ITConfig",
        "NVIC_Init", "GPIO_PinRemapConfig",
    )
    for block in comments:
        for name in api_names:
            if re.search(r"\b" + name + r"\s*\(", block):
                facts.commented_apis.append((name, 0))
    # 去重
    seen: set[str] = set()
    dedup: list[tuple[str, int]] = []
    for name, ln in facts.commented_apis:
        if name not in seen:
            seen.add(name)
            dedup.append((name, ln))
    facts.commented_apis = dedup

    # ---- 解析局限声明 ----
    if re.search(r"#if|#ifdef|#ifndef", clean):
        facts.limitations.append("源码含条件编译，未做宏展开，分支内的配置可能未被提取")
    if re.search(r"#define\s+\w+\s*\([^)]*\)\s*GPIO|#define\s+\w+\s*\([^)]*\)\s*USART", clean):
        facts.limitations.append("源码含函数式宏封装的初始化调用，正则可能漏检")
    if not facts.usart_inits and not facts.gpio_inits:
        facts.limitations.append("未提取到任何 USART_Init / GPIO_Init 调用，材料可能不含初始化代码")

    return facts
