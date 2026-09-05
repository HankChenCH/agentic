"""内部裸模型网关（``ChatModelGateway`` 的实现）：按 llm 配置创建模型。

领域侧（标题生成等一次性内部任务）只依赖 ``domain/conversation/ports``
的协议面；本适配器持有 ``ModelFactory``，每次 ``create`` 按配置的 default
entry 新建模型客户端（与原领域内直用 ModelFactory 的语义一致）。将来要为
特定用例单独配便宜模型时，在此按用途路由（参照 memory.extraction_provider
的先例），领域零改动。
"""

from dataclasses import dataclass

from wireup import injectable

from app.adapters.llm import ModelFactory
from app.domain.conversation.ports import ChatModel, ChatModelGateway


@injectable(as_type=ChatModelGateway)
@dataclass
class DefaultChatModelGateway:
    model_factory: ModelFactory

    def create(self) -> ChatModel:
        return self.model_factory.create()
