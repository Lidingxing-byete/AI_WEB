"""模型层（本地 Ollama + 云端占位 + 规则层兜底）。

对外只需用两个东西：

    from app.llm.service import LlmService
    svc = LlmService()
    nar = svc.narrate(symptom, candidates, unknowns, references)

职责边界（写在这里是因为它比任何注释都重要）
------------------------------------------
- 模型**只解释**已查出的结论、**只补充**用户可执行的验证建议。
- 模型**没有诊断权**：不能新增候选根因、不能改评分、不能标证据等级。
- 模型输出**恒为 `[推断]`**，且必须先过 `guard.validate` 才能离开本层。
- 模型不可用时**降级**，不影响诊断本身。

模块：
    client.py    Ollama HTTP 客户端（stdlib，绕过系统代理）
    guard.py     输出守卫（证据纪律四道闸）
    prompts.py   提示词模板（主流程 + 评测基线）
    service.py   三级降级链统一入口
"""

from .client import OllamaClient, OllamaUnavailable
from .guard import validate
from .service import LlmNarrative, LlmService, default_service

__all__ = [
    "OllamaClient",
    "OllamaUnavailable",
    "validate",
    "LlmService",
    "LlmNarrative",
    "default_service",
]
