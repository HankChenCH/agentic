from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from wireup import Injected

from app.services import ChatOrchestrator, ConversationService

from app.models.schema.request.pagination import PaginationRequest
from app.models.schema.request.chat import ChatRequest
from app.models.schema.response.biz_response import Response

router = APIRouter(prefix="/agentic", tags=["Agentic"])

# 受众分流：conversation 增删查是管理侧读路径，直接消费领域服务；
# chat 是用户侧行程，经编排层。存在性校验在领域服务内抛业务异常，
# 由全局处理器映射为 404 信封——端点不做 None 判断。

@router.get("/conversation")
def list_conversation(
    conversations: Injected[ConversationService],
    pagination: Annotated[PaginationRequest, Depends()],
):
    envelope = conversations.list_conversations(page=pagination.page, page_size=pagination.pageSize)
    # to_dict() 剔除 None 调试字段，保证成功响应体稳定（直接返回 dataclass 会带 null 字段）
    return Response.success(envelope).to_dict()

@router.get("/conversation/{thread_id}")
def get_conversation(
    conversations: Injected[ConversationService],
    thread_id: UUID,
):
    return Response.success(conversations.describe_conversation(thread_id=thread_id)).to_dict()

@router.get("/conversation/{thread_id}/history")
def list_conversation_history_messages(
    conversations: Injected[ConversationService],
    thread_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=0, le=100),
):
    messages = conversations.list_history_messages(thread_id=thread_id, offset=offset, limit=limit)
    return Response.success(messages).to_dict()

@router.delete("/conversation/{thread_id}")
def delete_conversation(
    conversations: Injected[ConversationService],
    thread_id: UUID,
):
    return Response.success(conversations.delete_conversation(thread_id=thread_id)).to_dict()

@router.post("/chat")
def chat(
    orchestrator: Injected[ChatOrchestrator],
    request: ChatRequest,
):
    # orchestrator.chat() 已输出 SSE 帧（"data: {...}\n\n"），endpoint 纯透传。
    # 多轮历史以服务端（库）为准：请求只取末条用户消息作为当前提问，payload 历史不回放。
    events = orchestrator.chat(request.threadId, request.runId, request.messages[-1].content)
    return StreamingResponse(events, media_type="text/event-stream")
