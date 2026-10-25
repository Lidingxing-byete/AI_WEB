/**
 * @file    main.c
 * @brief   芯诊 AI · Phase 2.5 硬件最小验证基准工程
 *
 * @details 用途：验证 STM32F103C8T6 + USART1 的最小串口链路能否跑通。
 *          行为：USART1 每约 500ms 发送一行固定字符串；同时翻转 PC13 上
 *                的板载 LED，作为「程序在运行」的目视旁证。
 *
 * @note    【版权与合规】
 *          本文件为「芯诊 AI」项目自撰代码，可入库。
 *          依赖的 STM32F10x 标准外设库（SPL）与启动文件来自本地已安装的
 *          Keil 设备包 / 官方固件库，**不属于本项目作品，不进公开仓库**。
 *          本文件不含官方手册原文、不含本地知识库原文。
 *
 * @note    【证据标签】本文件为 [静态分析] 产物，未经硬件实测。
 *          烧录后的实测结果须由用户在 Phase 2.5 验证记录中填写。
 *
 * @author  芯诊 AI 项目
 * @date    2026-10-25
 */

#include "stm32f10x.h"

/* ==========================================================================
 * 硬件约定（若与你的板子不符，改这里即可）
 * ========================================================================== */

/* USART1 默认引脚：PA9 = TX，PA10 = RX（不使用重映射） */
#define BASELINE_USART           USART1
#define BASELINE_GPIO_TX_PORT    GPIOA
#define BASELINE_GPIO_TX_PIN     GPIO_Pin_9
#define BASELINE_GPIO_RX_PORT    GPIOA
#define BASELINE_GPIO_RX_PIN     GPIO_Pin_10

/* 板载 LED：常见 STM32F103C8T6 最小系统板（蓝色药丸）的 LED 在 PC13，
 * 且为「低电平点亮」。若你的板子 LED 不在 PC13，请修改下面的端口与引脚。 */
#define BASELINE_LED_PORT        GPIOC
#define BASELINE_LED_PIN         GPIO_Pin_13
#define BASELINE_LED_ON()        GPIO_ResetBits(BASELINE_LED_PORT, BASELINE_LED_PIN)
#define BASELINE_LED_OFF()       GPIO_SetBits(BASELINE_LED_PORT, BASELINE_LED_PIN)

#define BASELINE_BAUDRATE        115200

/* ==========================================================================
 * 粗略软件延时
 * @note 仅用于基准工程，不精确；72MHz 下 1 个单位约 1ms（粗略估计）。
 *       延时精度不影响串口波特率的正确性判断。
 * ========================================================================== */
static void Baseline_DelayMs(uint32_t ms)
{
    uint32_t i, j;

    for (i = 0; i < ms; i++)
    {
        for (j = 0; j < 7200; j++)
        {
            __NOP();
        }
    }
}

/* ==========================================================================
 * 外设初始化
 * ========================================================================== */

/**
 * @brief  初始化 USART1 所用引脚
 * @note   顺序很重要：先开时钟，再配引脚。
 *         TX 必须为复用推挽输出（AF_PP）；RX 为浮空输入。
 */
static void Baseline_GPIO_Init(void)
{
    GPIO_InitTypeDef gpioInit;

    /* 1) 使能时钟：GPIOA（TX/RX）、GPIOC（LED）、USART1 */
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA |
                           RCC_APB2Periph_GPIOC |
                           RCC_APB2Periph_USART1, ENABLE);

    /* 2) TX 引脚：复用推挽输出 */
    gpioInit.GPIO_Pin   = BASELINE_GPIO_TX_PIN;
    gpioInit.GPIO_Mode  = GPIO_Mode_AF_PP;
    gpioInit.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(BASELINE_GPIO_TX_PORT, &gpioInit);

    /* 3) RX 引脚：浮空输入 */
    gpioInit.GPIO_Pin  = BASELINE_GPIO_RX_PIN;
    gpioInit.GPIO_Mode = GPIO_Mode_IN_FLOATING;
    GPIO_Init(BASELINE_GPIO_RX_PORT, &gpioInit);

    /* 4) LED 引脚：推挽输出 */
    gpioInit.GPIO_Pin   = BASELINE_LED_PIN;
    gpioInit.GPIO_Mode  = GPIO_Mode_Out_PP;
    gpioInit.GPIO_Speed = GPIO_Speed_2MHz;
    GPIO_Init(BASELINE_LED_PORT, &gpioInit);

    BASELINE_LED_OFF();
}

/**
 * @brief  初始化 USART1：115200 / 8 位数据 / 无校验 / 1 位停止位 / 无流控
 * @note   USART1 挂在 APB2 总线，波特率由 PCLK2 换算。
 *         本函数不使用中断，仅轮询发送，便于最小验证。
 */
static void Baseline_USART_Init(void)
{
    USART_InitTypeDef usartInit;

    usartInit.USART_BaudRate            = BASELINE_BAUDRATE;
    usartInit.USART_WordLength          = USART_WordLength_8b;
    usartInit.USART_StopBits            = USART_StopBits_1;
    usartInit.USART_Parity              = USART_Parity_No;
    usartInit.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
    usartInit.USART_Mode                = USART_Mode_Tx | USART_Mode_Rx;

    USART_Init(BASELINE_USART, &usartInit);
    USART_Cmd(BASELINE_USART, ENABLE);
}

/* ==========================================================================
 * 发送辅助
 * ========================================================================== */

static void Baseline_SendByte(uint8_t byte)
{
    /* 等待发送数据寄存器空，再写入下一字节 */
    while (USART_GetFlagStatus(BASELINE_USART, USART_FLAG_TXE) == RESET)
    {
    }
    USART_SendData(BASELINE_USART, (uint16_t)byte);
}

static void Baseline_SendString(const char *str)
{
    while (*str != '\0')
    {
        Baseline_SendByte((uint8_t)(*str));
        str++;
    }
}

/* ==========================================================================
 * 主函数
 * ========================================================================== */

int main(void)
{
    uint32_t counter = 0;

    /* SystemInit() 已在启动代码中调用，完成时钟配置（默认目标 72MHz）。
     * 若板载晶振频率与 HSE_VALUE 定义不一致，此处得到的系统时钟就不是
     * 72MHz，波特率会随之偏移 —— 这正是本次验证要暴露的风险之一。 */
    Baseline_GPIO_Init();
    Baseline_USART_Init();

    Baseline_SendString("\r\n[BASELINE] boot ok\r\n");

    while (1)
    {
        /* LED 翻转：目视确认程序在运行 */
        BASELINE_LED_ON();
        Baseline_DelayMs(250);
        BASELINE_LED_OFF();
        Baseline_DelayMs(250);

        /* 发送一行固定内容。
         * 字符串内含 "115200" 与连续数字，便于判断：
         *   - 收到乱码        → 波特率 / 时钟配置问题
         *   - 丢字节或截断    → 分频误差或连线问题
         *   - 完全收不到      → 时钟/引脚/接线问题 */
        Baseline_SendString("[BASELINE] tick ");

        /* 手动输出计数（不使用 printf，避免重定向与半主机相关依赖） */
        {
            char buf[12];
            uint32_t v = counter;
            uint8_t pos = 0;
            uint8_t k;

            if (v == 0)
            {
                buf[pos++] = '0';
            }
            while (v > 0)
            {
                buf[pos++] = (char)('0' + (v % 10));
                v /= 10;
            }
            /* 反转 */
            for (k = 0; k < pos / 2; k++)
            {
                char t = buf[k];
                buf[k] = buf[pos - 1 - k];
                buf[pos - 1 - k] = t;
            }
            buf[pos] = '\0';
            Baseline_SendString(buf);
        }

        Baseline_SendString(" - USART1 115200 8N1\r\n");

        counter++;
    }
}

/* 与本工程相关的说明：
 * - 本文件未使用 printf，因此不依赖 fputc 重定向，也不受半主机影响。
 * - 未使用中断，因此不涉及 NVIC 配置。
 * - 若后续需要在此基础上做故障注入（Phase 2 的 C1/C3/C5 等案例），
 *   本文件即为「正确基准」，注入时只改对应配置项。 */
