"""应用层（用例层）：用户侧行程与入口侧用例的唯一编排地。

依赖方向：可调 domain / components / agents；禁止直触 adapters（持久化与
机制一律经领域服务/端口）、api/tasks 与向上反向调用。入口侧（api/tasks/
commands）只消费本层门面：

- 行程面：``AgenticService``（run SSE 编排 + 显式取消 + 优雅关闭）、
  ``TurnFinalizer``（轮次收尾：标题/记忆/用户节点）、``translator/``
  （LangChain → ag-ui 唯一映射 + 落库翻译）；
- 目录面：``AgentCatalogService`` / ``ToolCatalogService``（api 禁触
  components/agents 的中转）；
- 管理面（一域一门面 ``*AppService``）：auth / conversation（含附件）/
  knowledge（含摄取派发）/ memory（含维护编排）/ usage / human_agent /
  ingestion（tasks 消费）。
"""

from .agent_catalog import AgentCatalogService
from .agentic_service import AgenticService
from .auth_app_service import AuthAppService, verify_access_token
from .conversation_app_service import ATTACHMENT_URL_PREFIX, ConversationAppService
from .human_agent_app_service import HumanAgentAppService
from .ingestion_app_service import IngestionAppService
from .knowledge_app_service import KnowledgeAppService
from .memory_app_service import MemoryAppService
from .tool_catalog import ToolCatalogService
from .turn_finalizer import TurnFinalizer
from .translator import AgUiTranslator, StorageTranslator
from .usage_app_service import UsageAppService

__all__ = [
    "ATTACHMENT_URL_PREFIX",
    "AgentCatalogService",
    "AgUiTranslator",
    "AgenticService",
    "AuthAppService",
    "ConversationAppService",
    "HumanAgentAppService",
    "IngestionAppService",
    "KnowledgeAppService",
    "MemoryAppService",
    "StorageTranslator",
    "ToolCatalogService",
    "TurnFinalizer",
    "UsageAppService",
    "verify_access_token",
]
