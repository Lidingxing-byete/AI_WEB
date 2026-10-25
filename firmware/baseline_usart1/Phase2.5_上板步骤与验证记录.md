# Phase 2.5 · 硬件最小验证：上板步骤与验证记录

> 时间：2026-10-25 下午 | 执行人：Zero（上板操作）／Agent（文档与代码）
> 编码解禁范围：**仅限 `firmware/baseline_usart1/` 下的基准工程固件**
> 证据标签：本文件步骤为 `[静态分析]`；**实测结果栏为空，须由用户上板后填写**

---

## 一、验证目标与验收标准

**目标**：确认 STM32F103C8T6 + USART1 的最小串口链路能跑通，作为后续 6 个故障案例的「正确基准」。

| 验收项 | 标准 | 结果 |
|---|---|---|
| A. 编译 | Keil 编译 0 Error、0 Warning（关键项） | ⬜ 待填 |
| B. 烧录 | ST-Link 下载成功，提示校验通过 | ⬜ 待填 |
| C. 程序运行 | PC13 板载 LED 每约 500ms 闪烁 | ⬜ 待填 |
| D. 串口接收 | 每约 500ms 收到一行 `[BASELINE] tick N - USART1 115200 8N1` | ⬜ 待填 |
| E. 波特率正确 | 收到内容**无乱码**，数字连续递增无丢行 | ⬜ 待填 |

**全部 5 项通过 → Phase 2.5 通过。** 任一项失败 → 记录现象，不强行判定通过。

---

## 二、前置检查（现在的状态）

| 项 | 状态 |
|---|---|
| ST-Link | ✅ `VID_0483&PID_3748`，Status OK |
| USB-TTL | ✅ COM3，`VID_0843&PID_5740`，CDC-ACM，`usbser.sys` |
| IO 电平 | ✅ 3.3V，与 STM32F103 直连安全 |
| Keil MDK | ✅ 5.41 + STM32F1xx_DFP 2.4.1 |
| SPL 库 | ✅ Keil DFP 自带 + 本地 V3.5.0 双来源 |

---

## 三、建 Keil 工程

### 方案 A（推荐）：用 RTE 自动勾选组件

1. `Project → New µVision Project`，保存为 `baseline_usart1.uvprojx`
2. 选设备：**STM32F103C8**（STMicroelectronics → STM32F103 → STM32F103C8）
3. 弹出 Manage Run-Time Environment，**按以下勾选**：
   - `CMSIS → CORE` ✅
   - `Device → Startup` ✅
   - `Device → StdPeriph Drivers → Framework` ✅
   - `Device → StdPeriph Drivers → GPIO` ✅
   - `Device → StdPeriph Drivers → RCC` ✅
   - `Device → StdPeriph Drivers → USART` ✅
4. 点 Resolve → OK。RTE 会自动配好头文件路径与源文件
5. 把本目录的 `main.c` 加入工程的 Source Group

> 已确认：DFP 2.4.1 的 pdsc 中声明了 `Device → StdPeriph Drivers` 组件，且 `Device/StdPeriph_Driver` 目录存在，方案 A 可用。

### 方案 B（备用）：手动添加本地 SPL V3.5.0

若方案 A 报错，改用本地库（路径已核实存在）：

```
根目录：C:\Users\Zero\Desktop\Data\STM32学习\固件库\STM32F10x_StdPeriph_Lib_V3.5.0\
```

需加入工程的文件：

| 文件 | 路径（相对上述根目录） |
|---|---|
| `system_stm32f10x.c` | `Libraries\CMSIS\CM3\DeviceSupport\ST\STM32F10x\` |
| `stm32f10x_gpio.c` | `Libraries\STM32F10x_StdPeriph_Driver\src\` |
| `stm32f10x_rcc.c` | 同上 |
| `stm32f10x_usart.c` | 同上 |
| `startup_stm32f10x_md.s` | `Libraries\CMSIS\CM3\DeviceSupport\ST\STM32F10x\startup\arm\` |
| `stm32f10x_conf.h` | 从 `Project\STM32F10x_StdPeriph_Template\` 复制到本目录 |

**Include Paths**（Options → C/C++ → Include Paths）：

```
Libraries\CMSIS\CM3\CoreSupport
Libraries\CMSIS\CM3\DeviceSupport\ST\STM32F10x
Libraries\STM32F10x_StdPeriph_Driver\inc
（本工程目录，放 stm32f10x_conf.h）
```

**Define**（Options → C/C++ → Define）：

```
USE_STDPERIPH_DRIVER, STM32F10X_MD
```

> `STM32F10X_MD` = Medium Density。C8T6 为 64KB Flash，属 MD，**不是 HD**，别选错。

---

## 四、编译与烧录配置

1. `Options for Target → Debug`：选 **ST-Link Debugger** → Settings → Port 选 **SW**
   - 若右侧 SW Device 能列出设备（显示 IDCODE），说明 ST-Link 与板子通信正常 ✅
2. `Options for Target → Utilities`：勾选 **Use Debug Driver** + **Update Target before Debugging**
3. `Flash Download` 中勾选 **Reset and Run**（烧完自动运行，不用手动复位）
4. 编译：`F7`（Build）→ 确认 **0 Error**
5. 下载：`F8`（Download）

---

## 五、接线（3.3V 直连，无需电平转换）

| USB-TTL | → | STM32F103C8T6 |
|---|---|---|
| RXD | ← | **PA9**（USART1 TX） |
| TXD | → | **PA10**（USART1 RX） |
| GND | ↔ | **GND**（必须共地） |

⚠️ **不要接 VCC**（板子由 ST-Link 或自身 USB 供电即可）。若需供电，确认电压是 3.3V 而非 5V。

⚠️ 注意 TX/RX 是**交叉**的：转换器的 RXD 接 MCU 的 TX。

---

## 六、串口接收验证

### 工具：`webserial_probe.html`（本项目自撰，已提供）

参数：**COM3，115200，8 位数据，无校验，1 位停止位，无流控**（页面默认值即此）

**用法**：用 Chrome 或 Edge 打开 → 点「连接串口」→ 在弹出列表中选设备 → 自动开始接收。

**它不只是通用串口助手**，针对基准工程做了自动判别：

| 自动判别 | 说明 |
|---|---|
| 乱码检测 | 统计非 ASCII 字节占比，>15% 判为乱码，提示波特率/时钟问题 |
| 丢行检测 | 解析输出中的 `tick N`，计数不连续即计为丢行 |
| 结论条 | 综合给出「链路正常 / 乱码 / 丢行」提示 |

数据全部在浏览器本地处理，不上传任何服务器；可一键保存为 txt 作为实测留档。

### ⚠️ 坑：直接双击打开（`file://`）大概率不行

Web Serial API 要求**安全上下文**（secure context）。`file://` 协议通常不满足，双击打开会看到「非安全上下文」提示，且连接按钮无效。

**解决办法**：在本目录下起一个本地静态服务，然后用浏览器访问 `http://localhost:PORT/webserial_probe.html`。

命令（**请你自己执行**，我不执行——启动服务不在本次解禁范围内）：

```bash
cd "C:/Users/Zero/Desktop/比赛/firmware/baseline_usart1"
"C:/Users/Zero/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m http.server 8765
```

然后浏览器打开 `http://localhost:8765/webserial_probe.html`。

> 备注：`http://localhost` 属于安全上下文，可正常使用 Web Serial。

### 其他注意

- **浏览器**：Chrome 或 Edge。Firefox 不支持 Web Serial API（风险 R18）
- **独占访问**：串口被本页打开期间其他工具无法访问，先关闭其他串口程序

---

## 七、结果记录（实测，由用户填写）

| 项 | 实测结果 | 证据标签 |
|---|---|---|
| 编译结果（Error/Warning 数） | | `[实测-硬件]` |
| 烧录结果 | | `[实测-硬件]` |
| LED 是否闪烁（频率） | | `[实测-硬件]` |
| 串口收到的第一行内容 | | `[实测-硬件]` |
| 是否乱码 | | `[实测-硬件]` |
| 计数是否连续（有无丢行） | | `[实测-硬件]` |
| 实测波特率是否可接受 | | `[实测-硬件]` |

**若失败**：填写实际现象，**不要填「通过」**。现象本身就是最有价值的诊断素材。

---

## 八、失败排查对照

| 现象 | 优先怀疑 | 对应规则 |
|---|---|---|
| 完全收不到，LED 也不闪 | 程序未运行 / 时钟配置错误 / 烧录失败 | R-X-01 |
| LED 闪但收不到 | USART 时钟未使能、TX 引脚模式、接线交叉 | R-S1-01 / R-S1-03 / R-S1-05 |
| 收到但乱码 | 波特率与时钟不符（HSE_VALUE / 分频） | R-S2-01 / R-S2-02 / R-S2-03 |
| 收到 0x00 或固定错字符 | 波特率严重偏差 | R-S2-01 |
| 丢行但不乱码 | 延时精度或接收端缓冲，非波特率问题 | — |

---

## 九、关于编码解禁的说明

本次解禁范围严格遵守 `AGENTS.md`：**仅限 `firmware/baseline_usart1/`**。

已在该范围内产出：`main.c`（固件）、`webserial_probe.html`（验证工具）、本文件。

**未做且仍禁止**：未编译、未烧录（由你在 Keil 执行）、未安装任何依赖、未启动任何服务、未修改开发环境。
启动本地静态服务的命令已在第六节给出，**由你自行执行**，我不代执行。
