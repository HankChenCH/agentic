"""从 LangChain 模型对象提取用量事实的纯函数助手。

零 langchain import（duck-typing）——domain 层只依赖对象形状，不依赖
框架类型；键名归一覆盖 LangChain ``usage_metadata``（input_tokens 族）与
OpenAI 兼容 ``token_usage``（prompt_tokens 族）两种键族。
"""

from typing import Any


def llm_model_name(model: Any) -> str:
    """读模型实例的模型名（ChatOpenAI/ChatDeepSeek 是 model_name，ChatOllama 是
    model），读不到返回 "unknown"——统计维度缺名字不缺行。"""
    for attr in ("model_name", "model"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            return value
    return "unknown"


def normalize_usage(raw: Any) -> dict:
    """两种用量键族归一为 ``{prompt_tokens, completion_tokens, total_tokens}``。

    total 缺失时由输入+输出补齐；空入参/全零返回空 dict（调用方按无用量
    处理，不产生零值流水行）。
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    prompt = int(raw.get("prompt_tokens") or raw.get("input_tokens") or 0)
    completion = int(raw.get("completion_tokens") or raw.get("output_tokens") or 0)
    total = int(raw.get("total_tokens") or 0) or (prompt + completion)
    if prompt <= 0 and completion <= 0 and total <= 0:
        return {}
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}
