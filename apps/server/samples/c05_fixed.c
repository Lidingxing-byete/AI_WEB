/*
 * 案例 C5 —— 正确版（对照组）
 *
 * 本文件为本项目自撰测试样本。相对 c05_rx_irq_no_nvic.c 的两处修正：
 *     ① 补上 NVIC_Init，把 USART1_IRQn 挂进中断控制器；
 *     ② 中断服务函数里调用 USART_ReceiveData 读 DR，RXNE 随之清除。
 */

#include "stm32f10x.h"

#define LED_GPIO_PORT GPIOC
#define LED_GPIO_PIN  GPIO_Pin_13
#define LED_GPIO_CLK  RCC_APB2Periph_GPIOC

volatile uint8_t g_rx_flag = 0;
volatile uint8_t g_rx_byte = 0;

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
    NVIC_InitTypeDef nvic;

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

    USART_ITConfig(USART1, USART_IT_RXNE, ENABLE);

    /* 正确①：NVIC 放行 USART1 中断 */
    nvic.NVIC_IRQChannel = USART1_IRQn;
    nvic.NVIC_IRQChannelPreemptionPriority = 1;
    nvic.NVIC_IRQChannelSubPriority = 1;
    nvic.NVIC_IRQChannelCmd = ENABLE;
    NVIC_Init(&nvic);

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
    if (USART_GetITStatus(USART1, USART_IT_RXNE) != RESET)
    {
        /* 正确②：读 DR，RXNE 随读操作自动清除 */
        g_rx_byte = USART_ReceiveData(USART1);
        g_rx_flag = 1;
        USART_SendData(USART1, g_rx_byte);
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
