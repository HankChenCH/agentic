from dataclasses import dataclass

from wireup import injectable
from langchain.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from app.infrastructures.llm import ModelFactory
from app.models.domain.agentic import AgenticConversationMessage, AgenticMessageType

_TITLE_SYSTEM_PROMPT = """你是一个会话标题生成器。根据用户提供的对话内容，生成一个简洁准确的中文标题。

要求：
- 不超过 15 个字，概括对话的核心主题；
- 直接输出标题文本，不要引号、序号、标点前后缀或任何解释；
- 优先从用户的提问提炼，不要编造对话中不存在的内容。
"""


def generate_title(model: BaseChatModel, query: str, turn_messages: list[AgenticConversationMessage]) -> str:
    """用裸模型把本轮对话浓缩为一句话会话标题；输出收敛为单行纯文本。

    走 ModelFactory 裸模型而非智能体层——一次性内部任务无需人设/工具循环/
    注册席位（与 components/memory 的抽取同款做法）。
    """
    assistant_text = "\n".join(
        part.get("text", "")
        for msg in turn_messages
        if msg.message_type == AgenticMessageType.MESSAGE
        for part in msg.content
        if part.get("type") == "text"
    )
    transcript = f"用户：{query}\n助手：{assistant_text}".strip()

    response = model.invoke([
        SystemMessage(content=_TITLE_SYSTEM_PROMPT),
        HumanMessage(content=transcript),
    ])
    content = response.content if isinstance(response.content, str) else str(response.content)
    return _normalize(content)


def _normalize(title: str) -> str:
    # 模型偶尔输出引号包裹、多行或纯空白：取首个非空行、去引号、截断；
    # 无可用内容时返回 ""（空结果由调用方按生成失败处理）
    first = next((line.strip() for line in title.strip().splitlines() if line.strip()), "")
    return first.strip('"“”‘’「」《》').strip()[:50]


@injectable
@dataclass
class ConversationTitleGenerator:
    """会话标题生成器门面：模型每次调用时经 ModelFactory 创建。

    不传 entry key 即路由到 llm.default（与原内置标题智能体无 preferred_provider
    时一致）；将来要单独配便宜模型时加一个显式 key 即可（参照 memory.extraction_provider）。
    """

    model_factory: ModelFactory

    def generate(self, query: str, turn_messages: list[AgenticConversationMessage]) -> str:
        return generate_title(self.model_factory.create(), query, turn_messages)
