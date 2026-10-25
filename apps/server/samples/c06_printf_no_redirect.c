/*
 * 案例 C6 —— 故障版：调用 printf 但未做底层重定向
 *
 * 本文件为本项目**自撰**测试样本，不复制任何教程或开源工程源码。
 *
 * 故障注入点（唯一实质改动）：
 *     main 里用 printf 输出，但工程里没有 fputc / _write / __io_putchar
 *     任何一个重定向实现。printf 的输出不会落到 USART，
 *     现象是"初始化看着都对，串口就是没东西"。
 *
 * 判定依据：R-S5-01（printf 未重定向）
 * 说明：本样本**不使用**半主机相关写法，规避 Keil 未勾选 MicroLIB 时
 *      程序卡死的干扰变量，使故障归因单一、可复现。
 *
 * 对照文件：c06_fixed.c
 */

#include "stm32f10x.h"
#include <stdio.h>

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

    uart.USART_BaudRate = 115200;
    uart.USART_WordLength = USART_WordLength_8b;
    uart.USART_StopBits = USART_StopBits_1;
    uart.USART_Parity = USART_Parity_No;
    uart.USART_HardwareFlowControl = USART_HardwareFlowControl_None;
    uart.USART_Mode = USART_Mode_Tx;
    USART_Init(USART1, &uart);

    USART_Cmd(USART1, ENABLE);
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

        /* 注入点：没有任何 printf 重定向实现，这里的输出不会到串口 */
        printf("[TICK] %lu\r\n", counter);
        counter++;

        for (volatile uint32_t i = 0; i < 800000u; i++)
            ;
    }
}
