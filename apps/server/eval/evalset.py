"""Phase 6 固定评测集（可执行版）。

来源：`docs/Phase2_诊断测试集.md`（P-01~P-06 + N-01~N-03）与
`docs/Phase2_评测指标定义.md`。**判定标准在跑测试之前就已固定**（指标文档第三节
「评判前置」），本文件只是把它机器化，不新增、不放宽、不删改任何一条期望。

红线：
- 本文件中的 `expect_*` 是**设计目标**，不是测试结果。
- 用例材料全部为本项目自撰（`samples/*.c` 或本文件内联），无第三方代码。
- 证据等级：本评测集本身为 `[静态分析]` 层级——它检验的是"引擎能否按规则
  检出注入的缺陷"，**不检验**该缺陷在真实板卡上的现象。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 内联材料：不便于落成 .c 文件的输入（负例、编译日志、超范围外设）
# --------------------------------------------------------------------------

N02_BUILD_LOG = """\
Build target 'Target 1'
compiling main.c...
compiling stm32f10x_usart.c...
linking...
.\\Objects\\test.axf: Error: L6218E: Undefined symbol USART_Init (referred from usart.o).
.\\Objects\\test.axf: Error: L6218E: Undefined symbol USART_Cmd (referred from usart.o).
Program Size: Code=1240 RO-data=336 RW-data=12 ZI-data=1024
".\\Objects\\test.axf" - 2 Error(s), 0 Warning(s).
"""

# 自撰的 I2C 初始化片段（用于 N-03 超范围用例）
N03_I2C_SOURCE = """\
/* 自撰：I2C1 初始化片段（用于测试范围边界，非 UART 场景） */
#include "stm32f10x.h"

void I2C1_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;
    I2C_InitTypeDef  I2C_InitStructure;

    RCC_APB1PeriphClockCmd(RCC_APB1Periph_I2C1, ENABLE);
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB, ENABLE);

    GPIO_InitStructure.GPIO_Pin   = GPIO_Pin_6 | GPIO_Pin_7;
    GPIO_InitStructure.GPIO_Mode  = GPIO_Mode_AF_OD;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOB, &GPIO_InitStructure);

    I2C_InitStructure.I2C_Mode        = I2C_Mode_I2C;
    I2C_InitStructure.I2C_ClockSpeed  = 100000;
    I2C_InitStructure.I2C_OwnAddress1 = 0x0A;
    I2C_Init(I2C1, &I2C_InitStructure);
    I2C_Cmd(I2C1, ENABLE);
}

int main(void)
{
    I2C1_Init();
    while (1) {
        /* 读取传感器 */
    }
}
"""

# 自撰：一个"干净"的 USART1 初始化（N-02 用：代码没问题，问题在编译链接）
N02_SOURCE = """\
/* 自撰：USART1 初始化（配置本身无缺陷），用于 N-02 编译错误用例 */
#include "stm32f10x.h"

void Uart1_Init(void)
{
    GPIO_InitTypeDef GPIO_InitStructure;
    USART_InitTypeDef USART_InitStructure;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_USART1 | RCC_APB2Periph_GPIOA, ENABLE);

    GPIO_InitStructure.GPIO_Pin   = GPIO_Pin_9;
    GPIO_InitStructure.GPIO_Mode  = GPIO_Mode_AF_PP;
    GPIO_InitStructure.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &GPIO_InitStructure);

    USART_InitStructure.USART_BaudRate   = 115200;
    USART_InitStructure.USART_WordLength = USART_WordLength_8b;
    USART_InitStructure.USART_StopBits   = USART_StopBits_1;
    USART_InitStructure.USART_Parity     = USART_Parity_No;
    USART_InitStructure.USART_Mode       = USART_Mode_Tx;
    USART_Init(USART1, &USART_InitStructure);
    USART_Cmd(USART1, ENABLE);
}

int main(void)
{
    Uart1_Init();
    while (1) {
        USART_SendData(USART1, 'A');
    }
}
"""


@dataclass
class EvalCase:
    """一个评测用例。"""

    case_id: str
    title: str
    kind: str                       # "positive" | "negative"
    # ---- 输入材料 ----
    sample_files: list[str] = field(default_factory=list)   # samples/ 下的文件名
    inline_source: str = ""
    source_path: str = "user/main.c"
    symptom_text: str = ""
    build_log: str = ""
    actual_hse_hz: int | None = None
    pclk2_hz: int | None = None
    pclk1_hz: int | None = None
    # ---- 期望（来自 Phase 2 测试集，不可事后修改）----
    expect_top1: str | None = None          # 正例：期望的 Top-1 规则号
    expect_rule_present: list[str] = field(default_factory=list)
    must_not_top1: list[str] = field(default_factory=list)
    expect_status: str = "ok"               # ok / insufficient
    # 负例专用
    expect_followup_keywords: list[str] = field(default_factory=list)
    forbid_rules: list[str] = field(default_factory=list)  # 不得出现的规则
    # ---- 元信息 ----
    hardware_plan: str = ""                 # 是否有硬件实测计划
    trap: str = ""                          # 陷阱点（人工复核用）


# --------------------------------------------------------------------------
# 9 个用例
# --------------------------------------------------------------------------

CASES: list[EvalCase] = [
    EvalCase(
        case_id="P-01",
        title="C1 USART1 外设时钟未使能",
        kind="positive",
        sample_files=["c01_no_usart1_clock.c"],
        source_path="user/usart.c",
        symptom_text="上电后串口助手完全没反应，LED 正常闪烁",
        pclk2_hz=72_000_000,
        expect_top1="R-S1-01",
        expect_rule_present=["R-S1-01"],
        must_not_top1=["R-S2-01", "R-S2-02", "R-S6-01"],
        hardware_plan="有（Phase 5 实测计划内）",
        trap="「LED 正常闪烁」暗示程序在跑，应排除「程序没运行」类猜测",
    ),
    EvalCase(
        case_id="P-02",
        title="C2 HSE_VALUE 与实际晶振不符导致波特率整体偏移",
        kind="positive",
        sample_files=["c02_hse_mismatch.c"],
        source_path="user/usart.c",
        symptom_text="收到的是乱码，但不是完全没有东西",
        actual_hse_hz=8_000_000,
        pclk2_hz=72_000_000,
        expect_top1="R-S2-01",
        expect_rule_present=["R-S2-01"],
        must_not_top1=["R-S1-01", "R-S6-01"],
        hardware_plan="有（Phase 5 实测计划内）",
        trap="未提供实际晶振频率时不得凭空断言误差；本用例显式提供 actual_hse_hz",
    ),
    EvalCase(
        case_id="P-03",
        title="C3 TX 引脚未配置为复用推挽输出",
        kind="positive",
        sample_files=["c03_tx_not_afpp.c"],
        source_path="user/usart.c",
        symptom_text="用示波器测量 PA9 没有波形",
        pclk2_hz=72_000_000,
        expect_top1="R-S1-03",
        expect_rule_present=["R-S1-03"],
        must_not_top1=["R-S2-01", "R-S2-02"],
        hardware_plan="有（Phase 5 实测计划内）",
        trap="与 C1 现象重叠，须通过 RCC 检查区分；只报时钟未使能属覆盖不全",
    ),
    EvalCase(
        case_id="P-04",
        title="C4 重映射未开启 AFIO 时钟",
        kind="positive",
        sample_files=["c04_remap_no_afio.c"],
        source_path="user/usart.c",
        symptom_text="换了引脚之后就没输出了",
        pclk2_hz=72_000_000,
        expect_top1="R-S1-04",
        expect_rule_present=["R-S1-04"],
        must_not_top1=["R-S2-01"],
        hardware_plan="无（仅静态分析，标 [待验证]）",
        trap="若建议「检查 PA9 接线」而未识别代码用的是 PB6，属材料读取错误",
    ),
    EvalCase(
        case_id="P-05",
        title="C5 接收中断未挂接 NVIC",
        kind="positive",
        sample_files=["c05_rx_irq_no_nvic.c"],
        source_path="user/usart.c",
        symptom_text="能收到板子发来的数据，但我发给板子它没反应",
        pclk2_hz=72_000_000,
        expect_top1="R-S6-01",
        expect_rule_present=["R-S6-01"],
        must_not_top1=["R-S2-01", "R-S2-02", "R-S1-01"],
        hardware_plan="有（Phase 5 实测计划内）",
        trap="「单向正常」已排除波特率/引脚问题；若断言波特率错误属严重误判",
    ),
    EvalCase(
        case_id="P-06",
        title="C6 printf 未重定向到串口",
        kind="positive",
        sample_files=["c06_printf_no_redirect.c"],
        source_path="user/main.c",
        symptom_text="初始化代码看着都对，但就是不发东西",
        pclk2_hz=72_000_000,
        expect_top1="R-S5-01",
        expect_rule_present=["R-S5-01"],
        must_not_top1=["R-S2-01", "R-S6-01"],
        hardware_plan="无（仅静态分析，标 [待验证]）",
        trap="应建议先用 USART_SendData 直发验证硬件链路，以区分于硬件故障",
    ),
    EvalCase(
        case_id="N-01",
        title="材料严重不足",
        kind="negative",
        symptom_text="串口不通，怎么办",
        expect_status="insufficient",
        forbid_rules=["R-S1-01", "R-S1-03", "R-S2-01", "R-S2-02",
                      "R-S5-01", "R-S6-01", "R-S1-04"],
        expect_followup_keywords=["代码", "补充"],
        hardware_plan="不适用",
        trap="给出带高置信度的唯一根因即判不通过（违反创新方向 A）",
    ),
    EvalCase(
        case_id="N-02",
        title="真实问题是链接错误，用户描述为硬件故障",
        kind="negative",
        inline_source=N02_SOURCE,
        source_path="user/usart.c",
        symptom_text="程序下载进去之后串口没有任何输出，是不是引脚接错了",
        build_log=N02_BUILD_LOG,
        pclk2_hz=72_000_000,
        expect_status="ok",
        expect_rule_present=["R-X-01"],
        expect_followup_keywords=["编译"],
        hardware_plan="不适用",
        trap="编译未过则硬件行为无从谈起；忽略编译日志直接给硬件根因判不通过",
    ),
    EvalCase(
        case_id="N-03",
        title="材料与首发场景不匹配（提交的是 I2C 代码）",
        kind="negative",
        inline_source=N03_I2C_SOURCE,
        source_path="user/i2c.c",
        symptom_text="I2C 读不到数据，怎么回事",
        expect_status="ok",
        forbid_rules=["R-S1-01", "R-S1-03", "R-S2-01", "R-S2-02",
                      "R-S5-01", "R-S6-01", "R-S1-04", "R-S4-01"],
        expect_followup_keywords=["范围"],
        hardware_plan="不适用",
        trap="硬套 UART 结论判不通过（违反 MVP 范围与防套用要求）",
    ),
]

CASES_BY_ID = {c.case_id: c for c in CASES}
