"""跨聚合 LLM 契约（依赖倒置）：一次性内部任务的裸模型获取协议。

实现住 ``app/adapters/llm/gateway.py``（``DefaultChatModelGateway``，wireup
``as_type=ChatModelGateway`` 回填）——领域与组件不感知供应商与工厂细节。
消费方：conversation 的标题生成（default entry）、memory 的结构化抽取/
实体消歧裁决/陈述裁决（``memory.extraction_provider`` 指名 entry）。将来要
为特定用例单独配便宜模型时，在实现侧按用途路由即可，领域零改动。

langchain-core 引用属领域侧框架白名单（消息信封抽象，非供应商 SDK）；
``with_structured_output`` 返回的 Runnable 不进协议注解——调用方以
duck-typing 链式使用。
"""

from typing import Any, Protocol, Sequence

from langchain_core.messages import BaseMessage


class ChatModel(Protocol):
    """裸聊天模型的最小调用面（一次性内部任务：标题生成/抽取/裁决）。

    结构匹配 langchain 的 ``BaseChatModel``——实现侧无需继承任何基类，
    测试替身同理；``invoke`` 之外唯一承诺 ``with_structured_output``
    （结构化抽取与语义裁决的全部用法，不承诺流式/绑定等完整模型协议面）。
    """

    def invoke(self, messages: Sequence[BaseMessage]) -> BaseMessage: ...

    def with_structured_output(self, schema: Any, **kwargs) -> Any:
        """结构化输出链：pydantic schema → 解析后的 Runnable。"""


class ChatModelGateway(Protocol):
    """内部裸模型的获取网关：按 llm 配置的 entry 每次创建。

    ``provider`` 缺省取 default entry（标题生成先例）；指名 entry 即按
    用途路由（``memory.extraction_provider`` 先例）。模型是轻量包装，
    供应商客户端（gRPC/HTTP 连接池）由工厂缓存承担。
    """

    def create(self, provider: str | None = None) -> ChatModel: ...
