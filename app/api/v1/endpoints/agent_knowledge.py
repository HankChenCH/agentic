"""agent 知识库绑定端点：只做 HTTP wiring，业务规则全部在服务层。

顶级领域前缀 ``/agent/{agent_id}/knowledge``：与 ``/knowledge/{kb_id}`` 的
UUID 路径解耦（避免路由竞争），也为未来 agent 管理端点留位。
"""

from fastapi import APIRouter
from wireup import Injected

from app.models.schema.request.knowledge import KnowledgeBindingsUpdateRequest
from app.models.schema.response.biz_response import Response
from app.services import KnowledgeBindingService

router = APIRouter(prefix="/agent/{agent_id}/knowledge", tags=["AgentKnowledge"])


@router.get("")
def list_agent_knowledge(
    binding_service: Injected[KnowledgeBindingService],
    agent_id: str,
):
    return Response.success(binding_service.list_bindings(agent_id)).to_dict()


@router.put("")
def replace_agent_knowledge(
    binding_service: Injected[KnowledgeBindingService],
    agent_id: str,
    request: KnowledgeBindingsUpdateRequest,
):
    # 全量替换语义：kbIds 即最终绑定集合（空列表=清空）
    return Response.success(binding_service.replace_bindings(agent_id, request.kbIds)).to_dict()
