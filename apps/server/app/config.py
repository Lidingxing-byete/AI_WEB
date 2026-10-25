"""全局配置与常量。

设计原则：所有"经验值"都显式声明为可配置，并标注证据等级。
未经硬件实测校准的阈值一律标 [待验证]，不得在文档中宣称已验证。
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 路径

# apps/server/app/config.py -> 上溯 4 层得到仓库根
REPO_ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = REPO_ROOT / "docs" / "corpus"
RULES_PATH = CORPUS_DIR / "l1_rules_uart.json"
CARDS_DIR = CORPUS_DIR / "cards"
# 案例 → 预期串口现象对照表（自撰，全部标 [推断]，Phase 5 实测后回填）
PHENOMENA_PATH = CORPUS_DIR / "phenomena_map.json"

# ---------------------------------------------------------------- 判定阈值

# 串口抓帧乱码判定阈值。[待验证] —— 依赖真实串口助手的呈现方式，
# Phase 5 采集到真实抓帧样本后校准，此刻不得宣称已验证
GARBLE_NONPRINT_RATIO = float(os.getenv("XZ_GARBLE_NONPRINT_RATIO", "0.15"))
# 短帧保护：低于该字节数不套用比例阈值（样本太小时比例不可靠）
GARBLE_MIN_BYTES = int(os.getenv("XZ_GARBLE_MIN_BYTES", "16"))

# ---------------------------------------------------------------- 芯片事实

# STM32F103C8T6 公有技术事实（RM0008 Ch.7 / Ch.19，非手册原文）
CHIP_F103C8T6 = {
    "name": "STM32F103C8T6",
    "flash_kb": 64,
    "density": "MD",          # Medium Density -> 启动文件 startup_stm32f10x_md.s
    "hse_default_hz": 8_000_000,
    "sysclk_max_hz": 72_000_000,
    "pclk2_max_hz": 72_000_000,   # APB2：USART1 挂在此总线
    "pclk1_max_hz": 36_000_000,   # APB1：USART2 / USART3 挂在此总线
}

# USART 实例 -> 总线归属。搞反总线，波特率会差一倍（卡片 card-01）
USART_BUS = {
    "USART1": "APB2",
    "USART2": "APB1",
    "USART3": "APB1",
}

# USART 实例 -> 默认引脚 / 重映射引脚（RM0008 Ch.9 复用功能映射）
USART_PINS = {
    "USART1": {"default": ("GPIOA", "GPIO_Pin_9", "GPIO_Pin_10"),
               "remap": ("GPIOB", "GPIO_Pin_6", "GPIO_Pin_7")},
    "USART2": {"default": ("GPIOA", "GPIO_Pin_2", "GPIO_Pin_3"),
               "remap": ("GPIOD", "GPIO_Pin_5", "GPIO_Pin_6")},
    "USART3": {"default": ("GPIOB", "GPIO_Pin_10", "GPIO_Pin_11"),
               "remap": ("GPIOC", "GPIO_Pin_10", "GPIO_Pin_11")},
}

# TX 必须的配置模式；RX 允许的两种输入模式（卡片 card-09 / card-10）
GPIO_MODE_TX_OK = {"GPIO_Mode_AF_PP", "GPIO_Mode_AF_OD"}
GPIO_MODE_RX_OK = {"GPIO_Mode_IN_FLOATING", "GPIO_Mode_IPU", "GPIO_Mode_IPD"}

# 调试引脚：作普通 GPIO 前必须释放 JTAG（卡片 B 组）
DEBUG_PINS = {"GPIO_Pin_15": "GPIOA", "GPIO_Pin_3": "GPIOB", "GPIO_Pin_4": "GPIOB"}

# ---------------------------------------------------------------- 可调阈值

# 波特率容差：初值 2%。[待验证] —— Phase 5 实测后回填，此刻不得宣称已验证
BAUD_TOLERANCE_PCT = float(os.getenv("XZ_BAUD_TOLERANCE_PCT", "2.0"))

# BM25 参数（语料仅 40 条，偏小 k1 让词频不至于压倒全部）
BM25_K1 = 1.2
BM25_B = 0.75

# 单文件解析上限，防止超大文件拖垮服务（Phase1 SEC / Phase6 异常输入测试）
MAX_SOURCE_BYTES = 512 * 1024

# ---------------------------------------------------------------- 模型层

# 三级降级链（Phase 1 ADR）：云端（DeepSeek，可配置）→ 本地 Ollama → 规则层兜底。
# 默认指向 DeepSeek（OpenAI 兼容接口）：填入 XZ_CLOUD_API_KEY 即启用云端；
# 密钥留空则云端自动跳过（clone 即跑不依赖密钥）。
CLOUD_PROVIDER = os.getenv("XZ_CLOUD_PROVIDER", "deepseek")
CLOUD_MODEL = os.getenv("XZ_CLOUD_MODEL", "deepseek-chat")   # 备选 deepseek-reasoner
CLOUD_BASE_URL = os.getenv("XZ_CLOUD_BASE_URL", "https://api.deepseek.com")
# 密钥只从环境变量读，绝不入库、绝不写进代码与文档
CLOUD_API_KEY = os.getenv("XZ_CLOUD_API_KEY", "")
# 模型来源偏好：auto=云端就绪优先否则本地；local=固定本地；cloud=固定云端（失败自动降级本地）
LLM_PREFERRED = os.getenv("XZ_LLM_PREFERRED", "auto").strip().lower()
# 云端请求超时（云端无冷启动，60s 足够；仅当上下文极大时可放宽）
CLOUD_TIMEOUT_S = float(os.getenv("XZ_CLOUD_TIMEOUT_S", "60"))

# 本地模型（Ollama）：仅本机 127.0.0.1，不下模型、不改 Ollama 配置。
# 默认 qwen2.5-coder:7b —— 2026-10-25 三模型基线横向对比后选定（docs/Phase6_模型选型对比.md）
OLLAMA_HOST = os.getenv("XZ_OLLAMA_HOST", "http://127.0.0.1:11434")
LLM_MODEL = os.getenv("XZ_LLM_MODEL", "qwen2.5-coder:7b")
# 冷启动需把 4.7GB 权重载入显存，因此超时不能设太短
LLM_TIMEOUT_S = float(os.getenv("XZ_LLM_TIMEOUT_S", "120"))
LLM_TEMPERATURE = float(os.getenv("XZ_LLM_TEMPERATURE", "0.2"))
LLM_MAX_TOKENS = int(os.getenv("XZ_LLM_MAX_TOKENS", "700"))
# 总开关：关闭时引擎行为与接入前完全一致（纯规则层）
LLM_ENABLED = os.getenv("XZ_LLM_ENABLED", "1") not in ("0", "false", "False", "")
# 健康检查缓存秒数，避免每轮诊断都打一次 /api/tags
LLM_HEALTH_CACHE_S = float(os.getenv("XZ_LLM_HEALTH_CACHE_S", "20"))

# 模型输出守卫上限（超出即截断并标记，防止把整篇输出塞进证据链）
LLM_MAX_SUMMARY_CHARS = 400
LLM_MAX_STEPS = 4
LLM_MAX_STEP_CHARS = 120
