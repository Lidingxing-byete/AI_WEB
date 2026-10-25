/*
 * 案例 C5 —— 故障版：接收中断未挂接 NVIC，且中断里未读 DR
 *
 * 本文件为本项目**自撰**测试样本，不复制任何教程或开源工程源码。
 *
 * 故障注入点（两处，均为真实高频故障）：
 *     ① 调用了 USART_ITConfig 使能 RXNE 中断，却没有 NVIC_Init，
 *        中断在内核侧根本没被放行 —— 现象为"能发不能收"。
 *     ② USART1_IRQHandler 里只做发送，没有调用 USART_ReceiveData，
 *        RXNE 标志不会清除 —— 即便进中断也会反复触发。
 *
 * 本案例是 Phase 4 的**多步诊断演示样本**：
 *     第 3 步 check_uart_config 命中 R-S6-01（NVIC 未配置）；
 *     第 4 步 analyze_isr_body 进一步读出函数体，命中 R-S6-03（未读 DR）。
 *     两步工具互相独立又互相补充，缺任一步都只能给出半个答案。
 *
 * 对照文件：c05_fixed.c
 */

#include "stm32f10x.h"

#define LED_GPIO_PORT GPIOC
#define LED_GPIO_PIN  GPIO_Pin_13
#define LED_GPIO_CLK  RCC_APB2Periph_GPIOC

volatile uint8_t g_rx_flag = 0;

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

    uart.USART_BaudRate = 115200;
    uart.USART_WordLength = USART_WordLength_8b;
    uart.USART_StopBits = USART_StopBits_1;
    uart.USART_Parity = USART_Parity_No;
    uart.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
    uart.USART_Mode = USART_Mode_Tx | USART_Mode_Rx;
    USART_Init(USART1, &uart);

    /* 注入点①：使能了 USART 级中断，但缺少 NVIC_Init */
    USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);

    USART_Cmd(USART1, ENABLE);
}

static void Uart1_SendByte(uint8_t byte)
{
    USART_SendData(USART1, byte);
    while (USART_GetFlagStatus(USART1, USART_FLAG_TXE) == RESET)
        ;
}

void USART1_IRQHandler(void)
{
    /* 注入点②：只读了中断标志却没读 DR，RXNE 不会清除 */
    if (USART_GetITStatus(USART1, USART_IT_RXNE) != RESET)
    {
        g_rx_flag = 1;
        USART_SendData(USART1, 'K');
    }
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
