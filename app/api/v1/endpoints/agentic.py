from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from wireup import Injected

from app.services import AgenticService
from app.exceptions import ConversationNotFoundError

from app.models.schema.request.pagination import PaginationRequest
from app.models.schema.request.chat import ChatRequest
from app.models.schema.request.summary import SummaryRequest
from app.models.schema.response.biz_response import Response

router = APIRouter(prefix="/agentic", tags=["Agentic"])

@router.get("/conversation")
def list_conversation(
    agentic_service: Injected[AgenticService],
    pagination: Annotated[PaginationRequest, Depends()],
):
    conversations = agentic_service.list_conversations(page=pagination.page, page_size=pagination.pageSize)
    # to_dict() 剔除 None 调试字段，保证成功响应体稳定（直接返回 dataclass 会带 null 字段）
    return Response.success(conversations).to_dict()

@router.get("/conversation/{thread_id}")
def get_conversation(
    agentic_service: Injected[AgenticService],
    thread_id: UUID,
):
    # describe 只读：不存在则 404（不再隐式创建空会话）
    conversation = agentic_service.describe_conversation(thread_id=thread_id)
    if conversation is None:
        raise ConversationNotFoundError("conversation not found")
    return Response.success(conversation).to_dict()

@router.get("/conversation/{thread_id}/history")
def list_conversation_history_messages(
    agentic_service: Injected[AgenticService],
    thread_id: UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    messages = agentic_service.list_conversation_history_messages(thread_id=thread_id, offset=offset, limit=limit)
    return Response.success(messages).to_dict()

@router.delete("/conversation/{thread_id}")
def delete_conversation(
    agentic_service: Injected[AgenticService],
    thread_id: UUID,
):
    # 硬删除：会话+全部轮次+消息单事务清掉（返回删除前的会话快照）；
    # 不存在则 404，与 describe 同语义。长期记忆不随会话删除。
    conversation = agentic_service.delete_conversation(thread_id=thread_id)
    if conversation is None:
        raise ConversationNotFoundError("conversation not found")
    return Response.success(conversation).to_dict()

@router.post("/chat")
def chat(
    agentic_service: Injected[AgenticService],
    request: ChatRequest,
):
    # agentic_service.chat() 已输出 SSE 帧（"data: {...}\n\n"），endpoint 纯透传。
    # 多轮历史以服务端（库）为准：请求只取末条用户消息作为当前提问，payload 历史不回放。
    events = agentic_service.chat(request.threadId, request.runId, request.messages[-1].content)
    return StreamingResponse(events, media_type="text/event-stream")
