"""内部裸模型网关（``ChatModelGateway`` 的实现）：按 llm 配置创建模型。

领域与组件（标题生成/记忆抽取与裁决等一次性内部任务）只依赖
``domain/ports/llm.py`` 的协议面；本适配器持有 ``ModelFactory``，每次
``create`` 按入参 entry 新建模型客户端——缺省 default entry，指名即按
用途路由（``memory.extraction_provider`` 先例），调用方零改动。
"""

from dataclasses import dataclass

from wireup import injectable

from app.adapters.llm import ModelFactory
from app.domain.ports.llm import ChatModel, ChatModelGateway


@injectable(as_type=ChatModelGateway)
@dataclass
class DefaultChatModelGateway:
    model_factory: ModelFactory

    def create(self, provider: str | None = None) -> ChatModel:
        return self.model_factory.create(provider)
