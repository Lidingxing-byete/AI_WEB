"""`calc_baud_error` —— 波特率误差确定性计算（工具 T-04）。

为什么必须做成工具而不是交给大模型算：
波特率误差是"乱码"类故障的**决定性判据**，属于纯粹的算术，
让模型心算是本项目最容易翻车的地方（且错了还看不出来）。

依据（公有技术事实，RM0008 Ch.19，非手册原文）：
    baud = fCK / (16 * USARTDIV)
    USART_BRR: 位[15:4] = DIV_Mantissa（整数），位[3:0] = DIV_Fraction（小数，步进 1/16）

STM32F1 **只有 16 倍过采样**，没有 OVER8 位，公式不涉及 /8 分支（卡片 card-02）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import BAUD_TOLERANCE_PCT


@dataclass
class BaudResult:
    instance: str
    bus: str
    fck_hz: int
    target_baud: int
    usart_div: float
    mantissa: int
    fraction: int
    brr_hex: str
    actual_baud: float
    error_pct: float
    within_tolerance: bool
    tolerance_pct: float
    evidence_tag: str
    note: str

    def to_dict(self) -> dict:
        return {
            "instance": self.instance,
            "bus": self.bus,
            "fck_hz": self.fck_hz,
            "target_baud": self.target_baud,
            "usart_div": round(self.usart_div, 4),
            "mantissa": self.mantissa,
            "fraction": self.fraction,
            "brr_hex": self.brr_hex,
            "actual_baud": round(self.actual_baud, 2),
            "error_pct": round(self.error_pct, 4),
            "within_tolerance": self.within_tolerance,
            "tolerance_pct": self.tolerance_pct,
            "evidence_tag": self.evidence_tag,
            "note": self.note,
        }


def calc_baud_error(
    fck_hz: int,
    target_baud: int,
    instance: str = "USART1",
    bus: str = "APB2",
    tolerance_pct: float | None = None,
) -> BaudResult:
    """计算目标波特率在该总线时钟下的实际误差。

    Args:
        fck_hz: USART 所在总线时钟频率（USART1 取 PCLK2，USART2/3 取 PCLK1）
        target_baud: 目标波特率
        instance / bus: 仅用于结果标注
        tolerance_pct: 容差百分比，默认取 config.BAUD_TOLERANCE_PCT

    Returns:
        BaudResult。evidence_tag 恒为 `[静态分析]`——这是纯计算，不是实测。
    """
    tol = BAUD_TOLERANCE_PCT if tolerance_pct is None else tolerance_pct

    if fck_hz <= 0 or target_baud <= 0:
        raise ValueError(f"fck_hz 与 target_baud 必须为正数，收到 {fck_hz} / {target_baud}")

    # 理想分频值（带小数）
    usart_div = fck_hz / (16.0 * target_baud)

    mantissa = int(usart_div)
    fraction = int(round((usart_div - mantissa) * 16))

    # 小数部分四舍五入到 16 时进位（真实硬件中 BRR 低 4 位无法表示 16）
    if fraction >= 16:
        mantissa += 1
        fraction = 0

    brr = ((mantissa & 0xFFF) << 4) | (fraction & 0xF)

    # 由实际写入 BRR 的值反算真实波特率：只认寄存器里真正能放下的东西
    div_actual = mantissa + fraction / 16.0
    actual_baud = fck_hz / (16.0 * div_actual) if div_actual > 0 else 0.0
    error_pct = (actual_baud - target_baud) / target_baud * 100.0

    note = (
        f"USARTDIV={usart_div:.4f} → DIV_Mantissa={mantissa}, "
        f"DIV_Fraction={fraction} → USART_BRR=0x{brr:04X}"
    )
    if abs(error_pct) > tol:
        note += f"；误差 {error_pct:.3f}% 超过容差 {tol:.1f}%"

    return BaudResult(
        instance=instance,
        bus=bus,
        fck_hz=fck_hz,
        target_baud=target_baud,
        usart_div=usart_div,
        mantissa=mantissa,
        fraction=fraction,
        brr_hex=f"0x{brr:04X}",
        actual_baud=actual_baud,
        error_pct=error_pct,
        within_tolerance=abs(error_pct) <= tol,
        tolerance_pct=tol,
        evidence_tag="[静态分析]",
        note=note,
    )


# ----------------------------------------------------------------------
# 反向推断：由实测波特率反推实际总线时钟（Phase 4 新增，工具 T-04 反向模式）
# ----------------------------------------------------------------------

# 常见时钟树配置（STM32F103 公有技术事实，非手册原文）
# 键为总线时钟频率，值为该频率的典型来源。命中仅作"解释候选"，
# 绝不宣称"已确认是该配置"——确认需要用户提供时钟配置代码或实测 SYSCLK。
COMMON_TREES: tuple[tuple[int, str], ...] = (
    (72_000_000, "HSE 8MHz × 9（PLL 典型配置，PCLK2 = SYSCLK）"),
    (36_000_000, "系统时钟 72MHz 时 PCLK1 = SYSCLK/2（APB1 典型）"),
    (64_000_000, "HSI 8MHz 二分频后 × 16（PLL 倍频 16）"),
    (48_000_000, "HSE 8MHz × 6 或 HSI 二分频 × 12"),
    (24_000_000, "HSE/HSI 8MHz × 3"),
    (16_000_000, "HSE/HSI 8MHz × 2"),
    (8_000_000, "未启用 PLL，HSI/HSE 8MHz 直接作为系统时钟"),
    (12_000_000, "外部晶振 12MHz 直接作为系统时钟（未倍频）"),
)


@dataclass
class BaudInferResult:
    """由实测波特率反推实际总线时钟的结果。"""

    instance: str
    bus: str
    target_baud: int
    nominal_fck_hz: int            # 代码/配置假定的总线时钟
    observed_baud: float           # 用户实测得到的实际波特率
    div_actual: float              # 固件实际写入 BRR 对应的分频值
    brr_hex: str
    implied_fck_hz: float          # 反推出的实际总线时钟
    ratio: float                   # implied / nominal
    expected_baud_at_nominal: float  # 若总线时钟真是 nominal，应该跑出来的波特率
    tree_candidates: list[str] = field(default_factory=list)
    evidence_tag: str = "[推断]"
    note: str = ""
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "instance": self.instance,
            "bus": self.bus,
            "target_baud": self.target_baud,
            "nominal_fck_hz": self.nominal_fck_hz,
            "observed_baud": round(self.observed_baud, 2),
            "div_actual": round(self.div_actual, 4),
            "brr_hex": self.brr_hex,
            "implied_fck_hz": round(self.implied_fck_hz, 1),
            "ratio": round(self.ratio, 4),
            "expected_baud_at_nominal": round(self.expected_baud_at_nominal, 2),
            "tree_candidates": list(self.tree_candidates),
            "evidence_tag": self.evidence_tag,
            "note": self.note,
            "limitations": list(self.limitations),
        }


def infer_actual_fck(
    target_baud: int,
    nominal_fck_hz: int,
    observed_baud: float,
    instance: str = "USART1",
    bus: str = "APB2",
    tolerance_pct: float = 1.0,
) -> BaudInferResult:
    """由**用户实测的实际波特率**反推固件运行时的真实总线时钟。

    为什么这是 C2 类故障的决定性判据：
    "波特率所依据的时钟与实际不符"的典型表现，是固件按 72MHz 算出了 BRR，
    但板子实际跑在 8MHz（PLL 未生效），于是实际波特率是目标的 1/9。
    只要用户从串口助手/示波器读出一个实际波特率，就能纯算术反推真实时钟，
    不需要模型猜，也不需要上板改代码。

    Args:
        target_baud: 代码里配置的目标波特率
        nominal_fck_hz: 代码/工程配置**假定**的总线时钟
        observed_baud: 用户实测得到的实际波特率（来源：串口助手实测/示波器位宽）
        instance / bus: 仅用于结果标注
        tolerance_pct: 命中常见时钟树的相对容差（默认 1%）

    证据纪律：
        实测观测值本身由用户提供（`[实测-硬件]`），
        但"由此推断出的总线时钟"是推导结论，因此本结果整体标 **`[推断]`**，
        绝不冒充实测。
    """
    if nominal_fck_hz <= 0 or target_baud <= 0 or observed_baud <= 0:
        raise ValueError(
            "target_baud / nominal_fck_hz / observed_baud 必须为正数，"
            f"收到 {target_baud} / {nominal_fck_hz} / {observed_baud}"
        )

    # 固件写入 BRR 的值只取决于"它以为的"总线时钟与目标波特率
    nominal = calc_baud_error(nominal_fck_hz, target_baud, instance, bus)
    div_actual = nominal.mantissa + nominal.fraction / 16.0

    # baud = fCK / (16 * DIV)  =>  fCK = baud * 16 * DIV
    implied = observed_baud * 16.0 * div_actual
    ratio = implied / nominal_fck_hz
    expected_at_nominal = nominal_fck_hz / (16.0 * div_actual)

    cands = []
    for freq, desc in COMMON_TREES:
        if abs(implied - freq) / freq * 100.0 <= tolerance_pct:
            cands.append(f"{freq // 1_000_000}MHz：{desc}")
    if abs(ratio - 1.0) * 100.0 <= tolerance_pct:
        cands.append("与假定总线时钟一致，实际波特率偏差可能来自对端设置或晶振精度")

    note = (
        f"固件按 {nominal_fck_hz} Hz 写入 BRR={nominal.brr_hex}（DIV={div_actual:.4f}）；"
        f"若总线时钟确为 {nominal_fck_hz} Hz，应测得 {expected_at_nominal:.1f} bps；"
        f"实测 {observed_baud:.1f} bps → 实际总线时钟约 {implied:,.0f} Hz"
        f"（为假定值的 {ratio:.4f} 倍）"
    )
    if cands:
        note += "；可能时钟来源：" + "；".join(cands)

    limitations = [
        "本推断基于用户提供的实测波特率，推断结果为算术结论，"
        "标 `[推断]`，不等于已实测总线时钟",
        "若实测波特率本身测量不准（如串口助手自动波特率识别错误），"
        "推断结果不可靠",
        "未考虑 USART 时钟源被分频配置二次修改的情况，"
        "需结合系统时钟配置代码确认",
    ]

    return BaudInferResult(
        instance=instance,
        bus=bus,
        target_baud=target_baud,
        nominal_fck_hz=nominal_fck_hz,
        observed_baud=observed_baud,
        div_actual=div_actual,
        brr_hex=nominal.brr_hex,
        implied_fck_hz=implied,
        ratio=ratio,
        expected_baud_at_nominal=expected_at_nominal,
        tree_candidates=cands,
        evidence_tag="[推断]",
        note=note,
        limitations=limitations,
    )
