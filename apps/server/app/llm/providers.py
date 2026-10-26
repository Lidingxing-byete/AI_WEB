"""云端模型服务商目录（OpenAI 兼容接口）。

为什么需要它
------------
之前云端只支持 DeepSeek（base_url / 模型名写死在配置里）。要支持"多样"，
主流做法是**服务商预设表 + 统一 OpenAI 兼容协议**：

  - 服务商由**服务端白名单**决定 base_url（绝不接受前端传入 base_url，
    否则本服务就成了任意地址的代理 → SSRF）；
  - 模型名允许测试者自填（各家模型迭代很快，硬编码会过期），只做字符校验；
  - 密钥仍由测试者自带，随请求头传入，不落盘。

新增一家服务商 = 在 PROVIDERS 里加一条，前端下拉自动出现，后端无需改动。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    default_model: str
    models: tuple[str, ...]
    note: str = ""

    def to_public(self) -> dict:
        """给前端的视图：不含任何密钥，只给地址与模型清单。"""
        return {
            "id": self.id,
            "label": self.label,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "models": list(self.models),
            "note": self.note,
        }


# 顺序即前端下拉顺序：国内可达的排前面（演示环境在国内）。
PROVIDERS: dict[str, Provider] = {
    "deepseek": Provider(
        id="deepseek",
        label="DeepSeek（深度求索）",
        base_url="https://api.deepseek.com",
        default_model="deepseek-chat",
        models=("deepseek-chat", "deepseek-reasoner"),
        note="国内直连；chat 快、reasoner 会思考更久",
    ),
    "qwen": Provider(
        id="qwen",
        label="通义千问（阿里云 DashScope）",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-plus",
        models=("qwen-plus", "qwen-turbo", "qwen-max", "qwen-long"),
        note="OpenAI 兼容模式；需要 DashScope 的 API Key",
    ),
    "moonshot": Provider(
        id="moonshot",
        label="Kimi（月之暗面）",
        base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        models=("moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k",
                "kimi-k2-0905-preview"),
        note="国内直连；长上下文版本更贵",
    ),
    "zhipu": Provider(
        id="zhipu",
        label="智谱 GLM",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-flash",
        models=("glm-4-flash", "glm-4-air", "glm-4-plus"),
        note="glm-4-flash 有免费额度，适合演示",
    ),
    "siliconflow": Provider(
        id="siliconflow",
        label="硅基流动 SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen2.5-7B-Instruct",
        models=("Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen2.5-72B-Instruct",
                "deepseek-ai/DeepSeek-V3"),
        note="国内聚合平台，模型名带斜杠（如 Qwen/Qwen2.5-7B-Instruct）",
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"),
        note="需要境外网络与 OpenAI Key",
    ),
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter（聚合）",
        base_url="https://openrouter.ai/api/v1",
        default_model="deepseek/deepseek-chat",
        models=("deepseek/deepseek-chat", "openai/gpt-4o-mini",
                "anthropic/claude-3.5-sonnet"),
        note="一个 Key 调多家模型；按量计费",
    ),
    "groq": Provider(
        id="groq",
        label="Groq（低延迟推理）",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
        note="速度极快，适合演示；需境外网络",
    ),
}

DEFAULT_PROVIDER_ID = "deepseek"


def resolve(provider_id: str | None) -> Provider | None:
    """按 id 取服务商；未知 id 返回 None（调用方须拒绝，不能猜）。"""
    if not provider_id:
        return None
    return PROVIDERS.get(provider_id.strip().lower())


def default_provider() -> Provider:
    return PROVIDERS[DEFAULT_PROVIDER_ID]


def catalog() -> list[dict]:
    return [p.to_public() for p in PROVIDERS.values()]
