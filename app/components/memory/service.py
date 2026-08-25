from dataclasses import dataclass
from typing import List
from uuid import UUID

from wireup import injectable

from app.core.config import AppConfig
from app.infrastructures.llm import ModelFactory
from app.components.memory.repositories import MemoryRepository
from app.components.memory.extraction import extract_memories

from app.models.domain.agentic import (
    AgenticMemory,
    AgenticMessageType,
    AgenticConversationMessage,
)


@injectable
@dataclass
class MemoryService:
    """记忆组件门面：remember 写入（LLM 抽取）、recall 召回。

    组件自内聚：存取策略收敛在自有 repositories（注入抽象
    MemoryRepository，当前绑定 SQLite 实现）；存储引擎复用共享 db
    基建，表模型在 app/models/domain。
    抽取走 ModelFactory 裸模型而非 AgentFactory——memory 组件不得依赖
    agents（智能体工具装配会反向依赖本组件），否则形成包级环。
    """

    memory_repo: MemoryRepository
    model_factory: ModelFactory
    app_config: AppConfig

    def remember(self, query: str, turn_messages: List[AgenticConversationMessage], thread_id: UUID, turn_id: UUID) -> List[AgenticMemory]:
        """轮次结束后写入记忆：拼 transcript -> 裸模型抽取（参考已有记忆去重）-> 入库。"""
        if not self.app_config.memory.enabled:
            return []

        transcript = self._build_transcript(query, turn_messages)
        if transcript == "":
            return []

        model = self.model_factory.create(self.app_config.memory.extraction_provider)
        existing = [memory.content for memory in self.memory_repo.recall_memories()]
        contents = extract_memories(model=model, existing=existing, transcript=transcript)
        if len(contents) == 0:
            return []

        memories = [
            AgenticMemory(content=content, source_thread_id=thread_id, source_turn_id=turn_id)
            for content in contents
        ]
        return self.memory_repo.store_memories(memories)

    def recall(self) -> List[AgenticMemory]:
        """v1 全量召回：记忆条目经 LLM 抽取、数量有限，直接全量返回。"""
        return self.memory_repo.recall_memories()

    def _build_transcript(self, query: str, turn_messages: List[AgenticConversationMessage]) -> str:
        # 与标题生成同款拼接：用户 query + 助手 MESSAGE 文本
        assistant_text = "\n".join(
            part.get("text", "")
            for msg in turn_messages
            if msg.message_type == AgenticMessageType.MESSAGE
            for part in msg.content
            if part.get("type") == "text"
        )
        return f"用户：{query}\n助手：{assistant_text}".strip()
