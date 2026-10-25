/*
 * 案例 C3 —— 故障版：TX 引脚未配置为复用推挽
 *
 * 本文件为本项目**自撰**测试样本，不复制任何教程或开源工程源码。
 *
 * 故障注入点（唯一实质改动）：
 *     PA9（USART1_TX）被配置为 GPIO_Mode_Out_PP（通用推挽输出）
 *     而非 GPIO_Mode_AF_PP（复用推挽）。通用输出模式下引脚由 GPIO
 *     输出数据寄存器驱动，USART 外设拿不到引脚控制权，
 *     现象是**串口无任何输出、PA9 上测不到预期波形**。
 *
 * 对照文件：c03_fixed.c
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

    RCC_APB2PeriphClockCmd(RCC_APB2Periph_GPIOA | RCC_APB2Periph_USART1, ENABLE);

    /* 注入点：应为 GPIO_Mode_AF_PP */
    gpio.GPIO_Mode = GPIO_Mode_Out_PP;
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
