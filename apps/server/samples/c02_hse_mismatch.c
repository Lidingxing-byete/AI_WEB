/*
 * 案例 C2 —— 故障版：波特率所依据的时钟与实际不符
 *
 * 本文件为本项目**自撰**测试样本，不复制任何教程或开源工程源码。
 *
 * 故障注入点（唯一实质改动）：
 *     HSE_VALUE 被改成 12000000，而目标板 STM32F103C8T6 实际焊接 8MHz 晶振。
 *     标准外设库按 HSE_VALUE 计算 PLL 与总线时钟，于是固件"以为"
 *     PCLK2=108MHz 并按此写入 USART_BRR，实际波特率与串口助手设置不一致，
 *     表现为**输出乱码**。
 *
 * ⚠️ 编译期陷阱（2026-10-25 实测前自查发现）：
 *     SPL 的 stm32f10x.h 中 HSE_VALUE 用 `#if !defined HSE_VALUE` 保护
 *     （默认非 VL 系列为 8MHz）。因此本文件的 `#define HSE_VALUE` 必须写在
 *     `#include "stm32f10x.h"` **之前**才会生效；写在之后会被库里的定义挡住，
 *     编译出来波特率正常 —— 静态分析会"检出"故障，上板却没有任何故障现象。
 *     这是"静态结论与实测脱节"的典型，务必保持当前顺序。
 *
 * 判定条件：
 *     本条（R-S2-01）静态无法自证——必须由用户提供板载实际晶振频率
 *     （actual_hse_hz），否则引擎返回 unknown，不冒充结论。
 *
 * 对照文件：c02_fixed.c
 */

/* 注入点：必须位于 include 之前（见上方说明） */
#ifndef HSE_VALUE
#define HSE_VALUE ((uint32_t)12000000)
#endif

#include "stm32f10x.h"

#define LED_GPIO_PORT GPIOC
#define LED_GPIO_PIN  GPIO_Pin_13
#define LED_GPIO_CLK  RCC_APB2Periph_GPIOC

static void Led_Init(void)
{
    GPIO_InitTypeDef gpio;

    RCC_APB2PeriphClockCmd(LED_GPIO_CLK, ENABLE);

    gpio.GPIO_Mode = GPIO_Mode_Out_PP;
    gpio.GPIO_Pin = LED_GPIO_PIN;
    gpio.GPIO_Speed = GPIO_Speed_2MHz;
    GPIO_Init(LED_GPIO_PORT, &gpio);
}

static void Uart1_Init(void)
{
    GPIO_InitTypeDef gpio;
    USART_InitTypeDef uart;

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_USART1, ENABLE);

    gpio.GPIO_Mode = GPIO_Mode_AF_PP;
    gpio.GPIO_Pin = GPIO_Pin_9;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &gpio);

    gpio.GPIO_Mode = GPIO_Mode_IPU;
    gpio.GPIO_Pin = GPIO_Pin_10;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOA, &gpio);

    /* 目标 115200：BRR 由库函数按 HSE_VALUE 推算出的 PCLK2 计算 */
    uart.USART_BaudRate = 115200;
    uart.USART_WordLength = USART_WordLength_8b;
    uart.USART_StopBits = USART_StopBits_1;
    uart.USART_Parity = USART_Parity_No;
    uart.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
    uart.USART_Mode = USART_Mode_Tx;
    USART_Init(USART1, &uart);

    USART_Cmd(USART1, ENABLE);
}

static void Uart1_SendByte(uint8_t byte)
{
    USART_SendData(USART1, byte);
    while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET)
        ;
}

int main(void)
{
    uint32_t counter = 0;

    Led_Init();
    Uart1_Init();

    while (1)
    {
        GPIO_WriteBit(LED_GPIO_PORT, LED_GPIO_PIN,
                      (BitAction)(1 - GPIO_ReadOutputDataBit(LED_GPIO_PORT, LED_GPIO_PIN)));

        Uart1_SendByte('A' + (counter % 26));
        counter++;

        for (volatile uint32_t i = 0; i < 800000u; i++)
            ;
    }
}
