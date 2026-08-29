from typing import Annotated, AsyncIterator, Iterator
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import iterate_in_threadpool
from starlette.types import Receive, Scope, Send
from wireup import Injected

from app.services import ChatOrchestrator, ConversationService

from app.models.schema.request.pagination import PaginationRequest
from app.models.schema.request.chat import CancelRequest, ChatRequest
from app.models.schema.response.biz_response import Response

router = APIRouter(prefix="/agentic", tags=["Agentic"])


async def _stream_and_close(events: Iterator[str]) -> AsyncIterator[str]:
    """同步 SSE 生成器 → async 流，并在任何收尾路径确定性 close 底层生成器。

    starlette 的 iterate_in_threadpool 没有 finally：断连取消时 CancelledError
    从 __anext__ 抛出直接终结 async 生成器帧，底层同步生成器无人 close，只能
    等循环 GC 才收到 GeneratorExit（实测分钟级），编排层的断连收口（轮次置
    CANCELED）随之延迟、进程先退出则丢失。这里包一层 finally close——close
    会吞掉生成器重抛的 GeneratorExit，收口逻辑在帧退出前同步执行完成。
    """
    try:
        async for frame in iterate_in_threadpool(events):
            yield frame
    finally:
        events.close()


class ClosingStreamingResponse(StreamingResponse):
    """响应周期结束（正常完成或客户端断连）时确定性关闭 body 迭代器的 SSE 响应。

    Starlette 从不收尾 body 迭代器：正常结束靠迭代器自然耗尽；断连时（uvicorn
    spec_version=2.3 走 listen_for_disconnect 取消流任务；spec_version>=2.4 靠
    send() 抛 OSError）直接把迭代器抛弃。配合 _stream_and_close 使用：断连瞬间
    在 finally 里 aclose 包装器 → 触发底层同步生成器的确定性 close；迭代器已
    自然耗尽时 aclose 是 no-op。
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            aclose = getattr(self.body_iterator, "aclose", None)
            if aclose is not None:
                await aclose()

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
    return ClosingStreamingResponse(_stream_and_close(events), media_type="text/event-stream")

@router.post("/chat/cancel")
def cancel_chat(
    orchestrator: Injected[ChatOrchestrator],
    request: CancelRequest,
):
    # 显式取消通道：置 Redis 取消标志即返回（幂等），运行中的流式循环在节流
    # 边界感知后收口。不等流结束——同步生成器无法从外部打断在执行的 next()，
    # 等待时长不可控；前端配合本地 abort 获得即时取消态。
    orchestrator.cancel_run(request.threadId)
    return Response.success({"canceled": True}).to_dict()
