import json
from typing import List

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

_EXTRACTION_SYSTEM_PROMPT = """你是一个记忆抽取器，负责从一段对话轮次中提取值得跨轮记住的长期记忆。

规则：
1. 只抽取稳定、跨轮有价值的信息：用户姓名/身份、职业、所在地、偏好、目标、约束等；
2. 忽略寒暄、一次性上下文、与用户无关的内容、以及仅本轮有效的临时信息；
3. 已有记忆列表中语义相同的信息不要重复抽取；若本轮信息使某条已有记忆过时，抽取新条目（无需标注替换关系）；
4. 每条记忆用一句简洁的中文陈述句表达，主语明确（如"用户在上海做后端开发"）；
5. 只输出一个 JSON 字符串数组作为全部新增记忆，无新增时输出 []，不要输出任何其他文字。

示例输出：
["用户叫小明，在上海做后端开发", "用户偏好使用 Python"]"""


def extract_memories(model: BaseChatModel, existing: List[str], transcript: str) -> List[str]:
    """用裸模型从本轮 transcript 抽取新增记忆条目；解析失败降级为空列表。"""
    existing_block = "\n".join(f"- {content}" for content in existing) or "（暂无）"
    response = model.invoke([
        SystemMessage(content=_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=f"已有记忆列表：\n{existing_block}\n\n本轮对话：\n{transcript}"),
    ])
    content = response.content if isinstance(response.content, str) else str(response.content)
    return _parse_json_array(content)


def _parse_json_array(content: str) -> List[str]:
    # 模型偶尔用 ```json 包裹或附带说明文字，截取首个 [ 到末个 ] 再解析
    start, end = content.find("["), content.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []

    try:
        data = json.loads(content[start:end + 1])
    except json.JSONDecodeError:
        return []

    return [item.strip() for item in data if isinstance(item, str) and item.strip()]
