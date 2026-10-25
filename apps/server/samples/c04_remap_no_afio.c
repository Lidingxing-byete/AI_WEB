/*
 * 案例 C4 —— 故障版：使用引脚重映射但未开启 AFIO 时钟
 *
 * 本文件为本项目**自撰**测试样本，不复制任何教程或开源工程源码。
 *
 * 故障注入点（唯一实质改动）：
 *     将 USART1 重映射到 PB6/PB7，并正确调用了 GPIO_PinRemapConfig，
 *     但 RCC_APB2PeriphClockCmd 只使能了 GPIOB，漏掉 RCC_APB2Periph_AFIO。
 *     AFIO 时钟未开启时重映射配置不生效，USART1 仍锁定在 PA9/PA10，
 *     而外接线接在 PB6/PB7 上，现象是**串口完全无输出**。
 *
 * 判定依据：R-S1-04（重映射必须先使能 AFIO 时钟）
 * 对照文件：c04_fixed.c
 */

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

    /* 注入点：只开了 GPIOB，漏掉 RCC_APB2Periph_AFIO */
    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOB | RCC_APB2Periph_USART1, ENABLE);

    /* 重映射：USART1 -> PB6(TX) / PB7(RX) */
    GPIO_PinRemapConfig(GPIO_Remap_USART1, ENABLE);

    gpio.GPIO_Mode = GPIO_Mode_AF_PP;
    gpio.GPIO_Pin = GPIO_Pin_6;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOB, &gpio);

    gpio.GPIO_Mode = GPIO_Mode_IPU;
    gpio.GPIO_Pin = GPIO_Pin_7;
    gpio.GPIO_Speed = GPIO_Speed_50MHz;
    GPIO_Init(GPIOB, &gpio);

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
