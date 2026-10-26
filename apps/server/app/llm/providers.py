"""云端模型服务商目录（OpenAI 兼容接口）。

设计原则（主流做法）
--------------------
1. **白名单预设**：服务商由服务端决定 base_url，前端只能选 id；
   绝不接受请求方传入地址，否则本服务就成了任意 URL 的代理（SSRF）。
2. **统一协议**：全部走 OpenAI 兼容 `/chat/completions`，一个客户端适配所有家。
3. **模型可自填**：各家模型迭代很快，硬编码会过期，只做字符白名单校验。
4. **国内优先**：默认 DeepSeek；列表按"国内可直连 → 需境外网络"排序，
   并在 note 里标明，避免测试者选了打不通的服务商还以为是自己 Key 的问题。

新增一家 = 在 PROVIDERS 里加一条（前端下拉自动出现）。
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
    region: str = "cn"          # cn = 国内可直连；intl = 需境外网络

    def to_public(self) -> dict:
        """给前端的视图：不含任何密钥，只给地址与模型清单。"""
        return {
            "id": self.id,
            "label": self.label,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "models": list(self.models),
            "note": self.note,
            "region": self.region,
        }


# 顺序 = 前端下拉顺序：国内可直连的在前。
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
        note="国内直连；OpenAI 兼容模式，需 DashScope API Key",
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
        note="国内直连；glm-4-flash 有免费额度，适合演示",
    ),
    "doubao": Provider(
        id="doubao",
        label="豆包（火山方舟）",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-1-5-pro-32k-250115",
        models=("doubao-1-5-pro-32k-250115", "doubao-1-5-lite-32k-250115",
                "doubao-pro-32k"),
        note="国内直连；模型名可填方舟的 Model ID 或接入点 ID（ep-…）",
    ),
    "qianfan": Provider(
        id="qianfan",
        label="文心一言（百度千帆 v2）",
        base_url="https://qianfan.baidubce.com/v2",
        default_model="ernie-4.0-8k",
        models=("ernie-4.0-8k", "ernie-3.5-8k", "ernie-speed-8k"),
        note="国内直连；用千帆的 API Key（Bearer），不是 AppID/Secret",
    ),
    "siliconflow": Provider(
        id="siliconflow",
        label="硅基流动 SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen2.5-7B-Instruct",
        models=("Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen2.5-72B-Instruct",
                "deepseek-ai/DeepSeek-V3"),
        note="国内直连；聚合平台，模型名带斜杠（如 Qwen/Qwen2.5-7B-Instruct）",
    ),
    "openai": Provider(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"),
        note="需境外网络与 OpenAI Key；国内直连通常不通",
        region="intl",
    ),
    "openrouter": Provider(
        id="openrouter",
        label="OpenRouter（聚合）",
        base_url="https://openrouter.ai/api/v1",
        default_model="deepseek/deepseek-chat",
        models=("deepseek/deepseek-chat", "openai/gpt-4o-mini",
                "anthropic/claude-3.5-sonnet"),
        note="需境外网络；一个 Key 调多家模型，按量计费",
        region="intl",
    ),
    "groq": Provider(
        id="groq",
        label="Groq（低延迟推理）",
        base_url="https://api.groq.com/openai/v1",
        default_model="llama-3.3-70b-versatile",
        models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
        note="需境外网络；速度极快，适合演示",
        region="intl",
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
